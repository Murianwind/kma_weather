"""
리팩터링 및 신규 기능 테스트.

변경 사항:
  - coordinator.py: _resolve_area_codes()로 모든 구역코드 통합 결정 (2km 캐시)
  - api_kma.py: KMAWeatherAPI(session, api_key, hass) — reg_id 파라미터 제거
                fetch_data()에 reg_id_temp, reg_id_land, warn_area_code 인자 추가
                _check_unsubscribed()로 API 미신청 감지 및 HA 알림
  - sensor.py: "warning" 센서 추가
"""
import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch, call

from custom_components.kma_weather.api_kma import (
    KMAWeatherAPI,
    _WARN_TYPE_MAP,
    _API_SERVICES,
    _UNSUBSCRIBED_CODES,
)
from custom_components.kma_weather.coordinator import (
    KMAWeatherUpdateCoordinator,
    _TEMP_ID_COORDS,
    _LAND_CODE_MAP,
    _EXCLUDE_FROM_NEAREST,
    _WARN_AREA,
    _calc_reg_ids,
    _calc_warn_area_code,
    _haversine,
    _is_valid_korean_coord,
)
from custom_components.kma_weather.sensor import SENSOR_TYPES

TZ = ZoneInfo("Asia/Seoul")


# ─────────────────────────────────────────────────────────────────────────────
# 1. coordinator._resolve_area_codes: 구역코드 통합 결정 및 2km 캐시
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveAreaCodes:
    def _make_coordinator(self, hass):
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": ""}
        entry.options = {}
        entry.entry_id = "test"
        return KMAWeatherUpdateCoordinator(hass, entry)

    @pytest.mark.asyncio
    async def test_returns_all_five_values(self, hass):
        """_resolve_area_codes가 (nx, ny, reg_temp, reg_land, warn_code) 5개를 반환함"""
        coord = self._make_coordinator(hass)
        result = coord._resolve_area_codes(37.608025, 127.094222)
        assert len(result) == 5
        nx, ny, reg_temp, reg_land, warn_code = result
        assert isinstance(nx, int)
        assert isinstance(ny, int)
        assert reg_temp is not None
        assert reg_land is not None
        assert warn_code is not None

    @pytest.mark.asyncio
    async def test_seoul_codes_correct(self, hass):
        """서울 중랑구 좌표의 구역코드가 올바르게 결정됨"""
        coord = self._make_coordinator(hass)
        nx, ny, reg_temp, reg_land, warn_code = coord._resolve_area_codes(37.608025, 127.094222)
        assert reg_temp == "11B20501"
        assert reg_land == "11B00000"
        assert warn_code == "L1100200"

    @pytest.mark.asyncio
    async def test_cache_used_within_2km(self, hass):
        """2km 이내 이동 시 구역코드 캐시를 재사용함"""
        coord = self._make_coordinator(hass)
        result1 = coord._resolve_area_codes(37.608025, 127.094222)
        # 1km 이동
        result2 = coord._resolve_area_codes(37.615000, 127.090000)
        # 캐시 재사용이므로 동일한 값
        assert result1 == result2
        # 캐시 좌표는 최초 좌표 유지
        assert coord._cached_area_lat == pytest.approx(37.608025)

    @pytest.mark.asyncio
    async def test_cache_invalidated_over_2km(self, hass):
        """2km 초과 이동 시 구역코드를 재계산함"""
        coord = self._make_coordinator(hass)
        nx1, ny1, reg1, land1, warn1 = coord._resolve_area_codes(37.608025, 127.094222)  # 중랑구
        nx2, ny2, reg2, land2, warn2 = coord._resolve_area_codes(37.498000, 127.027000)  # 강남
        assert warn1 != warn2  # 특보구역이 달라짐
        assert coord._cached_area_lat == pytest.approx(37.498000)

    @pytest.mark.asyncio
    async def test_cache_none_on_first_call(self, hass):
        """첫 호출 전에는 캐시가 None임"""
        coord = self._make_coordinator(hass)
        assert coord._cached_area_lat is None
        assert coord._cached_warn_area_code is None


