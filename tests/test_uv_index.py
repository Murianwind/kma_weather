"""
자외선지수(UV) 기능 테스트

- 지역코드 매칭(find_uv_area) — 꽃가루와 admin_area_map.json을 공유
- 발표시각 계산(_calc_uv_base_time) — 3시간 단위(00,03,...,21)
- 등급 계산(_get_uv_grade) — 낮음/보통/높음/매우높음/위험
- API 조회(_get_uv_index) — 정상/미신청/데이터없음/예외
- 센서 노출(SENSOR_TYPES, SENSOR_API_GROUPS, native_value, extra_state_attributes, available)
"""
import json
import pathlib
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, AsyncMock

import pytest

from custom_components.kma_weather.api_kma import KMAWeatherAPI
from custom_components.kma_weather.const import API_SERVICES as _API_SERVICES

_TZ = ZoneInfo("Asia/Seoul")
_UV_MAP_PATH = pathlib.Path(__file__).parent.parent / "custom_components" / "kma_weather" / "admin_area_map.json"


# ─────────────────────────────────────────────────────────────────────────
# 1. 지역코드 파일 자체 검증
# ─────────────────────────────────────────────────────────────────────────

class TestUvAreaMapFile:
    def test_file_exists_and_loads(self):
        assert _UV_MAP_PATH.exists()
        data = json.loads(_UV_MAP_PATH.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) > 3000

    def test_entries_have_required_fields(self):
        data = json.loads(_UV_MAP_PATH.read_text(encoding="utf-8"))
        sample = data[0]
        assert set(sample.keys()) == {"c", "n", "la", "lo"}
        assert isinstance(sample["c"], str)
        assert isinstance(sample["n"], str)
        assert isinstance(sample["la"], float)
        assert isinstance(sample["lo"], float)

    def test_no_duplicate_codes(self):
        data = json.loads(_UV_MAP_PATH.read_text(encoding="utf-8"))
        codes = [r["c"] for r in data]
        assert len(codes) == len(set(codes))

    def test_known_code_present(self):
        """실제 자외선지수 API 지역코드표(2026-07-01)에서 확인한 값."""
        data = json.loads(_UV_MAP_PATH.read_text(encoding="utf-8"))
        by_code = {r["c"]: r for r in data}
        assert "1111051500" in by_code
        assert by_code["1111051500"]["n"] == "서울특별시 종로구 청운효자동"

    def test_recent_administrative_reorganization_reflected(self):
        """화성시 동탄구 신설(개편) 등 최신 행정구역이 반영돼 있어야 한다
        (꽃가루 지역코드 파일에는 이 개편 이전 코드가 남아있음)."""
        data = json.loads(_UV_MAP_PATH.read_text(encoding="utf-8"))
        names = {r["n"] for r in data}
        assert "경기도 화성시동탄구 동탄4동" in names


# ─────────────────────────────────────────────────────────────────────────
# 2. 지역 매칭(find_uv_area) — coordinator
# ─────────────────────────────────────────────────────────────────────────

class TestFindUvArea:
    def _make_coordinator(self, hass):
        from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": ""}
        entry.options = {}
        entry.entry_id = "uv_test"
        hass.config.latitude = 37.5665
        hass.config.longitude = 126.9780
        coord = KMAWeatherUpdateCoordinator(hass, entry)
        return coord

    @pytest.mark.asyncio
    async def test_find_uv_area_returns_nearest(self, hass):
        coord = self._make_coordinator(hass)
        code, name = await coord.find_uv_area(37.584137, 126.970652)
        assert code == "1111051500"
        assert name == "서울특별시 종로구 청운효자동"

    @pytest.mark.asyncio
    async def test_find_uv_area_shares_cache_with_pollen(self, hass):
        """꽃가루·자외선지수는 지역코드가 완전히 동일해서 캐시도 공유한다
        — 매 갱신마다 같은 좌표로 지역을 두 번 계산하지 않기 위함."""
        coord = self._make_coordinator(hass)
        assert coord._admin_cached_area_no is None
        pollen_code, pollen_name = await coord.find_pollen_area(37.584137, 126.970652)
        # 꽃가루 조회만으로 이미 캐시가 채워졌고, 자외선지수도 그대로 재사용한다
        assert coord._admin_cached_area_no == "1111051500"
        uv_code, uv_name = await coord.find_uv_area(37.584137, 126.970652)
        assert uv_code == pollen_code == "1111051500"
        assert uv_name == pollen_name == "서울특별시 종로구 청운효자동"

    @pytest.mark.asyncio
    async def test_find_uv_area_cache_hit_skips_recompute(self, hass):
        coord = self._make_coordinator(hass)
        await coord.find_uv_area(37.584137, 126.970652)
        coord._admin_area_data = None  # 파일이 사라져도 캐시로 응답해야 함
        code, name = await coord.find_uv_area(37.584137, 126.970652)
        assert code == "1111051500"

    @pytest.mark.asyncio
    async def test_find_uv_area_load_failure_returns_empty(self, hass):
        coord = self._make_coordinator(hass)
        coord._load_admin_area_map = MagicMock()  # 아무것도 안 채움
        code, name = await coord.find_uv_area(0.0, 0.0)
        assert code == "" and name == ""


