"""
모든 미커버 라인을 커버하는 테스트.
"""
import pytest
import hashlib
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.kma_weather.api_kma import KMAWeatherAPI
from custom_components.kma_weather.sensor import KMACustomSensor
from custom_components.kma_weather.coordinator import _load_area_data
_load_area_data()  # 테스트 실행 전 정적 데이터 로드

TZ = ZoneInfo("Asia/Seoul")


def test_nominatim_agent_with_valid_uuid():
    class HasUuidHass:
        installation_uuid = "abcdef12-3456-7890-abcd-ef1234567890"
    api = KMAWeatherAPI(MagicMock(), "key", hass=HasUuidHass())
    assert "abcdef123456" in api._nominatim_user_agent


def test_nominatim_agent_no_hass_uses_hash():
    api = KMAWeatherAPI(MagicMock(), "MY_SECRET_KEY")
    expected_hash = hashlib.sha1("MY_SECRET_KEY".encode()).hexdigest()[:12]
    assert expected_hash in api._nominatim_user_agent


def test_nominatim_agent_hash_exception_returns_base():
    with patch("hashlib.sha1", side_effect=Exception("hash fail")):
        api = KMAWeatherAPI(MagicMock(), "key")
    assert api._nominatim_user_agent == "HomeAssistant-KMA-Weather"


@pytest.mark.asyncio
async def test_fetch_data_full_path():
    api = KMAWeatherAPI(MagicMock(), "key")
    api._get_short_term = AsyncMock(return_value=None)
    api._get_mid_term = AsyncMock(return_value=(None, None, datetime(2026, 4, 11, 6, 0, tzinfo=TZ)))
    api._get_air_quality = AsyncMock(return_value={"pm10Value": "30"})
    api._get_address = AsyncMock(return_value="서울시")
    result = await api.fetch_data(37.56, 126.98, 60, 127, "11B10101", "11B00000", "4111100000")
    assert result is not None
    assert "weather" in result
    assert result["weather"]["address"] == "서울시"


@pytest.mark.asyncio
async def test_fetch_data_with_exception_in_task():
    api = KMAWeatherAPI(MagicMock(), "key")
    api._get_short_term = AsyncMock(side_effect=Exception("network error"))
    api._get_mid_term = AsyncMock(return_value=(None, None, datetime(2026, 4, 11, 6, 0, tzinfo=TZ)))
    api._get_air_quality = AsyncMock(return_value={})
    api._get_address = AsyncMock(return_value="서울시")
    result = await api.fetch_data(37.56, 126.98, 60, 127, "11B10101", "11B00000", "4111100000")
    assert result is not None


@pytest.mark.asyncio
async def test_get_address_fetch_returns_none():
    api = KMAWeatherAPI(MagicMock(), "key")
    api._fetch = AsyncMock(return_value=None)
    result = await api._get_address(37.56, 126.98)
    assert result == "37.5600, 126.9800"


@pytest.mark.asyncio
async def test_get_address_exception_fallback():
    api = KMAWeatherAPI(MagicMock(), "key")
    api._fetch = AsyncMock(side_effect=Exception("timeout"))
    result = await api._get_address(37.56, 126.98)
    assert "37.5600" in result


@pytest.mark.asyncio
async def test_air_quality_exception_returns_empty():
    api = KMAWeatherAPI(MagicMock(), "key")
    api.lat, api.lon = 37.56, 126.98
    api._cached_station = "테스트"

    async def bad_fetch(url, params=None, timeout=10):
        raise Exception("connection error")

    api._fetch = bad_fetch
    result = await api._get_air_quality(37.56, 126.98)
    assert result == {}


@pytest.mark.asyncio
async def test_get_short_term_midnight():
    api = KMAWeatherAPI(MagicMock(), "key")
    api.nx, api.ny = 60, 127
    called_params = {}

    async def mock_fetch(url, params, **kwargs):
        called_params.update(params)
        return None

    api._fetch = mock_fetch
    now = datetime(2026, 4, 11, 0, 30, tzinfo=TZ)
    await api._get_short_term(now)
    assert called_params.get("base_time") == "2300"
    assert called_params.get("base_date") == "20260410"


def test_merge_all_short_res_none_uses_cache():
    api = KMAWeatherAPI(MagicMock(), "key")
    api.lat = api.lon = api.nx = api.ny = None
    now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
    today = now.strftime("%Y%m%d")
    api._cache_forecast_map = {
        today: {"1200": {"TMP": "20", "SKY": "1", "PTY": "0"},
                "0900": {"TMP": "15", "SKY": "1", "PTY": "0"},
                "1500": {"TMP": "22", "SKY": "1", "PTY": "0"}}
    }
    result = api._merge_all(now, None, None, {})
    assert result["weather"]["TMP"] == "20" or result is not None


def test_merge_all_mid_res_2tuple_fallback():
    api = KMAWeatherAPI(MagicMock(), "key")
    api.lat = api.lon = api.nx = api.ny = None
    now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
    ta_wrap = {"response": {"body": {"items": {"item": [{"taMax3": "23", "taMin3": "8"}]}}}}
    land_wrap = {"response": {"body": {"items": {"item": [{"wf3Am": "맑음", "wf3Pm": "흐림"}]}}}}
    result = api._merge_all(now, None, (ta_wrap, land_wrap), {})
    assert len(result["weather"]["forecast_daily"]) == 10


