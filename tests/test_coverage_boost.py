"""
test_coverage_boost.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[변경 이력]
  - test_haversine_known_distance() 제거 → test_coordinator_validation.py 에 있음
  - TestLandCodeMapping 클래스 제거     → test_coordinator_validation.py 에 있음
"""
import pytest
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock

from custom_components.kma_weather.api_kma import KMAWeatherAPI, _safe_float

# ─────────────────────────────────────────────────────────────────────────────
# 1. _safe_float
# ─────────────────────────────────────────────────────────────────────────────
class TestSafeFloat:
    def test_none_returns_none(self): assert _safe_float(None) is None
    def test_empty_string_returns_none(self): assert _safe_float("") is None
    def test_dash_returns_none(self): assert _safe_float("-") is None
    def test_valid_int_string(self): assert _safe_float("22") == 22.0
    def test_valid_float_string(self): assert _safe_float("3.14") == pytest.approx(3.14)
    def test_invalid_string_returns_none(self): assert _safe_float("abc") is None

# ─────────────────────────────────────────────────────────────────────────────
# 2. _calculate_apparent_temp
# ─────────────────────────────────────────────────────────────────────────────
class TestApparentTemp:
    def _api(self):
        return KMAWeatherAPI(MagicMock(), "key")

    def test_wind_chill_branch(self):
        api = self._api()
        result = api._calculate_apparent_temp(temp=5, reh=60, wsd=3)
        assert result is not None
        assert isinstance(result, float)
        assert result < 5

    def test_heat_index_branch(self):
        api = self._api()
        result = api._calculate_apparent_temp(temp=30, reh=70, wsd=1)
        assert result is not None
        assert isinstance(result, float)

    def test_default_branch_returns_temp(self):
        api = self._api()
        result = api._calculate_apparent_temp(temp=20, reh=30, wsd=0.5)
        assert result == 20

    def test_none_temp_returns_none(self):
        api = self._api()
        assert api._calculate_apparent_temp(temp=None, reh=50, wsd=2) is None

    def test_string_temp_parsed(self):
        api = self._api()
        result = api._calculate_apparent_temp(temp="15", reh=50, wsd=0)
        assert result == 15

# ─────────────────────────────────────────────────────────────────────────────
# 3. _get_vec_kor
# ─────────────────────────────────────────────────────────────────────────────
class TestGetVecKor:
    def _api(self):
        return KMAWeatherAPI(MagicMock(), "key")

    @pytest.mark.parametrize("vec,expected", [
        (0, "북"), (22.5, "북동"), (67.5, "동"), (112.5, "남동"),
        (157.5, "남"), (202.5, "남서"), (247.5, "서"), (292.5, "북서"),
        (337.5, "북"), (360, "북"),
    ])
    def test_directions(self, vec, expected):
        api = self._api()
        assert api._get_vec_kor(vec) == expected

    def test_none_vec_returns_none(self):
        api = self._api()
        assert api._get_vec_kor(None) is None

# ─────────────────────────────────────────────────────────────────────────────
# 4. _translate_mid_condition
# ─────────────────────────────────────────────────────────────────────────────
class TestTranslateMidCondition:
    def _api(self):
        return KMAWeatherAPI(MagicMock(), "key")

    @pytest.mark.parametrize("wf,expected_kor", [
        ("맑음",       "맑음"),
        ("구름많음",   "구름많음"),
        ("흐림",       "흐림"),
        ("비",         "비"),
        ("눈",         "눈"),
        ("구름많고 비", "구름많고 비"),
        ("흐리고 눈",   "흐리고 눈"),
        ("예상외문장 비", "비"),
    ])
    def test_kor_mapping(self, wf, expected_kor):
        api = self._api()
        assert api._translate_mid_condition_kor(wf) == expected_kor

    def test_translate_mid_condition_wrapper(self):
        api = self._api()
        result = api._translate_mid_condition("맑음")
        assert result == "sunny"

    def test_get_condition_wrapper(self):
        api = self._api()
        assert api._get_condition("1", "0") == "sunny"
        assert api._get_condition("4", "0") == "cloudy"
        assert api._get_condition("1", "1") == "rainy"

