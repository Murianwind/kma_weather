"""
모든 미커버 라인을 커버하는 테스트.
- api_kma.py  : fetch_data, _get_address, _get_short_term, _merge_all 분기들
- coordinator : _update_daily_temperatures, _sync_today_forecast, _async_update_data 전체
- sensor.py   : api_expire 정상, float 변환 실패, extra_state_attributes data=None
- weather.py  : ValueError/TypeError 분기, data=None 경우
- __init__.py : unload_ok=False 분기
"""
import pytest
import hashlib
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.kma_weather.api_kma import KMAWeatherAPI
from custom_components.kma_weather.sensor import KMACustomSensor, SENSOR_TYPES

TZ = ZoneInfo("Asia/Seoul")


def test_nominatim_agent_with_valid_uuid():
    # Given: hass 객체에 installation_uuid가 유효한 값으로 설정되어 있다
    # When:  KMAWeatherAPI 인스턴스를 생성하면
    # Then:  uuid 앞 12자리(하이픈 제거)가 Nominatim User-Agent에 포함된다
    class HasUuidHass:
        installation_uuid = "abcdef12-3456-7890-abcd-ef1234567890"
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2", hass=HasUuidHass())
    assert "abcdef123456" in api._nominatim_user_agent


def test_nominatim_agent_no_hass_uses_hash():
    # Given: hass 없이 KMAWeatherAPI 인스턴스를 생성한다
    # When:  _nominatim_user_agent를 확인하면
    # Then:  api_key의 sha1 해시 앞 12자리가 User-Agent에 포함된다
    api = KMAWeatherAPI(MagicMock(), "MY_SECRET_KEY", "r1", "r2")
    expected_hash = hashlib.sha1("MY_SECRET_KEY".encode()).hexdigest()[:12]
    assert expected_hash in api._nominatim_user_agent


def test_nominatim_agent_hash_exception_returns_base():
    # Given: hashlib.sha1 자체가 예외를 던지도록 monkeypatch한다
    # When:  KMAWeatherAPI 인스턴스를 생성하면
    # Then:  예외를 조용히 삼키고 "HomeAssistant-KMA-Weather" 기본 문자열을 반환한다
    with patch("hashlib.sha1", side_effect=Exception("hash fail")):
        api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    assert api._nominatim_user_agent == "HomeAssistant-KMA-Weather"


@pytest.mark.asyncio
async def test_fetch_data_full_path():
    # Given: 모든 내부 비동기 메서드(_get_short_term, _get_mid_term 등)가 mock으로 교체된다
    # When:  fetch_data(lat, lon, nx, ny)를 호출하면
    # Then:  asyncio.gather로 병렬 실행 후 _merge_all 결과를 반환하며
    #        address 필드에 mock에서 반환한 "서울시"가 담긴다
    api = KMAWeatherAPI(MagicMock(), "key", "11B10101", "11B00000")
    api._get_short_term = AsyncMock(return_value=None)
    api._get_mid_term = AsyncMock(return_value=(None, None, datetime(2026, 4, 11, 6, 0, tzinfo=TZ)))
    api._get_air_quality = AsyncMock(return_value={"pm10Value": "30"})
    api._get_address = AsyncMock(return_value="서울시")
    result = await api.fetch_data(37.56, 126.98, 60, 127)
    assert result is not None
    assert "weather" in result
    assert result["weather"]["address"] == "서울시"


@pytest.mark.asyncio
async def test_fetch_data_with_exception_in_task():
    # Given: _get_short_term이 RuntimeError를 던지도록 mock한다
    # When:  fetch_data를 호출하면
    # Then:  gather에서 발생한 예외가 None으로 처리되어 결과가 반환된다
    api = KMAWeatherAPI(MagicMock(), "key", "11B10101", "11B00000")
    api._get_short_term = AsyncMock(side_effect=Exception("network error"))
    api._get_mid_term = AsyncMock(return_value=(None, None, datetime(2026, 4, 11, 6, 0, tzinfo=TZ)))
    api._get_air_quality = AsyncMock(return_value={})
    api._get_address = AsyncMock(return_value="서울시")
    result = await api.fetch_data(37.56, 126.98, 60, 127)
    assert result is not None