# ─────────────────────────────────────────────────────────────────────────
# 3. 발표시각 계산(_calc_uv_base_time)
# ─────────────────────────────────────────────────────────────────────────

class TestCalcUvBaseTime:
    @pytest.mark.parametrize("hour,expected_base_hour", [
        (0, 0), (1, 0), (2, 0),
        (3, 3), (5, 3),
        (6, 6), (8, 6),
        (21, 21), (23, 21),
    ])
    def test_rounds_down_to_3hour_mark(self, hour, expected_base_hour):
        now = datetime(2026, 8, 16, hour, 30, tzinfo=_TZ)
        result = KMAWeatherAPI._calc_uv_base_time(now)
        assert result == f"20260816{expected_base_hour:02d}"

    def test_exact_mark_uses_itself(self):
        now = datetime(2026, 8, 16, 18, 0, tzinfo=_TZ)
        assert KMAWeatherAPI._calc_uv_base_time(now) == "20260816" + "18"


# ─────────────────────────────────────────────────────────────────────────
# 4. 등급 계산(_get_uv_grade)
# ─────────────────────────────────────────────────────────────────────────

class TestGetUvGrade:
    def _make_api(self):
        return KMAWeatherAPI(MagicMock(), "test_key")

    @pytest.mark.parametrize("value,expected", [
        (0, "낮음"), (2, "낮음"), (2.9, "낮음"),
        (3, "보통"), (5, "보통"), (5.9, "보통"),
        (6, "높음"), (7, "높음"),
        (8, "매우높음"), (10, "매우높음"),
        (11, "위험"), (15, "위험"),
    ])
    def test_grade_boundaries(self, value, expected):
        api = self._make_api()
        assert api._get_uv_grade(str(value)) == expected

    def test_none_or_invalid_returns_none(self):
        api = self._make_api()
        assert api._get_uv_grade(None) is None
        assert api._get_uv_grade("-") is None
        assert api._get_uv_grade("") is None
        assert api._get_uv_grade("abc") is None


# ─────────────────────────────────────────────────────────────────────────
# 5. API 조회(_get_uv_index)
# ─────────────────────────────────────────────────────────────────────────