# ─────────────────────────────────────────────────────────────────────────────
# 5. _wgs84_to_tm
# ─────────────────────────────────────────────────────────────────────────────
class TestWgs84ToTm:
    def test_seoul_tm_coords(self):
        api = KMAWeatherAPI(MagicMock(), "key")
        x, y = api._wgs84_to_tm(37.5665, 126.9780)
        assert 100_000 < x < 500_000
        assert 300_000 < y < 700_000

# ─────────────────────────────────────────────────────────────────────────────
# 6. _get_air_quality
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_air_quality_cache_hit():
    api = KMAWeatherAPI(MagicMock(), "key")
    api.lat, api.lon = 37.56, 126.98
    api._cached_station = "화성"
    api._cached_station_lat = 37.56
    api._cached_station_lon = 126.98

    air_json = {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
            "body": {"items": [{
                "pm10Value": "40", "pm10Grade": "2",
                "pm25Value": "18", "pm25Grade": "2",
            }]}
        }
    }

    async def mock_fetch(url, params=None, timeout=10):
        assert "MsrstnInfoInqireSvc" not in url, "캐시 HIT인데 측정소 재조회 발생"
        return air_json

    api._fetch = mock_fetch
    result = await api._get_air_quality(37.56, 126.98)
    assert result["station"] == "화성"
    assert result["pm10Grade"] == "보통"

@pytest.mark.asyncio
async def test_air_quality_no_station_items():
    api = KMAWeatherAPI(MagicMock(), "key")
    api.lat, api.lon = 37.56, 126.98

    async def mock_fetch(url, params=None, timeout=10):
        if "MsrstnInfoInqireSvc" in url:
            return {"response": {"body": {"items": []}}}
        return {}

    api._fetch = mock_fetch
    result = await api._get_air_quality(37.56, 126.98)
    assert result == {}

@pytest.mark.asyncio
async def test_air_quality_no_air_data_items():
    api = KMAWeatherAPI(MagicMock(), "key")
    api.lat, api.lon = 37.56, 126.98

    async def mock_fetch(url, params=None, timeout=10):
        if "MsrstnInfoInqireSvc" in url:
            return {"response": {"body": {"items": [{"stationName": "중구"}]}}}
        return {"response": {"body": {"items": []}}}

    api._fetch = mock_fetch
    result = await api._get_air_quality(37.56, 126.98)
    assert result == {"station": "중구"}

@pytest.mark.asyncio
async def test_air_quality_fetch_returns_none():
    api = KMAWeatherAPI(MagicMock(), "key")
    api.lat, api.lon = 37.56, 126.98

    async def mock_fetch(url, params=None, timeout=10):
        return None

    api._fetch = mock_fetch
    result = await api._get_air_quality(37.56, 126.98)
    assert result == {}