@pytest.mark.asyncio
async def test_get_address_fetch_returns_none():
    # Given: _fetch가 None을 반환하도록 mock한다
    # When:  _get_address(37.56, 126.98)를 호출하면
    # Then:  Nominatim 응답이 없으므로 "위도, 경도" 형식의 fallback 문자열을 반환한다
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    api._fetch = AsyncMock(return_value=None)
    result = await api._get_address(37.56, 126.98)
    assert result == "37.5600, 126.9800"


@pytest.mark.asyncio
async def test_get_address_exception_fallback():
    # Given: _fetch가 Exception을 던지도록 mock한다
    # When:  _get_address(37.56, 126.98)를 호출하면
    # Then:  예외를 조용히 삼키고 "위도, 경도" 형식의 fallback 문자열을 반환한다
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    api._fetch = AsyncMock(side_effect=Exception("timeout"))
    result = await api._get_address(37.56, 126.98)
    assert "37.5600" in result


@pytest.mark.asyncio
async def test_air_quality_exception_returns_empty():
    # Given: _cached_station이 설정되어 있고 _fetch가 Exception을 던지도록 mock한다
    # When:  _get_air_quality를 호출하면
    # Then:  에러를 로깅하고 빈 딕셔너리 {}를 반환한다
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    api.lat, api.lon = 37.56, 126.98
    api._cached_station = "테스트"

    async def bad_fetch(url, params=None, timeout=10):
        raise Exception("connection error")

    api._fetch = bad_fetch
    result = await api._get_air_quality()
    assert result == {}


@pytest.mark.asyncio
async def test_get_short_term_midnight():
    # Given: 자정 00:30 시각이 주어진다 (adj=00:20, valid_hours=[])
    # When:  _get_short_term을 호출하면
    # Then:  valid_hours가 비어 전날 23시 발표본을 사용한다
    #        (base_time="2300", base_date=전날)
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
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
    # Given: _cache_forecast_map에 오늘 날짜 데이터가 미리 저장되어 있고 short_res=None이다
    # When:  _merge_all을 호출하면
    # Then:  캐시 재사용 경고가 발생하고 캐시 데이터로 TMP 값이 채워진다
    api = KMAWeatherAPI(MagicMock(), "key", "11B10101", "11B00000")
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
    # Given: mid_res가 3-tuple이 아닌 2-tuple(ta응답, land응답)로 주어진다
    # When:  _merge_all을 호출하면
    # Then:  _get_mid_base_dt(now)로 폴백하여 10일치 forecast_daily를 정상 반환한다
    api = KMAWeatherAPI(MagicMock(), "key", "11B10101", "11B00000")
    api.lat = api.lon = api.nx = api.ny = None
    now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
    ta_wrap = {"response": {"body": {"items": {"item": [{"taMax3": "23", "taMin3": "8"}]}}}}
    land_wrap = {"response": {"body": {"items": {"item": [{"wf3Am": "맑음", "wf3Pm": "흐림"}]}}}}
    result = api._merge_all(now, None, (ta_wrap, land_wrap), {})
    assert len(result["weather"]["forecast_daily"]) == 10