# ─────────────────────────────────────────────────────────────────────────────
# 2. api_kma.KMAWeatherAPI 시그니처 변경 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestApiSignature:
    def test_init_no_reg_id_params(self):
        """KMAWeatherAPI 생성자에 reg_id 파라미터가 없음"""
        import inspect
        sig = inspect.signature(KMAWeatherAPI.__init__)
        params = list(sig.parameters.keys())
        assert "reg_id_temp" not in params
        assert "reg_id_land" not in params

    def test_fetch_data_receives_reg_ids(self):
        """fetch_data()가 reg_id_temp, reg_id_land, warn_area_code를 인자로 받음"""
        import inspect
        sig = inspect.signature(KMAWeatherAPI.fetch_data)
        params = list(sig.parameters.keys())
        assert "reg_id_temp" in params
        assert "reg_id_land" in params
        assert "warn_area_code" in params

    def test_get_mid_term_receives_reg_ids(self):
        """_get_mid_term()이 reg_id_temp, reg_id_land를 인자로 받음"""
        import inspect
        sig = inspect.signature(KMAWeatherAPI._get_mid_term)
        params = list(sig.parameters.keys())
        assert "reg_id_temp" in params
        assert "reg_id_land" in params

    def test_get_warning_receives_area_code(self):
        """_get_warning()이 warn_area_code를 인자로 받음"""
        import inspect
        sig = inspect.signature(KMAWeatherAPI._get_warning)
        params = list(sig.parameters.keys())
        assert "warn_area_code" in params

    def test_get_air_quality_receives_lat_lon(self):
        """_get_air_quality()가 lat, lon을 인자로 받음"""
        import inspect
        sig = inspect.signature(KMAWeatherAPI._get_air_quality)
        params = list(sig.parameters.keys())
        assert "lat" in params
        assert "lon" in params


# ─────────────────────────────────────────────────────────────────────────────
# 3. API 미신청 감지 (_check_unsubscribed)
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckUnsubscribed:
    def _make_api(self, hass=None):
        return KMAWeatherAPI(MagicMock(), "key", hass=hass)

    def test_returns_false_for_normal_code(self):
        """resultCode=00(정상)이면 False를 반환함"""
        api = self._make_api()
        assert api._check_unsubscribed("short", "00") is False

    @pytest.mark.parametrize("code", ["20", "22", "30", "31", "33"])
    def test_returns_true_for_unsubscribed_codes(self, code):
        """미신청/접근거부 코드에서 True를 반환함"""
        api = self._make_api()
        assert api._check_unsubscribed("short", code) is True

    def test_no_duplicate_notification(self):
        """같은 서비스에 대해 중복 알림을 발송하지 않음"""
        api = self._make_api()
        result1 = api._check_unsubscribed("short", "33")
        result2 = api._check_unsubscribed("short", "33")
        assert result1 is True
        assert result2 is True  # 두 번째도 True이지만 알림은 한 번만
        assert len(api._notified_unsubscribed) == 1

    def test_different_services_notified_independently(self):
        """서로 다른 서비스는 독립적으로 알림이 발송됨"""
        api = self._make_api()
        api._check_unsubscribed("short", "33")
        api._check_unsubscribed("mid", "33")
        api._check_unsubscribed("air", "33")
        assert len(api._notified_unsubscribed) == 3

    def test_persistent_notification_sent_with_hass(self):
        """hass가 있을 때 persistent_notification이 발송됨"""
        mock_hass = MagicMock()
        api = self._make_api(hass=mock_hass)
        api._check_unsubscribed("short", "33")
        mock_hass.components.persistent_notification.async_create.assert_called_once()

    def test_notification_contains_service_name_and_url(self):
        """알림 메시지에 서비스 이름과 URL이 포함됨"""
        mock_hass = MagicMock()
        api = self._make_api(hass=mock_hass)
        api._check_unsubscribed("short", "33")
        call_args = mock_hass.components.persistent_notification.async_create.call_args
        msg = call_args.kwargs.get("message", "")
        assert "기상청 단기예보" in msg
        assert "15084084" in msg  # URL 일부

    def test_notification_id_includes_service_key(self):
        """알림의 notification_id에 서비스 키가 포함됨"""
        mock_hass = MagicMock()
        api = self._make_api(hass=mock_hass)
        api._check_unsubscribed("warning", "33")
        call_args = mock_hass.components.persistent_notification.async_create.call_args
        nid = call_args.kwargs.get("notification_id", "")
        assert "warning" in nid

    def test_no_crash_without_hass(self):
        """hass 없이도 예외 없이 동작함"""
        api = self._make_api(hass=None)
        result = api._check_unsubscribed("short", "33")
        assert result is True

    def test_all_services_defined_in_api_services(self):
        """5개 API 서비스가 모두 _API_SERVICES에 정의되어 있음"""
        expected_keys = {"short", "mid", "air", "station", "warning"}
        assert set(_API_SERVICES.keys()) == expected_keys

    def test_all_services_have_url(self):
        """모든 서비스의 URL이 data.go.kr 도메인임"""
        for key, (name, url) in _API_SERVICES.items():
            assert "data.go.kr" in url, f"{key} 서비스 URL이 data.go.kr이 아님"
            assert name, f"{key} 서비스 이름이 비어있음"