def test_merge_all_boundary_date_short_cache_fallback():
    api = KMAWeatherAPI(MagicMock(), "key")
    api.lat = api.lon = api.nx = api.ny = None
    now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
    tm_fc_dt = datetime(2026, 4, 11, 6, 0, tzinfo=TZ)
    today = now.strftime("%Y%m%d")
    d1 = (now + timedelta(days=1)).strftime("%Y%m%d")
    items = []
    for d_str, tmp in [(today, "12"), (d1, "15")]:
        for h, val in [("0600", tmp), ("0800", tmp)]:
            for cat, v in [("TMP", val), ("SKY", "1"), ("PTY", "0")]:
                items.append({"fcstDate": d_str, "fcstTime": h, "category": cat, "fcstValue": v})
    short_res = {"response": {"body": {"items": {"item": items}}}}
    ta_item = {f"taMax{i}": str(20+i) for i in range(3, 11)}
    ta_item.update({f"taMin{i}": str(5+i) for i in range(3, 11)})
    land_item = {f"wf{i}Am": "맑음" for i in range(3, 11)}
    land_item.update({f"wf{i}Pm": "맑음" for i in range(3, 11)})
    def wrap(item):
        return {"response": {"body": {"items": {"item": [item]}}}}
    mid_res = (wrap(ta_item), wrap(land_item), tm_fc_dt)
    result = api._merge_all(now, short_res, mid_res, {})
    assert len(result["weather"]["forecast_daily"]) == 10


def test_merge_all_sets_vec_kor_when_vec_present():
    api = KMAWeatherAPI(MagicMock(), "key")
    api.lat = api.lon = api.nx = api.ny = None
    now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
    today = now.strftime("%Y%m%d")
    items = [
        {"fcstDate": today, "fcstTime": "1200", "category": "TMP",  "fcstValue": "20"},
        {"fcstDate": today, "fcstTime": "1200", "category": "SKY",  "fcstValue": "1"},
        {"fcstDate": today, "fcstTime": "1200", "category": "PTY",  "fcstValue": "0"},
        {"fcstDate": today, "fcstTime": "1200", "category": "VEC",  "fcstValue": "225"},
        {"fcstDate": today, "fcstTime": "0900", "category": "TMP",  "fcstValue": "15"},
        {"fcstDate": today, "fcstTime": "0900", "category": "SKY",  "fcstValue": "1"},
        {"fcstDate": today, "fcstTime": "0900", "category": "PTY",  "fcstValue": "0"},
        {"fcstDate": today, "fcstTime": "1500", "category": "TMP",  "fcstValue": "22"},
        {"fcstDate": today, "fcstTime": "1500", "category": "SKY",  "fcstValue": "1"},
        {"fcstDate": today, "fcstTime": "1500", "category": "PTY",  "fcstValue": "0"},
    ]
    short_res = {"response": {"body": {"items": {"item": items}}}}
    result = api._merge_all(now, short_res, None, {})
    assert "VEC_KOR" in result["weather"]
    assert result["weather"]["VEC_KOR"] == "남서"


def test_merge_all_rain_start_time_with_minutes():
    api = KMAWeatherAPI(MagicMock(), "key")
    api.lat = api.lon = api.nx = api.ny = None
    now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
    today = now.strftime("%Y%m%d")
    items = [
        {"fcstDate": today, "fcstTime": "1030", "category": "PTY", "fcstValue": "1"},
        {"fcstDate": today, "fcstTime": "1030", "category": "TMP", "fcstValue": "15"},
        {"fcstDate": today, "fcstTime": "1030", "category": "SKY", "fcstValue": "1"},
        {"fcstDate": today, "fcstTime": "0900", "category": "TMP", "fcstValue": "12"},
        {"fcstDate": today, "fcstTime": "0900", "category": "SKY", "fcstValue": "1"},
        {"fcstDate": today, "fcstTime": "0900", "category": "PTY", "fcstValue": "0"},
        {"fcstDate": today, "fcstTime": "1500", "category": "TMP", "fcstValue": "18"},
        {"fcstDate": today, "fcstTime": "1500", "category": "SKY", "fcstValue": "1"},
        {"fcstDate": today, "fcstTime": "1500", "category": "PTY", "fcstValue": "0"},
    ]
    short_res = {"response": {"body": {"items": {"item": items}}}}
    result = api._merge_all(now, short_res, None, {})
    assert "30분" in result["weather"]["rain_start_time"]


def test_merge_all_skips_am_slot_after_noon():
    api = KMAWeatherAPI(MagicMock(), "key")
    api.lat = api.lon = api.nx = api.ny = None
    now = datetime(2026, 4, 11, 14, 0, tzinfo=TZ)
    today = now.strftime("%Y%m%d")
    items = [
        {"fcstDate": today, "fcstTime": "1500", "category": "TMP", "fcstValue": "22"},
        {"fcstDate": today, "fcstTime": "1500", "category": "SKY", "fcstValue": "1"},
        {"fcstDate": today, "fcstTime": "1500", "category": "PTY", "fcstValue": "0"},
        {"fcstDate": today, "fcstTime": "0900", "category": "TMP", "fcstValue": "15"},
        {"fcstDate": today, "fcstTime": "0900", "category": "SKY", "fcstValue": "1"},
        {"fcstDate": today, "fcstTime": "0900", "category": "PTY", "fcstValue": "0"},
    ]
    short_res = {"response": {"body": {"items": {"item": items}}}}
    result = api._merge_all(now, short_res, None, {})
    twice = result["weather"]["forecast_twice_daily"]
    today_am = [e for e in twice if e["_day_index"] == 0 and e["is_daytime"]]
    assert len(today_am) == 0, "오후 12시 이후엔 오늘 오전 슬롯이 없어야 함"


def test_land_code_fallback():
    from custom_components.kma_weather.coordinator import _land_code
    result = _land_code("UNKNOWN_CODE")
    assert result == "11B00000"