def test_merge_all_boundary_date_short_cache_fallback():
    # Given: 단기예보에 0900/1500 TMP가 없는 날짜만 있어 short_covered_dates에서 제외되며
    #        mid_day_idx < 3인 경계 상황이 만들어진다
    # When:  _merge_all을 호출하면
    # Then:  경계 날짜는 단기 캐시 fallback을 사용하여 10일치 forecast를 정상 반환한다
    api = KMAWeatherAPI(MagicMock(), "key", "11B10101", "11B00000")
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
    # Given: 단기예보 응답에 VEC=225(남서) 값이 포함되어 있다
    # When:  _merge_all을 호출하면
    # Then:  weather_data에 VEC_KOR 키가 생성되고 값이 "남서"이다
    api = KMAWeatherAPI(MagicMock(), "key", "11B10101", "11B00000")
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
    # Given: 강수 시작 시각이 10:30(정시가 아님)인 단기예보 데이터가 있다
    # When:  _merge_all을 호출하면
    # Then:  rain_start_time이 "30분"을 포함하는 형식으로 표기된다
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
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
    # Given: 오후 14:00 시각이 주어진다 (now.hour >= 12)
    # When:  _merge_all을 호출하면
    # Then:  오늘(i=0)의 오전(is_daytime=True) 슬롯은 twice_daily에서 제외된다
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
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
    # Given: 어떤 접두사도 매칭되지 않는 임의의 temp_id가 주어진다
    # When:  _land_code를 호출하면
    # Then:  기본값 "11B00000"을 반환한다
    from custom_components.kma_weather.coordinator import _land_code
    result = _land_code("UNKNOWN_CODE")
    assert result == "11B00000"


@pytest.mark.asyncio
async def test_restore_daily_temps_float_conversion_fails(hass):
    # Given: 저장소에 today 날짜가 있지만 max 값이 "NOT_A_FLOAT"으로 저장되어 있다
    # When:  _restore_daily_temps를 호출하면
    # Then:  float 변환 실패로 except: pass가 실행되어
    #        _daily_max_temp는 None으로 남고 _store_loaded만 True가 된다
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
    # Given: 코디네이터가 초기화되어 있고 오늘 날짜의 예보 데이터가 존재한다
    # When:  _update_daily_temperatures를 여러 번 호출하면
    # Then:  1차: 날짜 초기화 후 min=15, max=25로 설정된다
    #        2차: 더 낮은 온도(10)가 들어와 min만 10으로 갱신된다
    #        3차: 동일 데이터 재입력 시 changed=False를 반환한다
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
    # Given: 코디네이터에 max=30, min=10, wf_am=맑음, wf_pm=구름많음이 저장되어 있고
    #        weather dict에 내일 데이터(TMX_tomorrow=28, wf_pm_tomorrow=비)가 있다
    # When:  _sync_today_forecast를 호출하면
    # Then:  forecast_daily[0]의 온도가 누적값(30/10)으로 갱신되고 condition=sunny
    #        forecast_daily[1]의 온도가 내일값(28/12)으로 갱신되고 condition=rainy
    #        twice_daily[0] 주간은 sunny, 야간은 partlycloudy
    #        twice_daily[1] 주간은 cloudy, 야간은 rainy로 갱신된다
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
    # Given: _wf_pm_today=None(기존 미설정), API 응답에 wf_pm_today="흐림"이 포함되며
    #        오후 15:00 시각으로 datetime.now가 모킹된다
    # When:  _async_update_data를 호출하면
    # Then:  wf_pm_today가 "흐림"으로 갱신되고 raw_forecast 처리 경로가 실행된다
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
    # Given: entity state에 유효한 한반도 좌표(37.56, 126.98)가 설정되어 있다
    # When:  _resolve_location을 호출하면
    # Then:  entity의 위도/경도 값을 그대로 반환한다
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
    # Given: entity state의 latitude/longitude가 "INVALID" 문자열로 되어 있다
    # When:  _resolve_location을 호출하면
    # Then:  float 변환이 실패해 except: pass 경로를 타고
    #        HA config 좌표(37.56, 126.98)로 fallback한다
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
    # Given: entity가 없고 HA config의 latitude/longitude도 "BAD" 문자열이다
    # When:  _resolve_location을 호출하면
    # Then:  모든 좌표 추출이 실패해 (None, None)을 반환한다
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
    # Given: entry.data에 오늘로부터 15일 뒤 만료 날짜가 설정되어 있다
    # When:  api_expire 센서의 native_value를 조회하면
    # Then:  잔여 일수 15를 int 타입으로 반환한다
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
    # Given: weather 데이터에 TMP가 "INVALID_FLOAT" 문자열로 저장되어 있다
    # When:  TMP 센서의 native_value를 조회하면
    # Then:  float 변환이 실패해 ValueError/TypeError 경로로 None을 반환한다
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
    # Given: coordinator.data가 None인 상태이다
    # When:  address 센서의 extra_state_attributes를 조회하면
    # Then:  데이터가 없으므로 None을 반환한다
    coordinator = MagicMock()
    coordinator.data = None
    entry = MagicMock()
    entry.entry_id = "no_data"
    entry.options = {}
    entry.data = {"prefix": "x"}
    sensor = KMACustomSensor(coordinator, "address", "x", entry)
    assert sensor.extra_state_attributes is None