# ─────────────────────────────────────────────────────────────────────────────
# 7. coordinator: 데이터 복구 및 저장 검증
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_restore_daily_temps_success(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock(data={"api_key": "key", "location_entity": ""}, options={}, entry_id="restore_test")
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    today_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    coord._store.async_load = AsyncMock(return_value={
        "date": today_str, "max": 28.5, "min": 12.0, "wf_am": "맑음", "wf_pm": "구름많음",
    })
    await coord._restore_daily_temps()
    assert coord._daily_max_temp == 28.5
    assert coord._daily_min_temp == 12.0
    assert coord._wf_am_today == "맑음"
    assert coord._wf_pm_today == "구름많음"
    assert coord._store_loaded is True

@pytest.mark.asyncio
async def test_restore_daily_temps_date_mismatch(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock(entry_id="restore_mismatch", data={"api_key": "k", "location_entity": ""}, options={})
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store.async_load = AsyncMock(return_value={"date": "20200101", "max": 99.0, "min": -99.0})
    await coord._restore_daily_temps()
    assert coord._daily_max_temp is None
    assert coord._daily_min_temp is None
    assert coord._store_loaded is True

@pytest.mark.asyncio
async def test_save_daily_temps(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock(entry_id="save_test", data={"api_key": "k", "location_entity": ""}, options={})
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._daily_date = date(2025, 6, 1)
    coord._daily_max_temp, coord._daily_min_temp = 30.0, 18.0
    coord._wf_am_today, coord._wf_pm_today = "맑음", "흐림"
    saved = {}
    coord._store.async_save = AsyncMock(side_effect=lambda data: saved.update(data))
    await coord._save_daily_temps()
    assert saved["date"] == "20250601"
    assert saved["max"] == 30.0
    assert saved["min"] == 18.0

# ─────────────────────────────────────────────────────────────────────────────
# 8. coordinator: 위치 해결
# ─────────────────────────────────────────────────────────────────────────────
def test_resolve_location_uses_cached_coords_when_entity_invalid():
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock(data={"api_key": "k", "location_entity": "zone.home"}, options={}, entry_id="cache_fallback")
    hass = MagicMock()
    state = MagicMock(attributes={"latitude": 0.0, "longitude": 0.0})
    hass.states.get.return_value = state
    coord = KMAWeatherUpdateCoordinator.__new__(KMAWeatherUpdateCoordinator)
    coord.hass, coord.entry, coord._last_lat, coord._last_lon = hass, entry, 35.1, 129.0
    lat, lon = coord._resolve_location()
    assert lat == 35.1
    assert lon == 129.0

# ─────────────────────────────────────────────────────────────────────────────
# 9. button & config_flow & sensor
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_button_press_cooldown(hass, kma_api_mock_factory):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.kma_weather.const import DOMAIN
    from custom_components.kma_weather.button import KMAUpdateButton
    entry = MockConfigEntry(domain=DOMAIN, data={"api_key": "k", "prefix": "cool", "location_entity": ""}, entry_id="cool_test")
    kma_api_mock_factory("full_test")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.async_request_refresh = AsyncMock()
    button = KMAUpdateButton(coordinator, entry)
    await button.async_press()
    button._last_press = datetime.now() - timedelta(seconds=3)
    await button.async_press()
    assert coordinator.async_request_refresh.call_count == 1

@pytest.mark.asyncio
async def test_options_flow(hass, mock_config_entry, kma_api_mock_factory):
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"location_entity": "zone.home", "expire_date": "2026-12-31", "apply_date": "2025-01-01"}
    )
    assert result2["type"] == "create_entry"

# ─────────────────────────────────────────────────────────────────────────────
# 10. 유틸리티 헬퍼
# [중복 제거] test_haversine_known_distance, TestLandCodeMapping
#             → test_coordinator_validation.py 에 있으므로 이 파일에서 삭제
# ─────────────────────────────────────────────────────────────────────────────

class TestGetAirGrade:
    def _api(self): return KMAWeatherAPI(MagicMock(), "key")

    @pytest.mark.parametrize("val,p_type,expected", [
        (15, "pm10", "좋음"), (50, "pm10", "보통"), (100, "pm10", "나쁨"), (200, "pm10", "매우나쁨"),
        (5, "pm25", "좋음"), (25, "pm25", "보통"), (50, "pm25", "나쁨"), (100, "pm25", "매우나쁨"),
        (None, "pm10", "정보없음"), ("-", "pm25", "정보없음")
    ])
    def test_all_grades(self, val, p_type, expected):
        assert self._api()._get_air_grade(val, p_type) == expected

# ══════════════════════════════════════════════════════════════════════════════
# _get_pollen 추가 커버리지
# ══════════════════════════════════════════════════════════════════════════════

class TestPollenAdditionalCoverage:
    """_get_pollen 미커버 분기 추가 테스트"""

    def _make_api(self):
        api = KMAWeatherAPI(MagicMock(), "test_key")
        api.hass = None
        api._approved_apis.add("pollen")
        api._pending_apis.discard("pollen")
        return api

    def _ok_response(self, today="1"):
        return {"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                "body": {"dataType": "JSON", "items": {"item": [{
                    "today": today, "tomorrow": "2",
                }]}, "pageNo": 1, "numOfRows": 10, "totalCount": 1}}}

    def _99_response(self):
        return {"response": {"header": {"resultCode": "99", "resultMsg": "NO_DATA"},
                "body": {"items": {"item": []}}}}

    @pytest.mark.asyncio
    async def test_check_code_99_returns_none_for_season_kinds(self):
        """check_code=99 → 시즌 항목 None, 비시즌 항목 좋음"""
        api = self._make_api()
        api._fetch = AsyncMock(return_value=self._99_response())
        now = datetime(2026, 5, 1, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))  # oak/pine 시즌
        result = await api._get_pollen(now, "1111051500", "서울")
        assert result is not None
        assert result.get("oak") is None    # 시즌 → unknown
        assert result.get("pine") is None   # 시즌 → unknown
        assert result.get("grass") == "좋음"  # 비시즌 → 좋음
        assert result.get("worst") is None
        assert result.get("announcement") == "데이터없음"

    @pytest.mark.asyncio
    async def test_never_approved_yet_and_rc99_still_marks_approved(self):
        """
        [Given] 꽃가루 API가 한 번도 승인된 적 없음(_pending 상태, _approved 비어있음)
                + 신청은 유효하지만 해당 지역에 데이터가 없어 resultCode=99 응답
        [When]  _get_pollen 최초 호출
        [Then]  "미신청"이 아니라 "구독은 유효함"으로 판단하여
                _approved_apis에 pollen이 추가됨 (센서가 생성될 수 있게 됨)
                _pending_apis에서는 제거됨
                반환값은 시즌 항목 unknown, 비시즌 항목 좋음으로 정상 처리됨
        """
        api = KMAWeatherAPI(MagicMock(), "test_key")
        api.hass = None
        # 승인 이력이 전혀 없는 최초 상태를 재현 (approved 비어있고 pending에만 존재)
        assert "pollen" not in api._approved_apis
        assert "pollen" in api._pending_apis

        api._fetch = AsyncMock(return_value=self._99_response())
        now = datetime(2026, 5, 1, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))  # oak/pine 시즌

        result = await api._get_pollen(now, "1111051500", "서울")

        assert "pollen" in api._approved_apis, \
            "resultCode=99여도 미신청이 아니므로 승인 처리되어야 함 (센서 생성 사각지대 방지)"
        assert "pollen" not in api._pending_apis
        assert result is not None
        assert result.get("oak") is None
        assert result.get("pine") is None
        assert result.get("grass") == "좋음"
        assert result.get("announcement") == "데이터없음"

    @pytest.mark.asyncio
    async def test_offseason_and_never_approved_still_calls_api_to_check(self):
        """
        [Given] 완전 비시즌(예: 1월)이지만 아직 한 번도 승인된 적 없음
        [When]  _get_pollen 호출
        [Then]  "비시즌이니 API 호출 없이 좋음 반환" 지름길은
                _approved_apis에 pollen이 있을 때만 적용되므로,
                아직 미승인 상태면 API를 호출해서 승인 여부를 확인하러 감
        """
        api = KMAWeatherAPI(MagicMock(), "test_key")
        api.hass = None
        assert "pollen" not in api._approved_apis

        api._fetch = AsyncMock(return_value=self._99_response())
        now = datetime(2026, 1, 15, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))  # 완전 비시즌

        await api._get_pollen(now, "1111051500", "서울")

        api._fetch.assert_called_once()
        assert "pollen" in api._approved_apis

    @pytest.mark.asyncio
    async def test_today_cache_exists_19h_tomorrow_g_none(self):
        """
        [Given] today 캐시 있음 + pollen 승인됨(_pending 없음) + 19시 이후
                tomorrow API가 빈 items 반환 → g=None
        [When]  _get_pollen 호출
        [Then]  today 캐시 반환 + tomorrow 캐시 초기화됨
        """
        api = self._make_api()
        # pine/oak 시즌(4~6월) 내로 날짜 고정 — datetime.now() 사용 시
        # 시즌 밖(예: 7월 이후) 실행되면 pine이 비시즌 처리되어 테스트가 깨짐
        now = datetime(2026, 4, 25, 19, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        today_str = now.strftime("%Y%m%d")

        # _pending에서 제거 + _approved에만 있어야 캐시를 신뢰함
        api._pending_apis.discard("pollen")
        api._approved_apis.add("pollen")

        for k in ("pine", "oak", "grass"):
            api._pollen_cache[k]["today"] = "좋음"
            api._pollen_cache[k]["today_date"] = today_str

        fetch_count = {"n": 0}

        async def mock_fetch(url, params):
            fetch_count["n"] += 1
            return {"response": {"header": {"resultCode": "00"},
                    "body": {"items": {"item": []}}}}

        api._fetch = mock_fetch
        result = await api._get_pollen(now, "1111051500", "서울")

        assert result is not None, "today 캐시 있을 때 None 반환됨"
        assert result.get("pine") == "좋음"
        # grass는 비시즌(8~10월)이라 tomorrow 갱신 대상 아님 → pine/oak만 검증
        for k in ("pine", "oak"):
            assert api._pollen_cache[k]["tomorrow"] is None
            assert api._pollen_cache[k]["tomorrow_date"] is None

    @pytest.mark.asyncio
    async def test_today_cache_exists_19h_tomorrow_updated(self):
        """
        [Given] today 캐시 있음 + pollen 승인됨(_pending 없음) + 19시 이후
        [When]  _get_pollen 호출
        [Then]  today 캐시 반환 + tomorrow 캐시 갱신됨
        """
        api = self._make_api()
        # pine/oak 시즌(4~6월) 내로 날짜 고정 — datetime.now() 사용 시
        # 시즌 밖(예: 7월 이후) 실행되면 pine이 비시즌 처리되어 테스트가 깨짐
        now = datetime(2026, 4, 25, 19, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        today_str = now.strftime("%Y%m%d")

        # _pending에서 제거 + _approved에만 있어야 캐시를 신뢰함
        # (_pending에 있으면 소스코드가 캐시를 즉시 초기화한다)
        api._pending_apis.discard("pollen")
        api._approved_apis.add("pollen")

        for k in ("pine", "oak", "grass"):
            api._pollen_cache[k]["today"] = "좋음"
            api._pollen_cache[k]["today_date"] = today_str

        api._fetch = AsyncMock(return_value=self._ok_response(today="2"))
        result = await api._get_pollen(now, "1111051500", "서울")

        assert result is not None, "today 캐시 있을 때 None 반환됨"
        assert result.get("pine") == "좋음", f"today 캐시 반환 실패: {result.get('pine')}"
        # grass는 8~10월만 시즌 → 6월 기준 비시즌이므로 tomorrow 갱신 대상 아님
        # pine/oak(4~6월 시즌)만 tomorrow 갱신 검증
        for k in ("pine", "oak"):
            assert api._pollen_cache[k]["tomorrow"] is not None, f"{k} tomorrow 캐시 갱신 안 됨"
            assert api._pollen_cache[k]["tomorrow_date"] == today_str


# ══════════════════════════════════════════════════════════════════════════════
# 에어코리아 측정소 캐시 영속화 검증
# ══════════════════════════════════════════════════════════════════════════════

class TestStationCachePersistence:
    """
    재시작 시 에어코리아 측정소 캐시가 유지되는지 검증.

    배경: _cached_station이 저장소(Store) 영속화 없이 API 인스턴스
    메모리에만 있어서, HA 재시작마다 getNearbyMsrstnList API를 다시
    호출하게 되어 '에어코리아_측정소' 호출 카운터가 실제 위치 이동 없이도
    계속 쌓이던 문제를 수정.
    """

    def _make_coordinator(self, hass):
        from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": ""}
        entry.options = {}
        entry.entry_id = "station_cache_test"
        return KMAWeatherUpdateCoordinator(hass, entry)

    @pytest.mark.asyncio
    async def test_restore_station_cache_from_store(self, hass):
        """
        [Given] 저장소에 이전에 저장된 측정소 정보가 있음
        [When]  _restore_station_cache 호출
        [Then]  api._cached_station과 위경도가 저장소 값으로 복구됨
        """
        coord = self._make_coordinator(hass)
        coord._station_store.async_load = AsyncMock(return_value={
            "station": "서석동", "lat": 35.145, "lon": 126.918,
        })

        await coord._restore_station_cache()

        assert coord.api._cached_station == "서석동"
        assert coord.api._cached_station_lat == 35.145
        assert coord.api._cached_station_lon == 126.918

    @pytest.mark.asyncio
    async def test_restore_station_cache_no_stored_data(self, hass):
        """
        [Given] 저장소에 아무것도 없음(최초 설치 등)
        [When]  _restore_station_cache 호출
        [Then]  크래시 없이 api._cached_station은 None으로 유지됨
        """
        coord = self._make_coordinator(hass)
        coord._station_store.async_load = AsyncMock(return_value=None)

        await coord._restore_station_cache()

        assert coord.api._cached_station is None

    @pytest.mark.asyncio
    async def test_restore_station_cache_only_runs_once(self, hass):
        """
        [Given] _restore_station_cache를 두 번 연달아 호출
        [When]  두 번째 호출
        [Then]  저장소 조회(async_load)는 처음 한 번만 일어남
        """
        coord = self._make_coordinator(hass)
        coord._station_store.async_load = AsyncMock(return_value={
            "station": "서석동", "lat": 35.145, "lon": 126.918,
        })

        await coord._restore_station_cache()
        await coord._restore_station_cache()

        coord._station_store.async_load.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_station_cache_writes_current_values(self, hass):
        """
        [Given] api._cached_station에 값이 설정되어 있음
        [When]  _save_station_cache 호출
        [Then]  저장소에 station/lat/lon이 그대로 기록됨
        """
        coord = self._make_coordinator(hass)
        coord.api._cached_station = "구월동"
        coord.api._cached_station_lat = 37.447
        coord.api._cached_station_lon = 126.731

        saved = {}
        coord._station_store.async_save = AsyncMock(side_effect=lambda d: saved.update(d))

        await coord._save_station_cache()

        assert saved == {"station": "구월동", "lat": 37.447, "lon": 126.731}

    @pytest.mark.asyncio
    async def test_save_station_cache_skips_when_no_station(self, hass):
        """
        [Given] api._cached_station이 아직 None(측정소 조회 전)
        [When]  _save_station_cache 호출
        [Then]  저장소 쓰기(async_save)가 호출되지 않음
        """
        coord = self._make_coordinator(hass)
        coord.api._cached_station = None
        coord._station_store.async_save = AsyncMock()

        await coord._save_station_cache()

        coord._station_store.async_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_restart_does_not_trigger_new_station_search_when_location_unchanged(self, hass):
        """
        [Given] 재시작 후 저장소에서 측정소 캐시가 복구된 상태
                (위치는 이전과 동일, 2km 이상 이동 없음)
        [When]  _get_air_quality 호출
        [Then]  getNearbyMsrstnList(측정소 검색) API는 호출되지 않고
                기존 캐시된 측정소로 바로 대기질 조회만 수행됨
        """
        coord = self._make_coordinator(hass)
        coord._station_store.async_load = AsyncMock(return_value={
            "station": "서석동", "lat": 35.145, "lon": 126.918,
        })
        await coord._restore_station_cache()

        station_search_called = {"n": 0}

        async def mock_fetch(url, params, **kwargs):
            if "MsrstnInfoInqireSvc" in url:
                station_search_called["n"] += 1
                return {"response": {"body": {"items": []}}}
            return {"response": {"body": {"items": [
                {"pm10Value": "30", "pm25Value": "15"}
            ]}}}

        coord.api._fetch = mock_fetch
        # 재시작 후 첫 위치가 캐시된 위치와 거의 동일(2km 이내)
        await coord.api._get_air_quality(35.145, 126.918)

        assert station_search_called["n"] == 0, \
            "재시작 후 위치가 안 바뀌었는데도 측정소를 재검색함"

    @pytest.mark.asyncio
    async def test_location_moved_over_2km_still_triggers_research(self, hass):
        """
        [Given] 캐시된 측정소 위치에서 2km 이상 떨어진 곳으로 이동
        [When]  _get_air_quality 호출
        [Then]  캐시가 무효화되고 측정소를 다시 검색함
                (이 동작 자체는 정상이며 영속화 수정과 무관하게 유지되어야 함)
        """
        coord = self._make_coordinator(hass)
        coord._station_store.async_load = AsyncMock(return_value={
            "station": "서석동", "lat": 35.145, "lon": 126.918,
        })
        await coord._restore_station_cache()

        station_search_called = {"n": 0}

        async def mock_fetch(url, params, **kwargs):
            if "MsrstnInfoInqireSvc" in url:
                station_search_called["n"] += 1
                return {"response": {"body": {"items": [{"stationName": "신도시동"}]}}}
            return {"response": {"body": {"items": [
                {"pm10Value": "30", "pm25Value": "15"}
            ]}}}

        coord.api._fetch = mock_fetch
        # 약 30km 이상 떨어진 위치로 이동
        await coord.api._get_air_quality(35.40, 127.20)

        assert station_search_called["n"] == 1, \
            "2km 이상 이동했는데도 측정소 재검색이 일어나지 않음"
