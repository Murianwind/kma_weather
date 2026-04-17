import pytest
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch

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
        return KMAWeatherAPI(MagicMock(), "key")  # reg_id 제거

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
        return KMAWeatherAPI(MagicMock(), "key")  # reg_id 제거

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
        return KMAWeatherAPI(MagicMock(), "key")  # reg_id 제거

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
        api = KMAWeatherAPI(MagicMock(), "key")  # reg_id 제거
        x, y = api._wgs84_to_tm(37.5665, 126.9780)
        assert 100_000 < x < 500_000
        assert 300_000 < y < 700_000

# ─────────────────────────────────────────────────────────────────────────────
# 6. _get_air_quality
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_air_quality_cache_hit():
    api = KMAWeatherAPI(MagicMock(), "key")  # reg_id 제거
    api.lat, api.lon = 37.56, 126.98
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    api._cached_station = "화성"
    api._cached_station_lat = 37.56
    api._cached_station_lon = 126.98

    air_json = {
        "response": {"body": {"items": [{
            "pm10Value": "40", "pm10Grade": "2",
            "pm25Value": "18", "pm25Grade": "2",
        }]}}
    }

    async def mock_fetch(url, params=None, timeout=10):
        assert "MsrstnInfoInqireSvc" not in url, "캐시 HIT인데 측정소 재조회 발생"
        return air_json

    api._fetch = mock_fetch
    result = await api._get_air_quality()
    assert result["station"] == "화성"
    assert result["pm10Grade"] == "보통"

@pytest.mark.asyncio
async def test_air_quality_no_station_items():
    api = KMAWeatherAPI(MagicMock(), "key")  # reg_id 제거
    api.lat, api.lon = 37.56, 126.98

    async def mock_fetch(url, params=None, timeout=10):
        if "MsrstnInfoInqireSvc" in url:
            return {"response": {"body": {"items": []}}}
        return {}

    api._fetch = mock_fetch
    result = await api._get_air_quality()
    assert result == {}

@pytest.mark.asyncio
async def test_air_quality_no_air_data_items():
    api = KMAWeatherAPI(MagicMock(), "key")  # reg_id 제거
    api.lat, api.lon = 37.56, 126.98

    async def mock_fetch(url, params=None, timeout=10):
        if "MsrstnInfoInqireSvc" in url:
            return {"response": {"body": {"items": [{"stationName": "중구"}]}}}
        return {"response": {"body": {"items": []}}}

    api._fetch = mock_fetch
    result = await api._get_air_quality()
    assert result == {"station": "중구"}

@pytest.mark.asyncio
async def test_air_quality_fetch_returns_none():
    api = KMAWeatherAPI(MagicMock(), "key")  # reg_id 제거
    api.lat, api.lon = 37.56, 126.98

    async def mock_fetch(url, params=None, timeout=10):
        return None

    api._fetch = mock_fetch
    result = await api._get_air_quality()
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
    from datetime import date
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
# ─────────────────────────────────────────────────────────────────────────────
from custom_components.kma_weather.coordinator import _haversine, _land_code

def test_haversine_known_distance():
    d = _haversine(37.5665, 126.9780, 35.1796, 129.0756)
    assert 310 < d < 340

class TestLandCodeMapping:
    @pytest.mark.parametrize("temp_id,expected_land", [
        ("11B10101", "11B00000"),
        ("11A00101", "11A00101"),
        ("11H10101", "11H10000"),
    ])
    def test_land_code(self, temp_id, expected_land):
        assert _land_code(temp_id) == expected_land

class TestTranslateGrade:
    def _api(self): return KMAWeatherAPI(MagicMock(), "key")  # reg_id 제거
    @pytest.mark.parametrize("grade,expected", [
        ("1", "좋음"), (None, "정보없음"), ("5", "정보없음")
    ])
    def test_all_grades(self, grade, expected):
        assert self._api()._translate_grade(grade) == expected