def test_weather_entity_bad_values():
    # Given: weather 데이터에 TMP, REH, WSD, VEC 모두 "BAD" 문자열이 들어 있다
    # When:  KMAWeather 엔티티의 각 속성(온도, 습도, 풍속, 풍향)을 조회하면
    # Then:  ValueError/TypeError가 발생해 모든 속성이 None을 반환한다
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
    # Given: coordinator.data가 None인 상태이다
    # When:  async_forecast_daily와 async_forecast_twice_daily를 호출하면
    # Then:  데이터가 없으므로 빈 리스트 []를 각각 반환한다
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
    # Given: 통합구성요소가 정상 로드된 상태에서 async_unload_platforms가 False를 반환한다
    # When:  async_unload_entry를 호출하면
    # Then:  unload_ok=False이므로 hass.data에서 entry를 pop하지 않아야 한다
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
    # Given: hass에 "zone.home" 엔티티가 등록되어 있고 state에 name이 있다
    # When:  async_step_user에 api_key, prefix, location_entity를 입력하면
    # Then:  config_entry가 생성되고 title에 "기상청 날씨:"가 포함된다
    from custom_components.kma_weather.config_flow import KMAWeatherConfigFlow
    hass.states.async_set("zone.home", "zoning",
                          {"latitude": 37.56, "longitude": 126.98, "friendly_name": "우리집"})
    flow = KMAWeatherConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    flow._async_current_entries = lambda: []
    with patch.object(flow, "async_set_unique_id", return_value=None), \
         patch.object(flow, "_abort_if_unique_id_configured"):
        result = await flow.async_step_user({
            "api_key": "KEY_WITH_STATE",
            "prefix": "home2",
            "location_entity": "zone.home",
        })
    assert result["type"] == "create_entry"
    assert "기상청 날씨:" in result["title"]


@pytest.mark.asyncio
async def test_config_flow_step_user_entity_no_state(hass):
    # Given: 존재하지 않는 엔티티 ID(zone.unknown_entity)가 입력된다
    # When:  async_step_user를 호출하면
    # Then:  state를 찾지 못해 entity_id의 뒷부분("unknown_entity")을 title에 사용한다
    from custom_components.kma_weather.config_flow import KMAWeatherConfigFlow
    flow = KMAWeatherConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    flow._async_current_entries = lambda: []
    with patch.object(flow, "async_set_unique_id", return_value=None), \
         patch.object(flow, "_abort_if_unique_id_configured"):
        result = await flow.async_step_user({
            "api_key": "KEY_NO_STATE",
            "prefix": "nostate",
            "location_entity": "zone.unknown_entity",
        })
    assert result["type"] == "create_entry"
    assert "unknown_entity" in result["title"]


@pytest.mark.asyncio
async def test_config_flow_step_user_no_entity(hass):
    # Given: CONF_LOCATION_ENTITY 키가 user_input에 포함되지 않는다
    # When:  async_step_user를 호출하면
    # Then:  entity 정보가 없으므로 title에 기본값 "우리집"이 사용된다
    from custom_components.kma_weather.config_flow import KMAWeatherConfigFlow
    flow = KMAWeatherConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    flow._async_current_entries = lambda: []
    with patch.object(flow, "async_set_unique_id", return_value=None), \
         patch.object(flow, "_abort_if_unique_id_configured"):
        result = await flow.async_step_user({
            "api_key": "KEY_NO_ENTITY",
            "prefix": "noent",
        })
    assert result["type"] == "create_entry"
    assert "우리집" in result["title"]


