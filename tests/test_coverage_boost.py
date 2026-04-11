# tests/test_coverage_boost.py
"""
커버리지 80% 달성을 위한 보완 테스트.

커버 대상:
  - api_kma.py  : _calculate_apparent_temp, _get_vec_kor, _get_air_quality,
                  _wgs84_to_tm, _safe_float, _translate_mid_condition,
                  _get_condition
  - coordinator.py : _restore_daily_temps, _save_daily_temps, _resolve_location
  - button.py   : async_press (정상 / 쿨다운 5초 제한)
  - config_flow.py : OptionsFlow
  - sensor.py   : extra_state_attributes
"""

import pytest
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.kma_weather.api_kma import KMAWeatherAPI, _safe_float


# ---------------------------------------------------------------------------
# _safe_float
# ---------------------------------------------------------------------------
class TestSafeFloat:
    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_empty_string_returns_none(self):
        assert _safe_float("") is None

    def test_dash_returns_none(self):
        assert _safe_float("-") is None

    def test_valid_int_string(self):
        assert _safe_float("22") == 22.0

    def test_valid_float_string(self):
        assert _safe_float("3.14") == pytest.approx(3.14)

    def test_invalid_string_returns_none(self):
        assert _safe_float("abc") is None


# ---------------------------------------------------------------------------
# _calculate_apparent_temp
# ---------------------------------------------------------------------------
class TestApparentTemp:
    def _api(self):
        return KMAWeatherAPI(MagicMock(), "key", "r1", "r2")

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


# ---------------------------------------------------------------------------
# _get_vec_kor (8방위)
# ---------------------------------------------------------------------------
class TestGetVecKor:
    def _api(self):
        return KMAWeatherAPI(MagicMock(), "key", "r1", "r2")

    @pytest.mark.parametrize("vec,expected", [
        (0,     "북"),
        (22.5,  "북동"),
        (67.5,  "동"),
        (112.5, "남동"),
        (157.5, "남"),
        (202.5, "남서"),
        (247.5, "서"),
        (292.5, "북서"),
        (337.5, "북"),
        (360,   "북"),
    ])
    def test_directions(self, vec, expected):
        api = self._api()
        assert api._get_vec_kor(vec) == expected

    def test_none_vec_returns_none(self):
        api = self._api()
        assert api._get_vec_kor(None) is None