# ─────────────────────────────────────────────────────────────────────────────
# 4. API 미신청 시 각 메서드 동작 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestUnsubscribedApiHandling:
    def _make_api(self):
        return KMAWeatherAPI(MagicMock(), "key", hass=MagicMock())

    def _unsubscribed_response(self, code="33"):
        return {
            "response": {
                "header": {"resultCode": code, "resultMsg": "서비스 신청하지 않음"},
                "body": {},
            }
        }

    @pytest.mark.asyncio
    async def test_short_term_returns_none_on_unsubscribed(self):
        """단기예보 미신청 시 None을 반환함"""
        api = self._make_api()
        api._fetch = AsyncMock(return_value=self._unsubscribed_response())
        api.nx, api.ny = 60, 127
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        result = await api._get_short_term(now)
        assert result is None

    @pytest.mark.asyncio
    async def test_mid_term_returns_none_tuple_on_unsubscribed(self):
        """중기예보 미신청 시 (None, None, tm_fc_dt) 반환함"""
        api = self._make_api()
        api._fetch = AsyncMock(return_value=self._unsubscribed_response())
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        ta_res, land_res, tm_fc_dt = await api._get_mid_term(now, "11B10101", "11B00000")
        assert ta_res is None
        assert land_res is None
        assert tm_fc_dt is not None

    @pytest.mark.asyncio
    async def test_air_quality_returns_empty_on_station_unsubscribed(self):
        """측정소정보 미신청 시 빈 dict를 반환함"""
        api = self._make_api()
        api._fetch = AsyncMock(return_value=self._unsubscribed_response())
        result = await api._get_air_quality(37.56, 126.98)
        assert result == {}

    @pytest.mark.asyncio
    async def test_warning_returns_none_on_unsubscribed(self):
        """기상특보 미신청 시 '없음'을 반환함"""
        api = self._make_api()
        api._fetch = AsyncMock(return_value=self._unsubscribed_response())
        result = await api._get_warning("L1100200")
        assert result == "없음"

    @pytest.mark.asyncio
    async def test_notification_sent_exactly_once_across_multiple_calls(self):
        """같은 서비스에 여러 번 미신청 응답이 와도 알림은 한 번만 발송됨"""
        api = self._make_api()
        api._fetch = AsyncMock(return_value=self._unsubscribed_response())
        api.nx, api.ny = 60, 127
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        for _ in range(3):
            await api._get_short_term(now)
        assert api.hass.components.persistent_notification.async_create.call_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# 5. fetch_data 통합: coordinator → api 연동
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchDataIntegration:
    @pytest.mark.asyncio
    async def test_fetch_data_passes_reg_ids_to_mid_term(self):
        """fetch_data()가 reg_id를 _get_mid_term에 올바르게 전달함"""
        api = KMAWeatherAPI(MagicMock(), "key")
        captured = {}

        async def mock_mid_term(now, reg_id_temp, reg_id_land):
            captured["reg_id_temp"] = reg_id_temp
            captured["reg_id_land"] = reg_id_land
            return (None, None, datetime(2026, 4, 11, 6, 0, tzinfo=TZ))

        api._get_short_term = AsyncMock(return_value=None)
        api._get_mid_term = mock_mid_term
        api._get_air_quality = AsyncMock(return_value={})
        api._get_address = AsyncMock(return_value="서울시")
        api._get_warning = AsyncMock(return_value="없음")

        await api.fetch_data(
            lat=37.56, lon=126.98, nx=60, ny=127,
            reg_id_temp="11B10101", reg_id_land="11B00000",
            warn_area_code="L1100200",
        )
        assert captured["reg_id_temp"] == "11B10101"
        assert captured["reg_id_land"] == "11B00000"

    @pytest.mark.asyncio
    async def test_fetch_data_passes_warn_area_code(self):
        """fetch_data()가 warn_area_code를 _get_warning에 전달함"""
        api = KMAWeatherAPI(MagicMock(), "key")
        captured = {}

        async def mock_warning(warn_area_code):
            captured["warn_area_code"] = warn_area_code
            return "건조주의보"

        api._get_short_term = AsyncMock(return_value=None)
        api._get_mid_term = AsyncMock(return_value=(None, None, datetime(2026, 4, 11, 6, 0, tzinfo=TZ)))
        api._get_air_quality = AsyncMock(return_value={})
        api._get_address = AsyncMock(return_value="서울시")
        api._get_warning = mock_warning

        result = await api.fetch_data(
            lat=37.56, lon=126.98, nx=60, ny=127,
            reg_id_temp="11B10101", reg_id_land="11B00000",
            warn_area_code="L1100200",
        )
        assert captured["warn_area_code"] == "L1100200"
        assert result["weather"]["warning"] == "건조주의보"

    @pytest.mark.asyncio
    async def test_fetch_data_passes_lat_lon_to_air_quality(self):
        """fetch_data()가 lat/lon을 _get_air_quality에 전달함"""
        api = KMAWeatherAPI(MagicMock(), "key")
        captured = {}

        async def mock_air(lat, lon):
            captured["lat"] = lat
            captured["lon"] = lon
            return {}

        api._get_short_term = AsyncMock(return_value=None)
        api._get_mid_term = AsyncMock(return_value=(None, None, datetime(2026, 4, 11, 6, 0, tzinfo=TZ)))
        api._get_air_quality = mock_air
        api._get_address = AsyncMock(return_value="서울시")
        api._get_warning = AsyncMock(return_value="없음")

        await api.fetch_data(
            lat=37.56, lon=126.98, nx=60, ny=127,
            reg_id_temp="11B10101", reg_id_land="11B00000",
            warn_area_code="L1100200",
        )
        assert captured["lat"] == pytest.approx(37.56)
        assert captured["lon"] == pytest.approx(126.98)