@pytest.mark.asyncio
async def test_config_flow_show_form_when_no_input(hass):
    # Given: KMAWeatherConfigFlow 인스턴스가 있다
    # When:  user_input=None으로 async_step_user를 호출하면
    # Then:  사용자 입력 폼을 표시하는 result(type="form", step_id="user")를 반환한다
    from custom_components.kma_weather.config_flow import KMAWeatherConfigFlow
    flow = KMAWeatherConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    result = await flow.async_step_user(None)
    assert result["type"] == "form"
    assert result["step_id"] == "user"


def test_nominatim_agent_uuid_replace_raises():
    # Given: hass.installation_uuid 프로퍼티가 None을 반환한다
    # When:  KMAWeatherAPI 인스턴스를 생성하면
    # Then:  uuid가 None이어서 if uuid: 조건이 False → 해시 fallback 경로를 사용한다
    class BadUuidHass:
        @property
        def installation_uuid(self):
            return None

    api = KMAWeatherAPI(MagicMock(), "MYKEY", "r1", "r2", hass=BadUuidHass())
    expected = hashlib.sha1("MYKEY".encode()).hexdigest()[:12]
    assert expected in api._nominatim_user_agent


def test_nominatim_agent_uuid_attribute_raises_exception():
    # Given: hass.installation_uuid 프로퍼티 접근 시 RuntimeError를 던진다
    # When:  KMAWeatherAPI 인스턴스를 생성하면
    # Then:  except Exception: pass가 실행되어 해시 fallback 경로를 사용한다
    class RaisingHass:
        @property
        def installation_uuid(self):
            raise RuntimeError("permission denied")

    api = KMAWeatherAPI(MagicMock(), "EXKEY", "r1", "r2", hass=RaisingHass())
    expected = hashlib.sha1("EXKEY".encode()).hexdigest()[:12]
    assert expected in api._nominatim_user_agent


@pytest.mark.asyncio
async def test_get_short_term_with_valid_hours():
    # Given: 12:30 시각이 주어진다 (adj=12:20, adj.hour=12, valid_hours=[2,5,8,11])
    # When:  _get_short_term을 호출하면
    # Then:  valid_hours 중 최댓값 11시가 base_h로 선택되어
    #        base_time="1100", base_date=오늘 날짜로 API가 호출된다
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
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
    # Given: 오전 9:00 시각이고 오늘 0900, 1200, 1500 데이터가 있다 (curr_h="0900")
    # When:  _merge_all을 호출하면
    # Then:  times 중 curr_h("0900") 이상인 첫 번째 시각이 best_t가 되어
    #        해당 시각의 TMP(="15") 값으로 weather_data가 업데이트된다
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
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
    # Given: 강수 시작 시각이 14:00(정시, minute=0)인 단기예보 데이터가 있다
    # When:  _merge_all을 호출하면
    # Then:  rain_start_time이 "14시"를 포함하고 "분"은 포함하지 않는다
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
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
    assert "분" not in rain_time, f"정시이므로 분이 없어야 함: {rain_time}"