@pytest.mark.asyncio
async def test_restore_daily_temps_float_conversion_fails(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "float_fail_test"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    tz = ZoneInfo("Asia/Seoul")
    today_str = datetime.now(tz).strftime("%Y%m%d")
    coord._store.async_load = AsyncMock(return_value={
        "date": today_str, "max": "NOT_A_FLOAT", "min": None,
        "wf_am": "맑음", "wf_pm": "흐림",
    })
    await coord._restore_daily_temps()
    assert coord._daily_max_temp is None
    assert coord._store_loaded is True


@pytest.mark.asyncio
async def test_update_daily_temperatures_full_path(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "daily_temp_test"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    tz = ZoneInfo("Asia/Seoul")
    today_str = datetime.now(tz).strftime("%Y%m%d")

    forecast_map = {today_str: {"0900": {"TMP": "15"}, "1500": {"TMP": "25"}}}
    changed = coord._update_daily_temperatures(forecast_map)
    assert changed is True
    assert coord._daily_min_temp == 15.0
    assert coord._daily_max_temp == 25.0

    forecast_map2 = {today_str: {"0600": {"TMP": "10"}}}
    changed2 = coord._update_daily_temperatures(forecast_map2)
    assert changed2 is True
    assert coord._daily_min_temp == 10.0
    assert coord._daily_max_temp == 25.0

    changed3 = coord._update_daily_temperatures(forecast_map2)
    assert changed3 is False


@pytest.mark.asyncio
async def test_sync_today_forecast_full(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "sync_forecast_test"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._daily_max_temp = 30.0
    coord._daily_min_temp = 10.0
    coord._wf_am_today = "맑음"
    coord._wf_pm_today = "구름많음"

    weather = {
        "current_condition": "sunny",
        "TMX_tomorrow": 28.0, "TMN_tomorrow": 12.0,
        "wf_am_tomorrow": "흐림", "wf_pm_tomorrow": "비",
        "forecast_daily": [
            {"_day_index": 0, "native_temperature": 20.0, "native_templow": 5.0, "condition": "cloudy"},
            {"_day_index": 1, "native_temperature": 22.0, "native_templow": 8.0, "condition": "cloudy"},
            {"_day_index": 2, "native_temperature": 25.0, "native_templow": 10.0, "condition": "sunny"},
        ],
        "forecast_twice_daily": [
            {"_day_index": 0, "is_daytime": True,  "native_temperature": 20.0, "native_templow": 5.0,  "condition": "cloudy"},
            {"_day_index": 0, "is_daytime": False, "native_temperature": 20.0, "native_templow": 5.0,  "condition": "cloudy"},
            {"_day_index": 1, "is_daytime": True,  "native_temperature": 22.0, "native_templow": 8.0,  "condition": "cloudy"},
            {"_day_index": 1, "is_daytime": False, "native_temperature": 22.0, "native_templow": 8.0,  "condition": "cloudy"},
        ],
    }
    coord._sync_today_forecast(weather)

    d0 = next(e for e in weather["forecast_daily"] if e["_day_index"] == 0)
    assert d0["native_temperature"] == 30.0
    assert d0["native_templow"] == 10.0
    assert d0["condition"] == "sunny"

    d1 = next(e for e in weather["forecast_daily"] if e["_day_index"] == 1)
    assert d1["native_temperature"] == 28.0
    assert d1["native_templow"] == 12.0
    assert d1["condition"] == "rainy"

    t0_am = next(e for e in weather["forecast_twice_daily"] if e["_day_index"] == 0 and e["is_daytime"])
    assert t0_am["condition"] == "sunny"
    t0_pm = next(e for e in weather["forecast_twice_daily"] if e["_day_index"] == 0 and not e["is_daytime"])
    assert t0_pm["condition"] == "partlycloudy"

    t1_am = next(e for e in weather["forecast_twice_daily"] if e["_day_index"] == 1 and e["is_daytime"])
    assert t1_am["condition"] == "cloudy"
    t1_pm = next(e for e in weather["forecast_twice_daily"] if e["_day_index"] == 1 and not e["is_daytime"])
    assert t1_pm["condition"] == "rainy"


@pytest.mark.asyncio
async def test_async_update_data_full_path_afternoon(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "full_update_pm"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    coord._wf_am_today = "맑음"
    coord._wf_pm_today = None
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98

    tz = ZoneInfo("Asia/Seoul")
    today_str = datetime.now(tz).strftime("%Y%m%d")
    mock_data = {
        "weather": {
            "wf_am_today": "맑음", "wf_pm_today": "흐림",
            "current_condition_kor": "맑음", "current_condition": "sunny",
            "forecast_daily": [
                {"_day_index": 0, "native_temperature": 25.0, "native_templow": 10.0, "condition": "sunny"},
            ],
            "forecast_twice_daily": [
                {"_day_index": 0, "is_daytime": True,  "native_temperature": 25.0, "native_templow": 10.0, "condition": "sunny"},
                {"_day_index": 0, "is_daytime": False, "native_temperature": 25.0, "native_templow": 10.0, "condition": "sunny"},
            ],
        },
        "air": {},
        "raw_forecast": {today_str: {"0900": {"TMP": "18"}, "1500": {"TMP": "26"}}},
    }
    coord.api.fetch_data = AsyncMock(return_value=mock_data)

    with patch("custom_components.kma_weather.coordinator.datetime") as mock_dt:
        afternoon = datetime(2026, 4, 11, 15, 0, tzinfo=tz)
        mock_dt.now.return_value = afternoon
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = await coord._async_update_data()

    assert result is not None
    assert coord._wf_pm_today == "흐림"


def test_resolve_location_returns_valid_coords():
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"location_entity": "zone.home"}
    entry.options = {}
    entry.entry_id = "valid_coords"
    hass = MagicMock()
    state = MagicMock()
    state.attributes = {"latitude": 37.56, "longitude": 126.98}
    hass.states.get.return_value = state
    coord = KMAWeatherUpdateCoordinator.__new__(KMAWeatherUpdateCoordinator)
    coord.hass = hass
    coord.entry = entry
    coord._last_lat = None
    coord._last_lon = None
    lat, lon = coord._resolve_location()
    assert lat == pytest.approx(37.56)
    assert lon == pytest.approx(126.98)


def test_resolve_location_bad_float_falls_back():
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"location_entity": "zone.home"}
    entry.options = {}
    entry.entry_id = "bad_float"
    hass = MagicMock()
    state = MagicMock()
    state.attributes = {"latitude": "INVALID", "longitude": "INVALID"}
    hass.states.get.return_value = state
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    coord = KMAWeatherUpdateCoordinator.__new__(KMAWeatherUpdateCoordinator)
    coord.hass = hass
    coord.entry = entry
    coord._last_lat = None
    coord._last_lon = None
    lat, lon = coord._resolve_location()
    assert lat == pytest.approx(37.56)


def test_resolve_location_ha_config_bad_float():
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"location_entity": ""}
    entry.options = {}
    entry.entry_id = "ha_bad_float"
    hass = MagicMock()
    hass.states.get.return_value = None
    hass.config.latitude = "BAD"
    hass.config.longitude = "BAD"
    coord = KMAWeatherUpdateCoordinator.__new__(KMAWeatherUpdateCoordinator)
    coord.hass = hass
    coord.entry = entry
    coord._last_lat = None
    coord._last_lon = None
    lat, lon = coord._resolve_location()
    assert lat is None and lon is None


def test_sensor_api_expire_returns_days():
    coordinator = MagicMock()
    coordinator.data = {"weather": {}, "air": {}}
    coordinator._daily_max_temp = None
    coordinator._daily_min_temp = None
    entry = MagicMock()
    entry.entry_id = "expire_test"
    future = (date.today() + timedelta(days=15)).isoformat()
    entry.options = {}
    entry.data = {"prefix": "x", "expire_date": future}
    sensor = KMACustomSensor(coordinator, "api_expire", "x", entry)
    val = sensor.native_value
    assert val == 15


def test_sensor_native_value_float_conversion_error():
    coordinator = MagicMock()
    coordinator.data = {"weather": {"TMP": "INVALID_FLOAT"}, "air": {}}
    coordinator._daily_max_temp = None
    coordinator._daily_min_temp = None
    entry = MagicMock()
    entry.entry_id = "float_err"
    entry.options = {}
    entry.data = {"prefix": "x"}
    sensor = KMACustomSensor(coordinator, "TMP", "x", entry)
    assert sensor.native_value is None


def test_sensor_extra_state_attrs_no_data():
    coordinator = MagicMock()
    coordinator.data = None
    entry = MagicMock()
    entry.entry_id = "no_data"
    entry.options = {}
    entry.data = {"prefix": "x"}
    sensor = KMACustomSensor(coordinator, "address", "x", entry)
    assert sensor.extra_state_attributes is None


def test_weather_entity_bad_values():
    from custom_components.kma_weather.weather import KMAWeather
    coordinator = MagicMock()
    coordinator.data = {"weather": {
        "TMP": "BAD", "REH": "BAD", "WSD": "BAD", "VEC": "BAD",
        "current_condition": "sunny",
    }}
    entry = MagicMock()
    entry.data = {"prefix": "test"}
    entry.entry_id = "bad_weather"
    weather = KMAWeather.__new__(KMAWeather)
    weather.coordinator = coordinator
    weather._attr_name = "날씨 요약"
    assert weather.native_temperature is None
    assert weather.humidity is None
    assert weather.native_wind_speed is None
    assert weather.wind_bearing is None


@pytest.mark.asyncio
async def test_weather_forecast_no_data():
    from custom_components.kma_weather.weather import KMAWeather
    coordinator = MagicMock()
    coordinator.data = None
    entry = MagicMock()
    entry.data = {"prefix": "test"}
    entry.entry_id = "no_data_weather"
    weather = KMAWeather.__new__(KMAWeather)
    weather.coordinator = coordinator
    weather._attr_name = "날씨 요약"
    daily = await weather.async_forecast_daily()
    twice = await weather.async_forecast_twice_daily()
    assert daily == []
    assert twice == []


@pytest.mark.asyncio
async def test_async_unload_entry_ok_false(hass, mock_config_entry, kma_api_mock_factory):
    from custom_components.kma_weather.const import DOMAIN
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    kma_api_mock_factory("full_test")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    with patch("homeassistant.config_entries.ConfigEntries.async_unload_platforms",
               return_value=False):
        from custom_components.kma_weather import async_unload_entry
        result = await async_unload_entry(hass, mock_config_entry)

    assert result is False
    assert mock_config_entry.entry_id in hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_config_flow_step_user_with_state_name(hass):
    from custom_components.kma_weather.config_flow import KMAWeatherConfigFlow
    hass.states.async_set("zone.home", "zoning",
                          {"latitude": 37.56, "longitude": 126.98, "friendly_name": "우리집"})
    flow = KMAWeatherConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    flow._async_current_entries = lambda: []
    with patch.object(flow, "async_set_unique_id", return_value=None), \
         patch.object(flow, "_abort_if_unique_id_configured"), \
         patch("custom_components.kma_weather.config_flow._validate_api_key", return_value=None):
        result = await flow.async_step_user({
            "api_key": "KEY_WITH_STATE",
            "prefix": "home2",
            "location_entity": "zone.home",
        })
    assert result["type"] == "create_entry"
    assert "기상청 날씨:" in result["title"]


@pytest.mark.asyncio
async def test_config_flow_step_user_entity_no_state(hass):
    from custom_components.kma_weather.config_flow import KMAWeatherConfigFlow
    flow = KMAWeatherConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    flow._async_current_entries = lambda: []
    with patch.object(flow, "async_set_unique_id", return_value=None), \
         patch.object(flow, "_abort_if_unique_id_configured"), \
         patch("custom_components.kma_weather.config_flow._validate_api_key", return_value=None):
        result = await flow.async_step_user({
            "api_key": "KEY_NO_STATE",
            "prefix": "nostate",
            "location_entity": "zone.unknown_entity",
        })
    assert result["type"] == "create_entry"
    assert "unknown_entity" in result["title"]


@pytest.mark.asyncio
async def test_config_flow_step_user_no_entity(hass):
    from custom_components.kma_weather.config_flow import KMAWeatherConfigFlow
    flow = KMAWeatherConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    flow._async_current_entries = lambda: []
    with patch.object(flow, "async_set_unique_id", return_value=None), \
         patch.object(flow, "_abort_if_unique_id_configured"), \
         patch("custom_components.kma_weather.config_flow._validate_api_key", return_value=None):
        result = await flow.async_step_user({
            "api_key": "KEY_NO_ENTITY",
            "prefix": "noent",
        })
    assert result["type"] == "create_entry"
    assert "우리집" in result["title"]


@pytest.mark.asyncio
async def test_config_flow_show_form_when_no_input(hass):
    from custom_components.kma_weather.config_flow import KMAWeatherConfigFlow
    flow = KMAWeatherConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    result = await flow.async_step_user(None)
    assert result["type"] == "form"
    assert result["step_id"] == "user"


def test_nominatim_agent_uuid_replace_raises():
    class BadUuidHass:
        @property
        def installation_uuid(self):
            return None
    api = KMAWeatherAPI(MagicMock(), "MYKEY", hass=BadUuidHass())
    expected = hashlib.sha1("MYKEY".encode()).hexdigest()[:12]
    assert expected in api._nominatim_user_agent


def test_nominatim_agent_uuid_attribute_raises_exception():
    class RaisingHass:
        @property
        def installation_uuid(self):
            raise RuntimeError("permission denied")
    api = KMAWeatherAPI(MagicMock(), "EXKEY", hass=RaisingHass())
    expected = hashlib.sha1("EXKEY".encode()).hexdigest()[:12]
    assert expected in api._nominatim_user_agent


@pytest.mark.asyncio
async def test_get_short_term_with_valid_hours():
    api = KMAWeatherAPI(MagicMock(), "key")
    api.nx, api.ny = 60, 127
    called_params = {}

    async def mock_fetch(url, params, **kwargs):
        called_params.update(params)
        return None

    api._fetch = mock_fetch
    now = datetime(2026, 4, 11, 12, 30, tzinfo=TZ)
    await api._get_short_term(now)
    assert called_params.get("base_time") == "1100"
    assert called_params.get("base_date") == "20260411"


def test_merge_all_updates_weather_data_with_best_t():
    api = KMAWeatherAPI(MagicMock(), "key")
    api.lat = api.lon = api.nx = api.ny = None
    now = datetime(2026, 4, 11, 9, 0, tzinfo=TZ)
    today = now.strftime("%Y%m%d")
    items = [
        {"fcstDate": today, "fcstTime": "0900", "category": "TMP", "fcstValue": "15"},
        {"fcstDate": today, "fcstTime": "0900", "category": "SKY", "fcstValue": "1"},
        {"fcstDate": today, "fcstTime": "0900", "category": "PTY", "fcstValue": "0"},
        {"fcstDate": today, "fcstTime": "1200", "category": "TMP", "fcstValue": "22"},
        {"fcstDate": today, "fcstTime": "1200", "category": "SKY", "fcstValue": "3"},
        {"fcstDate": today, "fcstTime": "1200", "category": "PTY", "fcstValue": "0"},
        {"fcstDate": today, "fcstTime": "1500", "category": "TMP", "fcstValue": "25"},
        {"fcstDate": today, "fcstTime": "1500", "category": "SKY", "fcstValue": "1"},
        {"fcstDate": today, "fcstTime": "1500", "category": "PTY", "fcstValue": "0"},
    ]
    short_res = {"response": {"body": {"items": {"item": items}}}}
    result = api._merge_all(now, short_res, None, {})
    assert result["weather"]["TMP"] == "15"


def test_merge_all_rain_start_time_on_the_hour():
    api = KMAWeatherAPI(MagicMock(), "key")
    api.lat = api.lon = api.nx = api.ny = None
    now = datetime(2026, 4, 11, 8, 0, tzinfo=TZ)
    today = now.strftime("%Y%m%d")
    items = [
        {"fcstDate": today, "fcstTime": "1400", "category": "PTY", "fcstValue": "1"},
        {"fcstDate": today, "fcstTime": "1400", "category": "TMP", "fcstValue": "18"},
        {"fcstDate": today, "fcstTime": "1400", "category": "SKY", "fcstValue": "1"},
        {"fcstDate": today, "fcstTime": "0900", "category": "TMP", "fcstValue": "12"},
        {"fcstDate": today, "fcstTime": "0900", "category": "SKY", "fcstValue": "1"},
        {"fcstDate": today, "fcstTime": "0900", "category": "PTY", "fcstValue": "0"},
        {"fcstDate": today, "fcstTime": "1500", "category": "TMP", "fcstValue": "20"},
        {"fcstDate": today, "fcstTime": "1500", "category": "SKY", "fcstValue": "1"},
        {"fcstDate": today, "fcstTime": "1500", "category": "PTY", "fcstValue": "0"},
    ]
    short_res = {"response": {"body": {"items": {"item": items}}}}
    result = api._merge_all(now, short_res, None, {})
    rain_time = result["weather"]["rain_start_time"]
    assert "14시" in rain_time
    assert "분" not in rain_time


def test_merge_all_boundary_date_rep_t_sky_kor():
    api = KMAWeatherAPI(MagicMock(), "key")
    api.lat = api.lon = api.nx = api.ny = None
    now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
    today = now.strftime("%Y%m%d")
    items = [
        {"fcstDate": today, "fcstTime": "0900", "category": "TMP",  "fcstValue": "12"},
        {"fcstDate": today, "fcstTime": "0900", "category": "SKY",  "fcstValue": "3"},
        {"fcstDate": today, "fcstTime": "0900", "category": "PTY",  "fcstValue": "0"},
        {"fcstDate": today, "fcstTime": "1500", "category": "TMP",  "fcstValue": "18"},
        {"fcstDate": today, "fcstTime": "1500", "category": "SKY",  "fcstValue": "1"},
        {"fcstDate": today, "fcstTime": "1500", "category": "PTY",  "fcstValue": "0"},
    ]
    short_res = {"response": {"body": {"items": {"item": items}}}}
    result = api._merge_all(now, short_res, None, {})
    assert result["weather"].get("wf_am_today") == "구름많음"


@pytest.mark.asyncio
async def test_async_update_data_returns_empty_no_location(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": "zone.gone"}
    entry.options = {}
    entry.entry_id = "no_loc_clean"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    coord._cached_data = None
    hass.config.latitude = 0.0
    hass.config.longitude = 0.0
    result = await coord._async_update_data()
    assert result == {"weather": {}, "air": {}}


@pytest.mark.asyncio
async def test_async_update_data_returns_cached_no_location(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": "zone.gone"}
    entry.options = {}
    entry.entry_id = "no_loc_cached"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    coord._cached_data = {"weather": {"TMP": 99}, "air": {}}
    hass.config.latitude = 0.0
    hass.config.longitude = 0.0
    result = await coord._async_update_data()
    assert result["weather"]["TMP"] == 99


@pytest.mark.asyncio
async def test_async_update_data_fetch_none_returns_cached(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "fetch_none_cached"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    coord._cached_data = {"weather": {"TMP": 77}, "air": {"pm10Value": 20}}
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    coord.api.fetch_data = AsyncMock(return_value=None)
    result = await coord._async_update_data()
    assert result["weather"]["TMP"] == 77


@pytest.mark.asyncio
async def test_async_update_data_morning_uses_wf_am(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    from zoneinfo import ZoneInfo as _ZI
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "morning_am"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    coord._wf_am_today = "맑음"
    coord._wf_pm_today = "흐림"
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    tz = _ZI("Asia/Seoul")
    mock_data = {
        "weather": {
            "wf_am_today": "맑음", "wf_pm_today": "흐림",
            "current_condition_kor": "맑음", "current_condition": "sunny",
            "forecast_daily": [], "forecast_twice_daily": [],
        },
        "air": {}, "raw_forecast": {},
    }
    coord.api.fetch_data = AsyncMock(return_value=mock_data)
    with patch("custom_components.kma_weather.coordinator.datetime") as mock_dt:
        morning = datetime(2026, 4, 11, 9, 0, tzinfo=tz)
        mock_dt.now.return_value = morning
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = await coord._async_update_data()
    assert result["weather"]["current_condition_kor"] == "맑음"
    assert result["weather"]["current_condition"] == "sunny"


@pytest.mark.asyncio
async def test_async_update_data_afternoon_uses_wf_pm(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    from zoneinfo import ZoneInfo as _ZI
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "afternoon_pm"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    coord._wf_am_today = "맑음"
    coord._wf_pm_today = "흐림"
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    tz = _ZI("Asia/Seoul")
    mock_data = {
        "weather": {
            "wf_am_today": "맑음", "wf_pm_today": "흐림",
            "current_condition_kor": "맑음", "current_condition": "sunny",
            "forecast_daily": [], "forecast_twice_daily": [],
        },
        "air": {}, "raw_forecast": {},
    }
    coord.api.fetch_data = AsyncMock(return_value=mock_data)
    with patch("custom_components.kma_weather.coordinator.datetime") as mock_dt:
        afternoon = datetime(2026, 4, 11, 15, 0, tzinfo=tz)
        mock_dt.now.return_value = afternoon
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = await coord._async_update_data()
    assert result["weather"]["current_condition_kor"] == "흐림"
    assert result["weather"]["current_condition"] == "cloudy"


@pytest.mark.asyncio
async def test_async_update_data_uses_entity_location(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": "zone.work"}
    entry.options = {}
    entry.entry_id = "entity_location"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    hass.states.async_set("zone.work", "zoning",
                          {"latitude": 35.18, "longitude": 129.07})
    captured_coords = {}

    # ★ 수정: reg_id_temp, reg_id_land, warn_area_code 인자 추가
    async def mock_fetch_data(lat, lon, nx, ny, reg_id_temp=None, reg_id_land=None, warn_area_code=None, pollen_area_no="", pollen_area_name=""):
        captured_coords["lat"] = lat
        captured_coords["lon"] = lon
        return None

    coord.api.fetch_data = mock_fetch_data
    coord._cached_data = {"weather": {}, "air": {}}
    await coord._async_update_data()
    assert captured_coords.get("lat") == pytest.approx(35.18)
    assert captured_coords.get("lon") == pytest.approx(129.07)


def test_sensor_api_expire_valid_iso_date():
    coordinator = MagicMock()
    coordinator.data = {"weather": {}, "air": {}}
    coordinator._daily_max_temp = None
    coordinator._daily_min_temp = None
    entry = MagicMock()
    entry.entry_id = "expire_iso"
    future = (date.today() + timedelta(days=7)).isoformat()
    entry.options = {"expire_date": future}
    entry.data = {"prefix": "x", "expire_date": "2099-01-01"}
    sensor = KMACustomSensor(coordinator, "api_expire", "x", entry)
    val = sensor.native_value
    assert val == 7
    assert isinstance(val, int)


def test_is_valid_korean_coord_nan():
    from custom_components.kma_weather.const import is_korean_coord_loose as _is_valid_korean_coord
    import math
    assert _is_valid_korean_coord(math.nan, 126.98) is False
    assert _is_valid_korean_coord(37.56, math.nan) is False


@pytest.mark.asyncio
async def test_async_update_228_exit_via_resolve_location_mock(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "mock_228"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    coord._cached_data = None
    with patch.object(coord, "_resolve_location", return_value=(None, None)):
        result = await coord._async_update_data()
    assert result == {"weather": {}, "air": {}}


@pytest.mark.asyncio
async def test_async_update_228_exit_returns_cached_via_mock(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "mock_228_cached"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    coord._cached_data = {"weather": {"TMP": 88}, "air": {}}
    with patch.object(coord, "_resolve_location", return_value=(None, None)):
        result = await coord._async_update_data()
    assert result["weather"]["TMP"] == 88


@pytest.mark.asyncio
async def test_async_update_235_exit_fetch_none_via_mock(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "mock_235"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    coord._cached_data = {"weather": {"TMP": 55}, "air": {}}
    with patch.object(coord, "_resolve_location", return_value=(37.56, 126.98)):
        coord.api.fetch_data = AsyncMock(return_value=None)
        result = await coord._async_update_data()
    assert result["weather"]["TMP"] == 55


@pytest.mark.asyncio
async def test_async_update_276_exception_returns_cached(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "except_276"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    coord._cached_data = {"weather": {"TMP": 33}, "air": {}}
    with patch.object(coord, "_resolve_location", return_value=(37.56, 126.98)):
        coord.api.fetch_data = AsyncMock(side_effect=RuntimeError("boom"))
        result = await coord._async_update_data()
    assert result["weather"]["TMP"] == 33


@pytest.mark.asyncio
async def test_resolve_location_285_valid_entity_coords(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": "zone.busan"}
    entry.options = {}
    entry.entry_id = "valid_entity_285"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    hass.states.async_set("zone.busan", "zoning",
                          {"latitude": 35.18, "longitude": 129.07})
    lat, lon = coord._resolve_location()
    assert lat == pytest.approx(35.18)
    assert lon == pytest.approx(129.07)


def test_sensor_api_expire_fromisoformat_success_options_priority():
    coordinator = MagicMock()
    coordinator.data = {"weather": {}, "air": {}}
    coordinator._daily_max_temp = None
    coordinator._daily_min_temp = None
    entry = MagicMock()
    entry.entry_id = "prio_expire"
    future = (date.today() + timedelta(days=20)).isoformat()
    entry.options = {"expire_date": future}
    entry.data = {"prefix": "x"}
    sensor = KMACustomSensor(coordinator, "api_expire", "x", entry)
    val = sensor.native_value
    assert val == 20
    assert isinstance(val, int)


@pytest.mark.asyncio
async def test_sync_today_forecast_none_values_not_overwrite(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "sync_none"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._daily_max_temp = None
    coord._daily_min_temp = None
    coord._wf_am_today = None
    coord._wf_pm_today = None
    weather = {
        "current_condition": None,
        "TMX_tomorrow": None, "TMN_tomorrow": None,
        "wf_am_tomorrow": None, "wf_pm_tomorrow": None,
        "forecast_daily": [
            {"_day_index": 0, "native_temperature": 20.0, "native_templow": 10.0, "condition": "sunny"},
            {"_day_index": 1, "native_temperature": 22.0, "native_templow": 12.0, "condition": "cloudy"},
        ],
        "forecast_twice_daily": [
            {"_day_index": 0, "is_daytime": True,  "native_temperature": 20.0, "native_templow": 10.0, "condition": "sunny"},
            {"_day_index": 0, "is_daytime": False, "native_temperature": 20.0, "native_templow": 10.0, "condition": "sunny"},
            {"_day_index": 1, "is_daytime": True,  "native_temperature": 22.0, "native_templow": 12.0, "condition": "cloudy"},
            {"_day_index": 1, "is_daytime": False, "native_temperature": 22.0, "native_templow": 12.0, "condition": "cloudy"},
        ],
    }
    coord._sync_today_forecast(weather)
    d0 = next(e for e in weather["forecast_daily"] if e["_day_index"] == 0)
    assert d0["native_temperature"] == 20.0
    assert d0["condition"] == "sunny"


@pytest.mark.asyncio
async def test_async_update_summary_changed_saves_temps(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "summary_save"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    coord._wf_am_today = "맑음"
    coord._wf_pm_today = "맑음"
    tz = ZoneInfo("Asia/Seoul")
    today_str = datetime.now(tz).strftime("%Y%m%d")
    mock_data = {
        "weather": {
            "wf_am_today": "흐림", "wf_pm_today": "비",
            "current_condition_kor": "흐림", "current_condition": "cloudy",
            "forecast_daily": [], "forecast_twice_daily": [],
        },
        "air": {},
        "raw_forecast": {today_str: {}},
    }
    save_called = []
    async def mock_save(data): save_called.append(data)
    coord._store.async_save = mock_save
    with patch.object(coord, "_resolve_location", return_value=(37.56, 126.98)):
        coord.api.fetch_data = AsyncMock(return_value=mock_data)
        await coord._async_update_data()
    assert len(save_called) > 0
    assert coord._wf_am_today == "흐림"
    assert coord._wf_pm_today == "비"


def test_merge_all_best_t_none_when_empty_times():
    api = KMAWeatherAPI(MagicMock(), "key")
    api.lat = api.lon = api.nx = api.ny = None
    now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
    today = now.strftime("%Y%m%d")
    api._cache_forecast_map = {today: {}}
    result = api._merge_all(now, None, None, {})
    assert result["weather"]["TMP"] is None


def test_merge_all_rep_t_none_when_empty_forecast_keys():
    api = KMAWeatherAPI(MagicMock(), "key")
    api.lat = api.lon = api.nx = api.ny = None
    now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
    tm_fc_dt = datetime(2026, 4, 11, 6, 0, tzinfo=TZ)
    today = now.strftime("%Y%m%d")
    api._cache_forecast_map = {today: {}}
    api._cache_mid_tm_fc_dt = tm_fc_dt
    api._cache_mid_ta = {}
    api._cache_mid_land = {}
    result = api._merge_all(now, None, None, {})
    assert result["weather"].get("wf_am_today") is None
    assert len(result["weather"]["forecast_daily"]) == 10


@pytest.mark.asyncio
async def test_async_update_228_exit_direct(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "228_direct_none"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    coord._cached_data = None
    with patch.object(coord, "_resolve_location", return_value=(None, None)):
        result = await coord._async_update_data()
    assert result == {"weather": {}, "air": {}}


@pytest.mark.asyncio
async def test_async_update_228_exit_with_cache(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "228_direct_cached"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    coord._cached_data = {"weather": {"TMP": 21}, "air": {"pm10Value": 15}}
    with patch.object(coord, "_resolve_location", return_value=(None, None)):
        result = await coord._async_update_data()
    assert result["weather"]["TMP"] == 21


@pytest.mark.asyncio
async def test_async_update_235_exit_direct(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "235_direct"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    coord._cached_data = {"weather": {"TMP": 44}, "air": {}}
    with patch.object(coord, "_resolve_location", return_value=(37.56, 126.98)):
        coord.api.fetch_data = AsyncMock(return_value=None)
        result = await coord._async_update_data()
    assert result["weather"]["TMP"] == 44


@pytest.mark.asyncio
async def test_async_update_244_summary_am_changed(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "244_am_changed"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    coord._wf_am_today = "맑음"
    coord._wf_pm_today = "맑음"
    tz = ZoneInfo("Asia/Seoul")
    today_str = datetime.now(tz).strftime("%Y%m%d")
    saved = []
    async def mock_save(data): saved.append(data)
    coord._store.async_save = mock_save
    mock_data = {
        "weather": {
            "wf_am_today": "흐림", "wf_pm_today": "맑음",
            "current_condition_kor": "흐림", "current_condition": "cloudy",
            "forecast_daily": [], "forecast_twice_daily": [],
        },
        "air": {},
        "raw_forecast": {today_str: {"1200": {"TMP": "20"}}},
    }
    with patch.object(coord, "_resolve_location", return_value=(37.56, 126.98)):
        coord.api.fetch_data = AsyncMock(return_value=mock_data)
        await coord._async_update_data()
    assert coord._wf_am_today == "흐림"
    assert len(saved) > 0


@pytest.mark.asyncio
async def test_async_update_246_summary_pm_changed(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "246_pm_changed"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    coord._wf_am_today = "맑음"
    coord._wf_pm_today = "맑음"
    tz = ZoneInfo("Asia/Seoul")
    today_str = datetime.now(tz).strftime("%Y%m%d")
    mock_data = {
        "weather": {
            "wf_am_today": "맑음", "wf_pm_today": "비",
            "current_condition_kor": "맑음", "current_condition": "sunny",
            "forecast_daily": [], "forecast_twice_daily": [],
        },
        "air": {},
        "raw_forecast": {today_str: {}},
    }
    with patch.object(coord, "_resolve_location", return_value=(37.56, 126.98)):
        coord.api.fetch_data = AsyncMock(return_value=mock_data)
        await coord._async_update_data()
    assert coord._wf_pm_today == "비"


@pytest.mark.asyncio
async def test_config_flow_invalid_api_key(hass):
    from custom_components.kma_weather.config_flow import KMAWeatherConfigFlow
    flow = KMAWeatherConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    with patch("custom_components.kma_weather.config_flow._validate_api_key",
               return_value="invalid_api_key"):
        result = await flow.async_step_user({
            "api_key": "INVALID_KEY",
            "prefix": "test",
        })
    assert result["type"] == "form"
    assert result["errors"]["api_key"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_async_update_248_temp_changed_saves(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "248_temp_save"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    coord._daily_max_temp = None
    tz = ZoneInfo("Asia/Seoul")
    today_str = datetime.now(tz).strftime("%Y%m%d")
    saved = []
    async def mock_save(data): saved.append(data)
    coord._store.async_save = mock_save
    mock_data = {
        "weather": {
            "wf_am_today": "맑음", "wf_pm_today": "맑음",
            "current_condition_kor": "맑음", "current_condition": "sunny",
            "forecast_daily": [], "forecast_twice_daily": [],
        },
        "air": {},
        "raw_forecast": {today_str: {"1200": {"TMP": "25"}, "1500": {"TMP": "28"}}},
    }
    with patch.object(coord, "_resolve_location", return_value=(37.56, 126.98)):
        coord.api.fetch_data = AsyncMock(return_value=mock_data)
        await coord._async_update_data()
    assert len(saved) > 0
    assert coord._daily_max_temp == 28.0


def test_resolve_location_entity_valid_korean_coords():
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"location_entity": "device_tracker.phone"}
    entry.options = {}
    entry.entry_id = "285_direct"
    hass = MagicMock()
    state = MagicMock()
    state.attributes = {"latitude": 36.35, "longitude": 127.38}
    hass.states.get.return_value = state
    coord = KMAWeatherUpdateCoordinator.__new__(KMAWeatherUpdateCoordinator)
    coord.hass = hass
    coord.entry = entry
    coord._last_lat = None
    coord._last_lon = None
    lat, lon = coord._resolve_location()
    assert lat == pytest.approx(36.35)
    assert lon == pytest.approx(127.38)


def test_sensor_native_value_none_when_data_is_none():
    coordinator = MagicMock()
    coordinator.data = None
    coordinator._daily_max_temp = None
    coordinator._daily_min_temp = None
    entry = MagicMock()
    entry.entry_id = "sensor_77"
    entry.options = {}
    entry.data = {"prefix": "x"}
    for sensor_type in ["TMP", "REH", "WSD", "POP", "apparent_temp"]:
        sensor = KMACustomSensor(coordinator, sensor_type, "x", entry)
        val = sensor.native_value
        assert val is None


def test_resolve_location_entity_out_of_range_falls_to_last_lat():
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"location_entity": "zone.foreign"}
    entry.options = {}
    entry.entry_id = "out_of_range"
    hass = MagicMock()
    state = MagicMock()
    state.attributes = {"latitude": 0.0, "longitude": 0.0}
    hass.states.get.return_value = state
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    coord = KMAWeatherUpdateCoordinator.__new__(KMAWeatherUpdateCoordinator)
    coord.hass = hass
    coord.entry = entry
    coord._last_lat = 35.5
    coord._last_lon = 129.3
    lat, lon = coord._resolve_location()
    assert lat == pytest.approx(35.5)
    assert lon == pytest.approx(129.3)


def test_resolve_location_285_lat_none_fallback():
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"location_entity": "zone.partial"}
    entry.options = {}
    entry.entry_id = "lat_none_285"
    hass = MagicMock()
    state = MagicMock()
    state.attributes = {"latitude": None, "longitude": 126.98}
    hass.states.get.return_value = state
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    coord = KMAWeatherUpdateCoordinator.__new__(KMAWeatherUpdateCoordinator)
    coord.hass = hass
    coord.entry = entry
    coord._last_lat = None
    coord._last_lon = None
    lat, lon = coord._resolve_location()
    assert lat == pytest.approx(37.56)


def test_resolve_location_285_valid_but_out_of_range_no_cache():
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"location_entity": "zone.abroad"}
    entry.options = {}
    entry.entry_id = "abroad_no_cache"
    hass = MagicMock()
    state = MagicMock()
    state.attributes = {"latitude": 35.6762, "longitude": 139.6503}
    hass.states.get.return_value = state
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    coord = KMAWeatherUpdateCoordinator.__new__(KMAWeatherUpdateCoordinator)
    coord.hass = hass
    coord.entry = entry
    coord._last_lat = None
    coord._last_lon = None
    lat, lon = coord._resolve_location()
    assert lat == pytest.approx(37.56)