class TestGetUvIndex:
    def _make_api(self):
        api = KMAWeatherAPI(MagicMock(), "test_key")
        api.hass = None
        return api

    def _ok_response(self, h0="5", date="2026081606"):
        return {"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                "body": {"dataType": "JSON", "items": {"item": [{
                    "code": "d", "areaNo": "1111051500", "date": date, "h0": h0,
                }]}, "pageNo": 1, "numOfRows": 1, "totalCount": 1}}}

    @pytest.mark.asyncio
    async def test_normal_response_parsed(self):
        api = self._make_api()
        api._fetch = AsyncMock(return_value=self._ok_response(h0="7"))
        now = datetime(2026, 8, 16, 6, 30, tzinfo=_TZ)
        result = await api._get_uv_index(now, "1111051500", "서울특별시 종로구 청운효자동")
        assert result["value"] == "7"
        assert result["grade"] == "높음"
        assert result["area_name"] == "서울특별시 종로구 청운효자동"
        assert "2026" in result["announcement"]

    @pytest.mark.asyncio
    async def test_hourly_forecast_attribute_built_from_h3_to_h75(self):
        api = self._make_api()
        resp = self._ok_response(h0="7", date="2026081606")
        item = resp["response"]["body"]["items"]["item"][0]
        for offset in range(3, 76, 3):
            item[f"h{offset}"] = str(offset % 12)
        api._fetch = AsyncMock(return_value=resp)
        now = datetime(2026, 8, 16, 6, 30, tzinfo=_TZ)
        result = await api._get_uv_index(now, "1111051500", "청운효자동")
        hourly = result["hourly"]
        assert len(hourly) == 25  # h3~h75, 3시간 간격
        assert hourly["08/16 09시"] == 3.0
        assert hourly["08/19 09시"] == 3.0  # 78시간 후는 3일 뒤로 날짜가 넘어감

    @pytest.mark.asyncio
    async def test_hourly_forecast_skips_missing_values(self):
        api = self._make_api()
        resp = self._ok_response(h0="7", date="2026081606")
        item = resp["response"]["body"]["items"]["item"][0]
        for offset in range(3, 76, 3):
            item[f"h{offset}"] = "-" if offset == 30 else "5"
        api._fetch = AsyncMock(return_value=resp)
        now = datetime(2026, 8, 16, 6, 30, tzinfo=_TZ)
        result = await api._get_uv_index(now, "1111051500", "청운효자동")
        assert len(result["hourly"]) == 24  # h30(결측) 하나만 빠짐
        assert "08/17 12시" not in result["hourly"]  # base(06시)+30h=08/17 12시

    @pytest.mark.asyncio
    async def test_hourly_forecast_empty_when_no_offsets_present(self):
        api = self._make_api()
        api._fetch = AsyncMock(return_value=self._ok_response(h0="7"))  # h3~h75 없음
        now = datetime(2026, 8, 16, 6, 30, tzinfo=_TZ)
        result = await api._get_uv_index(now, "1111051500", "청운효자동")
        assert result["hourly"] == {}

    @pytest.mark.asyncio
    async def test_no_area_no_returns_none(self):
        api = self._make_api()
        api._fetch = AsyncMock(return_value=self._ok_response())
        now = datetime(2026, 8, 16, 6, 30, tzinfo=_TZ)
        result = await api._get_uv_index(now, "", "")
        assert result is None
        api._fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_unsubscribed_returns_none(self):
        api = self._make_api()
        api._approved_apis.add("uv")
        api._fetch = AsyncMock(return_value={
            "response": {"header": {"resultCode": "30", "resultMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR"}}
        })
        now = datetime(2026, 8, 16, 6, 30, tzinfo=_TZ)
        result = await api._get_uv_index(now, "1111051500", "청운효자동")
        assert result is None
        assert "uv" not in api._approved_apis

    @pytest.mark.asyncio
    async def test_nodata_returns_empty_dict_not_error(self):
        """resultCode=03(NODATA)은 아직 그 시각 자료가 안 올라온 정상적인
        상황이므로, 미신청 취급하지 않고 빈 값만 반환한다."""
        api = self._make_api()
        api._fetch = AsyncMock(return_value={
            "response": {"header": {"resultCode": "03", "resultMsg": "NODATA_ERROR"}}
        })
        now = datetime(2026, 8, 16, 6, 30, tzinfo=_TZ)
        result = await api._get_uv_index(now, "1111051500", "청운효자동")
        assert result == {}

    @pytest.mark.asyncio
    async def test_5xx_http_error_returns_empty_dict(self):
        api = self._make_api()
        api._fetch = AsyncMock(return_value={"_http_error": "504"})
        now = datetime(2026, 8, 16, 6, 30, tzinfo=_TZ)
        result = await api._get_uv_index(now, "1111051500", "청운효자동")
        assert result == {}

    @pytest.mark.asyncio
    async def test_marks_approved_on_success(self):
        api = self._make_api()
        api._fetch = AsyncMock(return_value=self._ok_response())
        now = datetime(2026, 8, 16, 6, 30, tzinfo=_TZ)
        await api._get_uv_index(now, "1111051500", "청운효자동")
        assert "uv" in api._approved_apis

    @pytest.mark.asyncio
    async def test_exception_returns_empty_dict(self):
        api = self._make_api()
        api._fetch = AsyncMock(side_effect=Exception("network boom"))
        now = datetime(2026, 8, 16, 6, 30, tzinfo=_TZ)
        result = await api._get_uv_index(now, "1111051500", "청운효자동")
        assert result == {}

    @pytest.mark.asyncio
    async def test_base_time_passed_to_request(self):
        api = self._make_api()
        api._fetch = AsyncMock(return_value=self._ok_response())
        now = datetime(2026, 8, 16, 14, 45, tzinfo=_TZ)
        await api._get_uv_index(now, "1111051500", "청운효자동")
        _, kwargs_or_args = api._fetch.call_args, None
        call = api._fetch.call_args
        params = call.args[1] if len(call.args) > 1 else call.kwargs.get("params")
        assert params["time"] == "2026081612"
        assert params["areaNo"] == "1111051500"