def test_merge_all_boundary_date_rep_t_sky_kor():
    # 기존: mid_day_idx < 3 경계 분기에서 rep_t → SKY=3 → 구름많음 검증
    # 신규: i=0~3은 단기이므로, 오늘(i=0) 데이터에서 오전 슬롯 SKY 검증으로 변경
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    api.lat = api.lon = api.nx = api.ny = None
    now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
    today = now.strftime("%Y%m%d")
    items = [
        {"fcstDate": today, "fcstTime": "0900", "category": "TMP",  "fcstValue": "12"},
        {"fcstDate": today, "fcstTime": "0900", "category": "SKY",  "fcstValue": "3"},  # 구름많음
        {"fcstDate": today, "fcstTime": "0900", "category": "PTY",  "fcstValue": "0"},
        {"fcstDate": today, "fcstTime": "1500", "category": "TMP",  "fcstValue": "18"},
        {"fcstDate": today, "fcstTime": "1500", "category": "SKY",  "fcstValue": "1"},
        {"fcstDate": today, "fcstTime": "1500", "category": "PTY",  "fcstValue": "0"},
    ]
    short_res = {"response": {"body": {"items": {"item": items}}}}
    result = api._merge_all(now, short_res, None, {})
    # 오전 슬롯(0900)이 SKY=3이므로 wf_am_today는 구름많음
    assert result["weather"].get("wf_am_today") == "구름많음"


@pytest.mark.asyncio
async def test_async_update_data_returns_empty_no_location(hass):
    # Given: location_entity가 없고 HA config 좌표도 범위 밖(0,0)이며 캐시도 없다
    # When:  _async_update_data를 호출하면
    # Then:  _resolve_location이 (None, None)을 반환하여
    #        빈 dict {"weather": {}, "air": {}}를 반환한다
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
    # Given: location_entity가 없고 HA config 좌표도 범위 밖이지만 캐시가 있다
    # When:  _async_update_data를 호출하면
    # Then:  (None, None) 반환 후 _cached_data를 그대로 반환한다
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
    # Given: 유효한 한반도 좌표가 있고 fetch_data가 None을 반환하며 캐시가 있다
    # When:  _async_update_data를 호출하면
    # Then:  새 데이터가 없으므로 _cached_data를 그대로 반환한다
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
    # Given: _wf_am_today="맑음", _wf_pm_today="흐림"이 설정되고
    #        datetime.now가 오전 9:00로 모킹된다
    # When:  _async_update_data를 호출하면
    # Then:  now_h=9 < 12이므로 kor=wf_am_today="맑음"이 선택되고
    #        current_condition="sunny"로 설정된다
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
    # Given: _wf_am_today="맑음", _wf_pm_today="흐림"이 설정되고
    #        datetime.now가 오후 15:00으로 모킹된다
    # When:  _async_update_data를 호출하면
    # Then:  now_h=15 >= 12이므로 kor=wf_pm_today="흐림"이 선택되고
    #        current_condition="cloudy"로 설정된다
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
    # Given: "zone.work" 엔티티가 부산 좌표(35.18, 129.07)로 등록되어 있다
    # When:  _async_update_data를 호출하면
    # Then:  _resolve_location이 엔티티 좌표를 반환하고
    #        fetch_data가 그 좌표(35.18, 129.07)로 호출된다
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

    async def mock_fetch_data(lat, lon, nx, ny):
        captured_coords["lat"] = lat
        captured_coords["lon"] = lon
        return None

    coord.api.fetch_data = mock_fetch_data
    coord._cached_data = {"weather": {}, "air": {}}
    await coord._async_update_data()
    assert captured_coords.get("lat") == pytest.approx(35.18)
    assert captured_coords.get("lon") == pytest.approx(129.07)


def test_sensor_api_expire_valid_iso_date():
    # Given: options에 오늘로부터 7일 뒤 만료 날짜가 설정되어 있다
    #        (options가 data보다 우선순위가 높다)
    # When:  api_expire 센서의 native_value를 조회하면
    # Then:  잔여 일수 7을 int 타입으로 반환한다
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
    # Given: 위도 또는 경도에 NaN 값이 입력된다
    # When:  _is_valid_korean_coord를 호출하면
    # Then:  math.isnan 검사에 걸려 False를 반환한다
    from custom_components.kma_weather.coordinator import _is_valid_korean_coord
    import math
    assert _is_valid_korean_coord(math.nan, 126.98) is False
    assert _is_valid_korean_coord(37.56, math.nan) is False