# ─────────────────────────────────────────────────────────────────────────────
# 6. coordinator → api 연동: _async_update_data에서 구역코드 전달 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordinatorApiIntegration:
    @pytest.mark.asyncio
    async def test_coordinator_passes_area_codes_to_api(self, hass):
        """coordinator가 _resolve_area_codes 결과를 api.fetch_data에 전달함"""
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": ""}
        entry.options = {}
        entry.entry_id = "integ_test"
        hass.config.latitude = 37.608025
        hass.config.longitude = 127.094222

        coord = KMAWeatherUpdateCoordinator(hass, entry)
        coord._store_loaded = True
        captured = {}

        async def mock_fetch_data(lat, lon, nx, ny, reg_id_temp, reg_id_land, warn_area_code):
            captured.update({
                "reg_id_temp": reg_id_temp,
                "reg_id_land": reg_id_land,
                "warn_area_code": warn_area_code,
            })
            return None

        coord.api.fetch_data = mock_fetch_data
        coord._cached_data = {"weather": {}, "air": {}}
        await coord._async_update_data()

        assert captured.get("reg_id_temp") == "11B20501"
        assert captured.get("reg_id_land") == "11B00000"
        assert captured.get("warn_area_code") == "L1100200"

    @pytest.mark.asyncio
    async def test_coordinator_area_cache_prevents_recalculation(self, hass):
        """2km 이내 이동 시 구역코드 재계산 없이 캐시를 사용함"""
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": ""}
        entry.options = {}
        entry.entry_id = "cache_test"
        hass.config.latitude = 37.608025
        hass.config.longitude = 127.094222

        coord = KMAWeatherUpdateCoordinator(hass, entry)
        coord._store_loaded = True

        # 첫 번째 구역코드 결정
        r1 = coord._resolve_area_codes(37.608025, 127.094222)
        # 1km 이동 (캐시 범위 내)
        r2 = coord._resolve_area_codes(37.615000, 127.090000)
        assert r1 == r2