# ---------------------------------------------------------------------------
# _translate_mid_condition_kor + _translate_mid_condition
# ---------------------------------------------------------------------------
class TestTranslateMidCondition:
    def _api(self):
        return KMAWeatherAPI(MagicMock(), "key", "r1", "r2")

    @pytest.mark.parametrize("wf,expected_kor", [
        ("맑음",       "맑음"),
        ("구름많음",   "구름많음"),
        ("흐림",       "흐림"),
        ("비",         "비"),
        ("눈",         "눈"),
        ("구름많고 비", "비"),
        ("흐리고 눈",  "눈"),
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


# ---------------------------------------------------------------------------
# _wgs84_to_tm
# ---------------------------------------------------------------------------
class TestWgs84ToTm:
    def test_seoul_tm_coords(self):
        api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
        x, y = api._wgs84_to_tm(37.5665, 126.9780)
        assert 100_000 < x < 500_000
        assert 300_000 < y < 700_000


# ---------------------------------------------------------------------------
# _get_air_quality
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_air_quality_cache_hit():
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    api.lat, api.lon = 37.56, 126.98
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    api._cached_station = "화성"
    api._cached_lat_lon = (37.56, 126.98)
    api._station_cache_time = now

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
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    api.lat, api.lon = 37.56, 126.98
    api._cached_station = None
    api._cached_lat_lon = None
    api._station_cache_time = None

    async def mock_fetch(url, params=None, timeout=10):
        if "MsrstnInfoInqireSvc" in url:
            return {"response": {"body": {"items": []}}}
        return {}

    api._fetch = mock_fetch
    result = await api._get_air_quality()
    assert result == {}


@pytest.mark.asyncio
async def test_air_quality_no_air_data_items():
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    api.lat, api.lon = 37.56, 126.98
    api._cached_station = None
    api._cached_lat_lon = None
    api._station_cache_time = None

    async def mock_fetch(url, params=None, timeout=10):
        if "MsrstnInfoInqireSvc" in url:
            return {"response": {"body": {"items": [{"stationName": "중구"}]}}}
        return {"response": {"body": {"items": []}}}

    api._fetch = mock_fetch
    result = await api._get_air_quality()
    assert result == {"station": "중구"}


@pytest.mark.asyncio
async def test_air_quality_fetch_returns_none():
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    api.lat, api.lon = 37.56, 126.98
    api._cached_station = None
    api._cached_lat_lon = None
    api._station_cache_time = None

    async def mock_fetch(url, params=None, timeout=10):
        return None

    api._fetch = mock_fetch
    result = await api._get_air_quality()
    assert result == {}


# ---------------------------------------------------------------------------
# coordinator._restore_daily_temps
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_restore_daily_temps_success(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "restore_test"

    coord = KMAWeatherUpdateCoordinator(hass, entry)
    tz = ZoneInfo("Asia/Seoul")
    today_str = datetime.now(tz).strftime("%Y%m%d")

    coord._store.async_load = AsyncMock(return_value={
        "date": today_str,
        "max": 28.5,
        "min": 12.0,
        "wf_am": "맑음",
        "wf_pm": "구름많음",
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

    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "restore_date_mismatch"

    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store.async_load = AsyncMock(return_value={
        "date": "20200101",
        "max": 99.0,
        "min": -99.0,
    })

    await coord._restore_daily_temps()
    assert coord._daily_max_temp is None
    assert coord._daily_min_temp is None
    assert coord._store_loaded is True


@pytest.mark.asyncio
async def test_restore_daily_temps_empty_store(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "restore_empty"

    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store.async_load = AsyncMock(return_value=None)

    await coord._restore_daily_temps()
    assert coord._store_loaded is True
    assert coord._daily_max_temp is None


# ---------------------------------------------------------------------------
# coordinator._save_daily_temps
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_save_daily_temps(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "save_test"

    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._daily_date = date(2025, 6, 1)
    coord._daily_max_temp = 30.0
    coord._daily_min_temp = 18.0
    coord._wf_am_today = "맑음"
    coord._wf_pm_today = "흐림"

    saved = {}

    async def mock_save(data):
        saved.update(data)

    coord._store.async_save = mock_save
    await coord._save_daily_temps()

    assert saved["date"] == "20250601"
    assert saved["max"] == 30.0
    assert saved["min"] == 18.0
    assert saved["wf_am"] == "맑음"
    assert saved["wf_pm"] == "흐림"


@pytest.mark.asyncio
async def test_save_daily_temps_skips_when_no_date(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "save_skip_test"

    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._daily_date = None

    coord._store.async_save = AsyncMock()
    await coord._save_daily_temps()
    coord._store.async_save.assert_not_called()


# ---------------------------------------------------------------------------
# coordinator._resolve_location
# ---------------------------------------------------------------------------
def test_resolve_location_uses_cached_coords_when_entity_invalid():
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

    entry = MagicMock()
    entry.data = {"location_entity": "zone.home"}
    entry.options = {}
    entry.entry_id = "cache_fallback"

    hass = MagicMock()
    state = MagicMock()
    state.attributes = {"latitude": 0.0, "longitude": 0.0}
    hass.states.get.return_value = state
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98

    coord = KMAWeatherUpdateCoordinator.__new__(KMAWeatherUpdateCoordinator)
    coord.hass = hass
    coord.entry = entry
    coord._last_lat = 35.1
    coord._last_lon = 129.0

    lat, lon = coord._resolve_location()
    assert lat == 35.1
    assert lon == 129.0


def test_resolve_location_falls_back_to_ha_config():
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

    entry = MagicMock()
    entry.data = {"location_entity": ""}
    entry.options = {}
    entry.entry_id = "ha_config_fallback"

    hass = MagicMock()
    hass.states.get.return_value = None
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98

    coord = KMAWeatherUpdateCoordinator.__new__(KMAWeatherUpdateCoordinator)
    coord.hass = hass
    coord.entry = entry
    coord._last_lat = None
    coord._last_lon = None

    lat, lon = coord._resolve_location()
    assert lat == pytest.approx(37.56)
    assert lon == pytest.approx(126.98)


# ---------------------------------------------------------------------------
# button.py — async_press
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_button_press_triggers_refresh(hass, mock_config_entry, kma_api_mock_factory):
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98

    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.kma_weather.const import DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "api_key": "test_key",
            "prefix": "btn",
            "location_entity": "device_tracker.my_phone",
        },
        entry_id="btn_test",
        title="기상청 날씨: 테스트",
    )

    kma_api_mock_factory("full_test")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    button_state = hass.states.get("button.btn_manual_update")
    assert button_state is not None, "버튼 엔티티가 생성되지 않았습니다"

    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.async_request_refresh = AsyncMock()

    await hass.services.async_call(
        "button", "press",
        target={"entity_id": "button.btn_manual_update"},
        blocking=True,
    )
    await hass.async_block_till_done()
    coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_button_press_cooldown(hass, kma_api_mock_factory):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.kma_weather.const import DOMAIN
    from custom_components.kma_weather.button import KMAUpdateButton

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "api_key": "test_key",
            "prefix": "cool",
            "location_entity": "device_tracker.phone",
        },
        entry_id="cooldown_test",
        title="테스트",
    )

    kma_api_mock_factory("full_test")
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.async_request_refresh = AsyncMock()

    button = KMAUpdateButton(coordinator, entry)

    await button.async_press()
    assert coordinator.async_request_refresh.call_count == 1

    button._last_press = datetime.now() - timedelta(seconds=3)
    await button.async_press()
    assert coordinator.async_request_refresh.call_count == 1

    button._last_press = datetime.now() - timedelta(seconds=6)
    await button.async_press()
    assert coordinator.async_request_refresh.call_count == 2


# ---------------------------------------------------------------------------
# config_flow — OptionsFlow
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_options_flow(hass, mock_config_entry, kma_api_mock_factory):
    from homeassistant import config_entries

    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    kma_api_mock_factory("full_test")

    hass.states.async_set("zone.home", "zoning", {"latitude": 37.56, "longitude": 126.98})

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "init"

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "location_entity": "zone.home",
            "expire_date": "2026-12-31",
            "apply_date": "2025-01-01",
        },
    )
    assert result2["type"] == "create_entry"


# ---------------------------------------------------------------------------
# sensor.py — extra_state_attributes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sensor_location_extra_attributes(hass, mock_config_entry, kma_api_mock_factory):
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    kma_api_mock_factory("full_test")

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    loc = hass.states.get("sensor.test_location")
    assert loc is not None

    attrs = loc.attributes
    assert "air_korea_station" in attrs
    assert "short_term_nx" in attrs
    assert "short_term_ny" in attrs
    assert "latitude" in attrs
    assert "longitude" in attrs


# ---------------------------------------------------------------------------
# _translate_grade
# ---------------------------------------------------------------------------
class TestTranslateGrade:
    def _api(self):
        return KMAWeatherAPI(MagicMock(), "key", "r1", "r2")

    @pytest.mark.parametrize("grade,expected", [
        ("1", "좋음"),
        ("2", "보통"),
        ("3", "나쁨"),
        ("4", "매우나쁨"),
        (1,   "좋음"),
        (None, "정보없음"),
        ("5",  "정보없음"),
        ("",   "정보없음"),
    ])
    def test_all_grades(self, grade, expected):
        api = self._api()
        assert api._translate_grade(grade) == expected


# ---------------------------------------------------------------------------
# _get_sky_kor
# ---------------------------------------------------------------------------
class TestGetSkyKor:
    def _api(self):
        return KMAWeatherAPI(MagicMock(), "key", "r1", "r2")

    @pytest.mark.parametrize("sky,pty,expected", [
        ("1", "0", "맑음"),
        ("3", "0", "구름많음"),
        ("4", "0", "흐림"),
        ("1", "1", "비"),
        ("1", "2", "비/눈"),
        ("1", "3", "눈"),
        ("1", "4", "소나기"),
        (None, None, "맑음"),
    ])
    def test_sky_kor_mapping(self, sky, pty, expected):
        api = self._api()
        assert api._get_sky_kor(sky, pty) == expected


# ---------------------------------------------------------------------------
# coordinator helpers
# ---------------------------------------------------------------------------
from custom_components.kma_weather.coordinator import _haversine, _land_code


def test_haversine_same_point():
    assert _haversine(37.5, 127.0, 37.5, 127.0) == pytest.approx(0.0)


def test_haversine_known_distance():
    d = _haversine(37.5665, 126.9780, 35.1796, 129.0756)
    assert 310 < d < 340


class TestLandCodeMapping:
    @pytest.mark.parametrize("temp_id,expected_land", [
        ("11B10101", "11B00000"),
        ("11G00101", "11G00000"),
        ("11A00101", "11A00101"),
        ("11E00101", "11E00101"),
        ("11H10101", "11H10000"),
    ])
    def test_land_code(self, temp_id, expected_land):
        assert _land_code(temp_id) == expected_land