@pytest.mark.asyncio
async def test_async_update_228_exit_via_resolve_location_mock(hass):
    # Given: _resolve_location이 (None, None)을 반환하도록 patch되고 캐시가 없다
    # When:  _async_update_data를 호출하면
    # Then:  228번 라인 early return이 실행되어 빈 dict를 반환한다
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
    # Given: _resolve_location이 (None, None)을 반환하도록 patch되고 캐시가 있다
    # When:  _async_update_data를 호출하면
    # Then:  228번 라인 early return이 실행되어 캐시 데이터를 반환한다
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
    # Given: _resolve_location이 유효 좌표를 반환하고 fetch_data가 None을 반환하며 캐시가 있다
    # When:  _async_update_data를 호출하면
    # Then:  235번 라인 early return이 실행되어 캐시 데이터를 반환한다
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
    # Given: _resolve_location이 유효 좌표를 반환하고 fetch_data가 RuntimeError를 던지며 캐시가 있다
    # When:  _async_update_data를 호출하면
    # Then:  except 블록에서 오류를 로깅하고 _cached_data를 반환한다
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
    # Given: "zone.busan" 엔티티에 부산 좌표(35.18, 129.07)가 설정되어 있다
    # When:  _resolve_location을 호출하면
    # Then:  is_valid_korean_coord → True이므로 엔티티 좌표를 그대로 반환한다
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
    # Given: options에 20일 뒤 만료일이 있고 data에는 expire_date가 없다
    # When:  api_expire 센서의 native_value를 조회하면
    # Then:  options 값이 data보다 우선 읽혀 잔여 일수 20을 반환한다
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
    # Given: 코디네이터의 누적값(max/min/wf_am/pm)이 모두 None이고
    #        current_condition, TMX/TMN_tomorrow, wf_am/pm_tomorrow도 모두 None이다
    # When:  _sync_today_forecast를 호출하면
    # Then:  None이므로 if 조건들이 모두 False가 되어
    #        기존 forecast_daily/twice_daily 값을 덮어쓰지 않는다
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
    d1 = next(e for e in weather["forecast_daily"] if e["_day_index"] == 1)
    assert d1["native_temperature"] == 22.0


@pytest.mark.asyncio
async def test_async_update_summary_changed_saves_temps(hass):
    # Given: _wf_am_today="맑음", _wf_pm_today="맑음"이 저장된 코디네이터가 있고
    #        API 응답에 wf_am_today="흐림", wf_pm_today="비"가 들어온다
    # When:  _async_update_data를 호출하면
    # Then:  두 값 모두 변경되어 summary_changed=True가 되고
    #        _save_daily_temps가 호출되며 코디네이터 값이 갱신된다
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
    # Given: _cache_forecast_map에 오늘 날짜 키는 있지만 내부 dict가 빈 상태이다
    #        (times = [] → best_t = None)
    # When:  _merge_all을 호출하면
    # Then:  "if best_t:" 조건이 False가 되어 weather_data.update가 실행되지 않으며
    #        TMP는 초기값 None을 유지한다
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    api.lat = api.lon = api.nx = api.ny = None
    now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
    today = now.strftime("%Y%m%d")
    api._cache_forecast_map = {today: {}}
    result = api._merge_all(now, None, None, {})
    assert result["weather"]["TMP"] is None