# ─────────────────────────────────────────────────────────────────────────────
# 7. 에어코리아 측정소 캐시 (api 내부) 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestAirQualityStationCache:
    def _make_api(self):
        return KMAWeatherAPI(MagicMock(), "key")

    @pytest.mark.asyncio
    async def test_station_cache_used_within_2km(self):
        """2km 이내 이동 시 측정소 캐시를 재사용함 (측정소 API 재호출 없음)"""
        api = self._make_api()
        api._cached_station = "화성"
        api._cached_station_lat = 37.56
        api._cached_station_lon = 126.98

        call_count = {"n": 0}
        air_resp = {"response": {"body": {"items": [{"pm10Value": "30", "pm10Grade": "1",
                                                      "pm25Value": "15", "pm25Grade": "1"}]}}}

        async def mock_fetch(url, params, **kwargs):
            if "MsrstnInfoInqireSvc" in url:
                call_count["n"] += 1
            return air_resp

        api._fetch = mock_fetch
        # 0.5km 이동
        await api._get_air_quality(37.564, 126.982)
        assert call_count["n"] == 0  # 측정소 재조회 없음

    @pytest.mark.asyncio
    async def test_station_cache_invalidated_over_2km(self):
        """2km 초과 이동 시 측정소를 재조회함"""
        api = self._make_api()
        api._cached_station = "화성"
        api._cached_station_lat = 37.56
        api._cached_station_lon = 126.98

        station_resp = {"response": {"body": {"items": [{"stationName": "강남"}]}}}
        air_resp = {"response": {"body": {"items": [{"pm10Value": "30", "pm10Grade": "1",
                                                      "pm25Value": "15", "pm25Grade": "1"}]}}}

        async def mock_fetch(url, params, **kwargs):
            if "MsrstnInfoInqireSvc" in url:
                return station_resp
            return air_resp

        api._fetch = mock_fetch
        # 강남 (약 15km 이동)
        await api._get_air_quality(37.498, 127.027)
        assert api._cached_station == "강남"
        assert api._cached_station_lat == pytest.approx(37.498)


# ─────────────────────────────────────────────────────────────────────────────
# 8. coordinator area.json / warn_area.json 로드 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestJsonLoading:
    def test_temp_id_coords_loaded(self):
        assert len(_TEMP_ID_COORDS) > 100
        assert "11B10101" in _TEMP_ID_COORDS

    def test_land_code_map_loaded(self):
        assert len(_LAND_CODE_MAP) >= 14
        land_dict = dict(_LAND_CODE_MAP)
        assert land_dict.get("11B") == "11B00000"

    def test_exclude_loaded(self):
        assert "11G00601" in _EXCLUDE_FROM_NEAREST
        assert "11E00102" in _EXCLUDE_FROM_NEAREST

    def test_warn_area_loaded(self):
        assert len(_WARN_AREA) > 500
        first = _WARN_AREA[0]
        assert isinstance(first[0], float)
        assert isinstance(first[2], str)
        assert first[2].startswith("L")

    def test_calc_reg_ids_seoul(self):
        reg_temp, reg_land = _calc_reg_ids(37.5665, 126.9780)
        assert reg_temp == "11B10101"
        assert reg_land == "11B00000"

    def test_calc_warn_area_jungrang(self):
        code = _calc_warn_area_code(37.608025, 127.094222)
        assert code == "L1100200"

    def test_excluded_not_returned(self):
        reg_temp, _ = _calc_reg_ids(37.24, 131.86)
        assert reg_temp != "11E00102"