# ─────────────────────────────────────────────────────────────────────────
# 6. API 서비스 등록(const.py)
# ─────────────────────────────────────────────────────────────────────────

class TestUvApiServiceRegistered:
    def test_uv_in_api_services(self):
        assert "uv" in _API_SERVICES
        name, url = _API_SERVICES["uv"]
        assert name
        assert "data.go.kr" in url

    def test_uv_url_distinct_from_pollen(self):
        """자외선지수(LivingWthrIdxServiceV5)는 꽃가루(HealthWthrIdxServiceV3)와
        서로 다른 공공데이터포털 등록 건이다."""
        assert _API_SERVICES["uv"][1] != _API_SERVICES["pollen"][1]


# ─────────────────────────────────────────────────────────────────────────
# 7. 센서 노출
# ─────────────────────────────────────────────────────────────────────────

class TestUvSensorExposure:
    def test_uv_value_in_sensor_types(self):
        from custom_components.kma_weather.sensor import SENSOR_TYPES
        assert "uv_value" in SENSOR_TYPES
        assert "uv_grade" in SENSOR_TYPES

    def test_uv_in_api_groups(self):
        from custom_components.kma_weather.sensor import SENSOR_API_GROUPS
        assert SENSOR_API_GROUPS.get("uv") == ["uv_value", "uv_grade"]

    def test_native_value_returns_numeric_value(self):
        from custom_components.kma_weather.sensor import KMACustomSensor
        coordinator = MagicMock()
        coordinator.data = {"weather": {}, "air": {}, "uv": {"value": "6", "grade": "높음"}}
        entry = MagicMock()
        sensor = KMACustomSensor(coordinator, "uv_value", "kma_weather", entry)
        assert sensor.native_value == 6

    def test_native_value_returns_float_when_not_whole(self):
        from custom_components.kma_weather.sensor import KMACustomSensor
        coordinator = MagicMock()
        coordinator.data = {"weather": {}, "air": {}, "uv": {"value": "6.5", "grade": "높음"}}
        entry = MagicMock()
        sensor = KMACustomSensor(coordinator, "uv_value", "kma_weather", entry)
        assert sensor.native_value == 6.5

    def test_native_value_grade_returns_string(self):
        from custom_components.kma_weather.sensor import KMACustomSensor
        coordinator = MagicMock()
        coordinator.data = {"weather": {}, "air": {}, "uv": {"value": "6", "grade": "높음"}}
        entry = MagicMock()
        sensor = KMACustomSensor(coordinator, "uv_grade", "kma_weather", entry)
        assert sensor.native_value == "높음"

    def test_native_value_none_when_unsubscribed(self):
        from custom_components.kma_weather.sensor import KMACustomSensor
        coordinator = MagicMock()
        coordinator.data = {"weather": {}, "air": {}, "uv": None}
        entry = MagicMock()
        sensor = KMACustomSensor(coordinator, "uv_value", "kma_weather", entry)
        assert sensor.native_value is None

    def test_native_value_none_when_nodata(self):
        """resultCode=03 등으로 빈 딕셔너리만 온 경우 값은 unknown."""
        from custom_components.kma_weather.sensor import KMACustomSensor
        coordinator = MagicMock()
        coordinator.data = {"weather": {}, "air": {}, "uv": {}}
        entry = MagicMock()
        sensor = KMACustomSensor(coordinator, "uv_value", "kma_weather", entry)
        assert sensor.native_value is None

    def test_extra_state_attributes_includes_announcement_and_area(self):
        from custom_components.kma_weather.sensor import KMACustomSensor
        coordinator = MagicMock()
        coordinator.data = {
            "weather": {}, "air": {},
            "uv": {"value": "6", "grade": "높음",
                   "announcement": "2026년 08월 16일 06시 발표",
                   "area_name": "서울특별시 종로구 청운효자동"},
        }
        entry = MagicMock()
        sensor = KMACustomSensor(coordinator, "uv_value", "kma_weather", entry)
        attrs = sensor.extra_state_attributes
        assert attrs["발표 시각"] == "2026년 08월 16일 06시 발표"
        assert attrs["지역"] == "서울특별시 종로구 청운효자동"

    def test_extra_state_attributes_includes_hourly_forecast(self):
        from custom_components.kma_weather.sensor import KMACustomSensor
        coordinator = MagicMock()
        coordinator.data = {
            "weather": {}, "air": {},
            "uv": {"value": "6", "grade": "높음",
                   "announcement": "2026년 08월 16일 06시 발표",
                   "area_name": "청운효자동",
                   "hourly": {"08/16 09시": 3.0, "08/16 12시": 6.0}},
        }
        entry = MagicMock()
        sensor = KMACustomSensor(coordinator, "uv_value", "kma_weather", entry)
        attrs = sensor.extra_state_attributes
        assert attrs["08/16 09시"] == 3.0
        assert attrs["08/16 12시"] == 6.0
        # 강수량 센서처럼 hourly 값들이 별도 키(중첩)가 아니라 그대로 펼쳐져 있어야 함
        assert "hourly" not in attrs

    def test_extra_state_attributes_grade_sensor_shows_grade_labels_not_numbers(self):
        """uv_grade 센서의 시간대별 속성은 숫자가 아니라 등급(낮음/보통/...)이어야 한다."""
        from custom_components.kma_weather.sensor import KMACustomSensor
        coordinator = MagicMock()
        coordinator.data = {
            "weather": {}, "air": {},
            "uv": {"value": "6", "grade": "높음",
                   "announcement": "2026년 08월 16일 06시 발표",
                   "area_name": "청운효자동",
                   "hourly": {"08/16 09시": 1.0, "08/16 12시": 6.0, "08/16 15시": 12.0}},
        }
        entry = MagicMock()
        sensor = KMACustomSensor(coordinator, "uv_grade", "kma_weather", entry)
        attrs = sensor.extra_state_attributes
        assert attrs["08/16 09시"] == "낮음"
        assert attrs["08/16 12시"] == "높음"
        assert attrs["08/16 15시"] == "위험"

    def test_extra_state_attributes_value_sensor_still_shows_numbers(self):
        """uv_value 센서는 (등급 변환 로직 추가 이후에도) 여전히 숫자 그대로여야 한다."""
        from custom_components.kma_weather.sensor import KMACustomSensor
        coordinator = MagicMock()
        coordinator.data = {
            "weather": {}, "air": {},
            "uv": {"value": "6", "grade": "높음",
                   "hourly": {"08/16 09시": 1.0, "08/16 12시": 6.0}},
        }
        entry = MagicMock()
        sensor = KMACustomSensor(coordinator, "uv_value", "kma_weather", entry)
        attrs = sensor.extra_state_attributes
        assert attrs["08/16 09시"] == 1.0
        assert attrs["08/16 12시"] == 6.0

    def test_extra_state_attributes_none_when_no_uv_data(self):
        from custom_components.kma_weather.sensor import KMACustomSensor
        coordinator = MagicMock()
        coordinator.data = {"weather": {}, "air": {}, "uv": None}
        entry = MagicMock()
        sensor = KMACustomSensor(coordinator, "uv_value", "kma_weather", entry)
        assert sensor.extra_state_attributes is None

    def test_available_true_when_approved(self):
        from custom_components.kma_weather.sensor import KMACustomSensor
        coordinator = MagicMock()
        coordinator.data = {"weather": {}, "air": {}, "uv": {"value": "6", "grade": "높음"}}
        coordinator.api._approved_apis = {"uv"}
        coordinator.last_update_success = True
        entry = MagicMock()
        sensor = KMACustomSensor(coordinator, "uv_value", "kma_weather", entry)
        assert sensor.available is True

    def test_available_false_when_not_approved(self):
        from custom_components.kma_weather.sensor import KMACustomSensor
        coordinator = MagicMock()
        coordinator.data = {"weather": {}, "air": {}, "uv": None}
        coordinator.api._approved_apis = set()
        coordinator.last_update_success = True
        entry = MagicMock()
        sensor = KMACustomSensor(coordinator, "uv_value", "kma_weather", entry)
        assert sensor.available is False