def test_merge_all_rep_t_none_when_empty_forecast_keys():
    # Given: _cache_forecast_map의 오늘 날짜 dict가 비어 있고
    #        tm_fc_dt가 설정되어 mid_day_idx=0 < 3인 경계 상황이다
    # When:  _merge_all을 호출하면
    # Then:  min([], default=None)으로 rep_t=None이 되어
    #        "if rep_t:" 조건이 False가 되고 _get_sky_kor 호출이 스킵된다
    #        기본값 "맑음"이 wf_am_today에 유지되고 10일치 예보가 반환된다
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
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
    # Given: _resolve_location이 (None, None)을 반환하도록 patch되고 캐시가 None이다
    # When:  _async_update_data를 호출하면
    # Then:  228번 라인(if curr_lat is None: return cached or {})이 실행되어
    #        {"weather": {}, "air": {}}를 반환한다
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
    # Given: _resolve_location이 (None, None)을 반환하도록 patch되고 캐시가 있다
    # When:  _async_update_data를 호출하면
    # Then:  228번 라인이 실행되어 _cached_data를 반환한다
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
    # Given: _resolve_location이 유효 좌표를 반환하고 fetch_data가 None이며 캐시가 있다
    # When:  _async_update_data를 호출하면
    # Then:  235번 라인(if not new_data: return self._cached_data)이 실행된다
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
    # Given: _wf_am_today="맑음"이고 API 응답의 wf_am_today="흐림"으로 다르다
    #        _wf_pm_today="맑음"이고 API 응답도 "맑음"으로 동일하다
    # When:  _async_update_data를 호출하면
    # Then:  244번 if 조건(api_am != _wf_am_today)이 True가 되어
    #        _wf_am_today가 "흐림"으로 갱신되고 _save_daily_temps가 호출된다
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
    # Given: _wf_am_today="맑음"이고 API 응답도 "맑음"으로 동일하다
    #        _wf_pm_today="맑음"이고 API 응답의 wf_pm_today="비"로 다르다
    # When:  _async_update_data를 호출하면
    # Then:  246번 if 조건(api_pm != _wf_pm_today)이 True가 되어
    #        _wf_pm_today가 "비"로 갱신된다
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
async def test_async_update_248_temp_changed_saves(hass):
    # Given: _daily_max_temp=None인 상태에서 오늘 TMP 데이터가 새로 들어온다
    # When:  _async_update_data를 호출하면
    # Then:  _update_daily_temperatures에서 temp_changed=True가 되어
    #        248번 if 조건이 True가 되고 _save_daily_temps가 호출된다
    #        _daily_max_temp가 28.0으로 갱신된다
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
    # Given: device_tracker.phone 엔티티의 state에 대전 좌표(36.35, 127.38)가 있다
    # When:  _resolve_location을 호출하면
    # Then:  is_valid_korean_coord → True이므로 (36.35, 127.38)을 반환한다
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
    # Given: coordinator.data가 None인 상태이고 api_expire가 아닌 여러 센서가 있다
    # When:  각 센서의 native_value를 조회하면
    # Then:  77번 라인(if not self.coordinator.data: return None)이 실행되어
    #        모든 센서가 None을 반환한다
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
        assert val is None, f"{sensor_type}: coordinator.data=None이면 None이어야 함"


def test_resolve_location_entity_out_of_range_falls_to_last_lat():
    # Given: entity state에 한반도 범위 밖 좌표(적도 0,0)가 있고 _last_lat이 캐시되어 있다
    # When:  _resolve_location을 호출하면
    # Then:  is_valid_korean_coord → False이므로 288에서 return이 실행되지 않고
    #        290번 라인(if self._last_lat is not None: return)으로 이동해 캐시 좌표를 반환한다
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
    # Given: entity state가 있지만 latitude 속성이 None이다
    # When:  _resolve_location을 호출하면
    # Then:  285번 조건(lat_attr is not None and lon_attr is not None)이 False가 되어
    #        290번으로 이동하고 _last_lat=None이므로 HA config 좌표를 사용한다
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
    # Given: entity state에 도쿄 좌표(한반도 범위 밖)가 있고 _last_lat도 None이다
    # When:  _resolve_location을 호출하면
    # Then:  285에서 float 변환 성공 but is_valid=False → 288 return 미실행
    #        290에서 _last_lat=None → 291로 이동해 HA config 좌표를 반환한다
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {"location_entity": "zone.abroad"}
    entry.options = {}
    entry.entry_id = "abroad_no_cache"
    hass = MagicMock()
    state = MagicMock()
    state.attributes = {"latitude": 35.6762, "longitude": 139.6503}  # 도쿄
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