# ─────────────────────────────────────────────────────────────────────────────
# 9. warning 센서 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestWarningSensor:
    def test_warning_in_sensor_types(self):
        assert "warning" in SENSOR_TYPES

    def test_warning_sensor_structure(self):
        details = SENSOR_TYPES["warning"]
        assert len(details) == 6
        name, unit, icon, device_class, suffix, category = details
        assert name == "기상특보"
        assert unit is None
        assert icon == "mdi:alert-outline"
        assert suffix == "warning"
        assert category is None

    def test_warning_sensor_returns_value(self):
        from custom_components.kma_weather.sensor import KMACustomSensor
        coordinator = MagicMock()
        coordinator.data = {"weather": {"warning": "건조주의보"}, "air": {}}
        coordinator._daily_max_temp = None
        coordinator._daily_min_temp = None
        entry = MagicMock()
        entry.entry_id = "warn_test"
        entry.options = {}
        entry.data = {"prefix": "test"}
        sensor = KMACustomSensor(coordinator, "warning", "test", entry)
        assert sensor.native_value == "건조주의보"

    def test_warning_sensor_returns_none_str(self):
        from custom_components.kma_weather.sensor import KMACustomSensor
        coordinator = MagicMock()
        coordinator.data = {"weather": {"warning": "없음"}, "air": {}}
        coordinator._daily_max_temp = None
        coordinator._daily_min_temp = None
        entry = MagicMock()
        entry.entry_id = "warn_none"
        entry.options = {}
        entry.data = {"prefix": "test"}
        sensor = KMACustomSensor(coordinator, "warning", "test", entry)
        assert sensor.native_value == "없음"


# ─────────────────────────────────────────────────────────────────────────────
# 10. _get_warning 동작 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestGetWarning:
    def _make_api(self):
        return KMAWeatherAPI(MagicMock(), "key")

    def _resp(self, items):
        return {"response": {"header": {"resultCode": "00"},
                              "body": {"items": {"item": items}}}}

    @pytest.mark.asyncio
    async def test_active_warning_returned(self):
        api = self._make_api()
        api._fetch = AsyncMock(return_value=self._resp([{
            "command": "1", "cancel": "0", "endTime": "0",
            "warnVar": "4", "warnStress": "0",
        }]))
        assert await api._get_warning("L1100200") == "건조주의보"

    @pytest.mark.asyncio
    async def test_no_area_code_returns_none(self):
        api = self._make_api()
        assert await api._get_warning(None) == "없음"

    @pytest.mark.asyncio
    async def test_ended_warning_excluded(self):
        api = self._make_api()
        api._fetch = AsyncMock(return_value=self._resp([{
            "command": "2", "cancel": "0", "endTime": "202604171700",
            "warnVar": "4", "warnStress": "0",
        }]))
        assert await api._get_warning("L1100200") == "없음"

    @pytest.mark.asyncio
    async def test_multiple_warnings_combined(self):
        api = self._make_api()
        api._fetch = AsyncMock(return_value=self._resp([
            {"command": "1", "cancel": "0", "endTime": "0", "warnVar": "4", "warnStress": "0"},
            {"command": "1", "cancel": "0", "endTime": "0", "warnVar": "2", "warnStress": "0"},
        ]))
        result = await api._get_warning("L1100200")
        assert "건조주의보" in result
        assert "호우주의보" in result

    @pytest.mark.asyncio
    async def test_severe_warning_stress_1(self):
        """warnStress=1이면 경보로 변환됨"""
        api = self._make_api()
        api._fetch = AsyncMock(return_value=self._resp([{
            "command": "1", "cancel": "0", "endTime": "0",
            "warnVar": "8", "warnStress": "1",
        }]))
        assert await api._get_warning("L1100200") == "대설경보"

    @pytest.mark.asyncio
    async def test_fetch_none_returns_none(self):
        api = self._make_api()
        api._fetch = AsyncMock(return_value=None)
        assert await api._get_warning("L1100200") == "없음"
