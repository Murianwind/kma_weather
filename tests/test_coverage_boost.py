# tests/test_coverage_boost.py
"""
커버리지 80% 달성을 위한 보완 테스트.

커버 대상 모듈 및 미커버 분기:
  - api_kma.py  : _calculate_apparent_temp (체감온도 3분기),
                  _get_vec_kor (8방위 전체),
                  _get_air_quality (캐시 HIT / 측정소 없음 / 데이터 없음),
                  _wgs84_to_tm,
                  _safe_float,
                  _translate_mid_condition / _translate_mid_condition_kor,
                  _get_condition (하위호환 래퍼)
  - coordinator.py : _restore_daily_temps (저장소 복구 성공 / 날짜 불일치),
                     _save_daily_temps,
                     _resolve_location (캐시 fallback / HA config fallback)
  - button.py   : async_press (정상 / 쿨다운 5 초 제한)
  - config_flow.py : OptionsFlow
  - sensor.py   : extra_state_attributes (address 타입 속성 검증)
"""

import pytest
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

# ---------------------------------------------------------------------------
# api_kma.py — _safe_float
# ---------------------------------------------------------------------------
from custom_components.kma_weather.api_kma import KMAWeatherAPI, _safe_float


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
# api_kma.py — _calculate_apparent_temp (3 분기)
# ---------------------------------------------------------------------------
class TestApparentTemp:
    def _api(self):
        api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
        return api

    def test_wind_chill_branch(self):
        """기온 ≤10°C, 풍속 ≥4.8km/h → 체감온도 공식 적용"""
        api = self._api()
        result = api._calculate_apparent_temp(temp=5, reh=60, wsd=3)  # 3 m/s = 10.8 km/h
        assert result is not None
        assert isinstance(result, float)
        assert result < 5  # 바람으로 인해 실제 온도보다 낮아야 함

    def test_heat_index_branch(self):
        """기온 ≥25°C, 습도 ≥40% → Heat Index 공식 적용"""
        api = self._api()
        result = api._calculate_apparent_temp(temp=30, reh=70, wsd=1)
        assert result is not None
        assert isinstance(result, float)

    def test_default_branch_returns_temp(self):
        """바람/습도 조건 미충족 → 기온 그대로 반환"""
        api = self._api()
        result = api._calculate_apparent_temp(temp=20, reh=30, wsd=0.5)
        assert result == 20

    def test_none_temp_returns_none(self):
        """기온 None → None 반환"""
        api = self._api()
        assert api._calculate_apparent_temp(temp=None, reh=50, wsd=2) is None

    def test_string_temp_parsed(self):
        """문자열 기온도 정상 처리"""
        api = self._api()
        result = api._calculate_apparent_temp(temp="15", reh=50, wsd=0)
        assert result == 15


# ---------------------------------------------------------------------------
# api_kma.py — _get_vec_kor (8 방위 전체)
# ---------------------------------------------------------------------------
class TestGetVecKor:
    def _api(self):
        return KMAWeatherAPI(MagicMock(), "key", "r1", "r2")

    @pytest.mark.parametrize("vec,expected", [
        (0,   "북"),
        (22.5, "북동"),
        (67.5, "동"),
        (112.5, "남동"),
        (157.5, "남"),
        (202.5, "남서"),
        (247.5, "서"),
        (292.5, "북서"),
        (337.5, "북"),
        (360,  "북"),
    ])
    def test_directions(self, vec, expected):
        api = self._api()
        assert api._get_vec_kor(vec) == expected

    def test_none_vec_returns_none(self):
        api = self._api()
        assert api._get_vec_kor(None) is None


# ---------------------------------------------------------------------------
# api_kma.py — _translate_mid_condition_kor + _translate_mid_condition (래퍼)
# ---------------------------------------------------------------------------
class TestTranslateMidCondition:
    def _api(self):
        return KMAWeatherAPI(MagicMock(), "key", "r1", "r2")

    @pytest.mark.parametrize("wf,expected_kor", [
        ("맑음",      "맑음"),
        ("구름많음",  "구름많음"),
        ("흐림",      "흐림"),
        ("비",        "비"),
        ("눈",        "눈"),
        ("구름많고 비", "비"),
        ("흐리고 눈",  "눈"),
    ])
    def test_kor_mapping(self, wf, expected_kor):
        api = self._api()
        assert api._translate_mid_condition_kor(wf) == expected_kor

    def test_translate_mid_condition_wrapper(self):
        """하위호환 래퍼가 kor_to_condition 을 경유하는지 확인"""
        api = self._api()
        result = api._translate_mid_condition("맑음")
        assert result == "sunny"

    def test_get_condition_wrapper(self):
        """_get_condition 래퍼도 정상 동작하는지 확인"""
        api = self._api()
        assert api._get_condition("1", "0") == "sunny"   # 맑음
        assert api._get_condition("4", "0") == "cloudy"  # 흐림
        assert api._get_condition("1", "1") == "rainy"   # 비


# ---------------------------------------------------------------------------
# api_kma.py — _wgs84_to_tm (TM 좌표 변환)
# ---------------------------------------------------------------------------
class TestWgs84ToTm:
    def test_seoul_tm_coords(self):
        """서울 좌표 → TM 좌표 변환 결과가 합리적 범위인지 확인"""
        api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
        x, y = api._wgs84_to_tm(37.5665, 126.9780)
        # TM 좌표는 미터 단위이며 한반도 기준으로 수십만~수백만 범위
        assert 100_000 < x < 500_000
        assert 300_000 < y < 700_000


# ---------------------------------------------------------------------------
# api_kma.py — _get_air_quality (캐시 분기)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_air_quality_cache_hit():
    """측정소 캐시 HIT 시 재조회 없이 기존 측정소 이름을 사용하는지 검증"""
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    api.lat, api.lon = 37.56, 126.98

    now = datetime.now(ZoneInfo("Asia/Seoul"))
    api._cached_station = "화성"
    api._cached_lat_lon = (37.56, 126.98)
    api._station_cache_time = now  # 캐시 유효 (600초 이내)

    air_json = {
        "response": {
            "body": {
                "items": [{
                    "pm10Value": "40",
                    "pm10Grade": "2",
                    "pm25Value": "18",
                    "pm25Grade": "2",
                }]
            }
        }
    }

    async def mock_fetch(url, params=None, timeout=10):
        # 측정소 조회 URL 은 호출되면 안 됨
        assert "MsrstnInfoInqireSvc" not in url, "캐시 HIT 인데 측정소 재조회 발생"
        return air_json

    api._fetch = mock_fetch
    result = await api._get_air_quality()

    assert result["station"] == "화성"
    assert result["pm10Grade"] == "보통"


@pytest.mark.asyncio
async def test_air_quality_no_station_items():
    """측정소 조회 응답에 items 가 빈 경우 빈 dict 반환"""
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
    """대기 측정 데이터가 빈 경우 station 만 포함한 dict 반환"""
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    api.lat, api.lon = 37.56, 126.98
    api._cached_station = None
    api._cached_lat_lon = None
    api._station_cache_time = None

    call_count = 0

    async def mock_fetch(url, params=None, timeout=10):
        nonlocal call_count
        call_count += 1
        if "MsrstnInfoInqireSvc" in url:
            return {"response": {"body": {"items": [{"stationName": "중구"}]}}}
        # 대기 데이터 응답: items 비어 있음
        return {"response": {"body": {"items": []}}}

    api._fetch = mock_fetch
    result = await api._get_air_quality()
    assert result == {"station": "중구"}


@pytest.mark.asyncio
async def test_air_quality_fetch_returns_none():
    """_fetch 가 None 반환 시 빈 dict 반환"""
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
# coordinator.py — _restore_daily_temps
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_restore_daily_temps_success(hass):
    """저장소에 오늘 날짜 데이터가 있으면 정상 복구되는지 검증"""
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "restore_test"

    coord = KMAWeatherUpdateCoordinator(hass, entry)
    tz = ZoneInfo("Asia/Seoul")
    today_str = datetime.now(tz).strftime("%Y%m%d")

    # 저장소에 오늘 날짜 데이터 주입
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
    """저장소의 날짜가 오늘과 다르면 복구하지 않아야 함"""
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "restore_date_mismatch"

    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store.async_load = AsyncMock(return_value={
        "date": "20200101",  # 과거 날짜
        "max": 99.0,
        "min": -99.0,
    })

    await coord._restore_daily_temps()

    assert coord._daily_max_temp is None
    assert coord._daily_min_temp is None
    assert coord._store_loaded is True


@pytest.mark.asyncio
async def test_restore_daily_temps_empty_store(hass):
    """저장소가 비어 있을 때 예외 없이 정상 종료"""
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
# coordinator.py — _save_daily_temps
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_save_daily_temps(hass):
    """_save_daily_temps 가 저장소에 올바른 데이터를 저장하는지 검증"""
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
    """_daily_date 가 None 이면 저장하지 않아야 함"""
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
# coordinator.py — _resolve_location (캐시 fallback)
# ---------------------------------------------------------------------------
def test_resolve_location_uses_cached_coords_when_entity_invalid():
    """엔티티 좌표가 유효하지 않을 때 _last_lat/_last_lon 캐시를 사용"""
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

    entry = MagicMock()
    entry.data = {"location_entity": "zone.home"}
    entry.options = {}
    entry.entry_id = "cache_fallback"

    hass = MagicMock()
    # 엔티티 좌표: 한반도 밖 (적도)
    state = MagicMock()
    state.attributes = {"latitude": 0.0, "longitude": 0.0}
    hass.states.get.return_value = state
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98

    coord = KMAWeatherUpdateCoordinator.__new__(KMAWeatherUpdateCoordinator)
    coord.hass = hass
    coord.entry = entry
    coord._last_lat = 35.1
    coord._last_lon = 129.0  # 부산

    lat, lon = coord._resolve_location()
    assert lat == 35.1
    assert lon == 129.0


def test_resolve_location_falls_back_to_ha_config():
    """엔티티도 없고 캐시도 없을 때 HA 설정 좌표 사용"""
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
# button.py — async_press (정상 호출 / 5초 쿨다운)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_button_press_triggers_refresh(hass, mock_config_entry, kma_api_mock_factory):
    """버튼 press 시 coordinator.async_request_refresh 가 호출되는지 검증"""
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98

    # device_tracker 를 사용하도록 entry 데이터 변경
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

    # 첫 번째 press — 갱신 요청 발생해야 함
    await hass.services.async_call(
        "button", "press",
        target={"entity_id": "button.btn_manual_update"},
        blocking=True,
    )
    await hass.async_block_till_done()
    coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_button_press_cooldown(hass, kma_api_mock_factory):
    """5초 이내 연속 press 시 두 번째 요청은 무시되는지 검증"""
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

    # 첫 번째 press
    await button.async_press()
    assert coordinator.async_request_refresh.call_count == 1

    # 3초 후 press (5초 제한 이내)
    button._last_press = datetime.now() - timedelta(seconds=3)
    await button.async_press()
    assert coordinator.async_request_refresh.call_count == 1  # 호출 증가 없어야 함

    # 6초 후 press (5초 제한 초과)
    button._last_press = datetime.now() - timedelta(seconds=6)
    await button.async_press()
    assert coordinator.async_request_refresh.call_count == 2  # 이번엔 호출되어야 함


# ---------------------------------------------------------------------------
# config_flow.py — OptionsFlow
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_options_flow(hass, mock_config_entry, kma_api_mock_factory):
    """Options Flow 가 정상적으로 폼을 표시하고 저장하는지 검증"""
    from homeassistant import config_entries
    from custom_components.kma_weather.const import DOMAIN

    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    kma_api_mock_factory("full_test")

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Options Flow 시작
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "init"

    # Options 저장
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "expire_date": "2026-12-31",
            "apply_date": "2025-01-01",
        },
    )
    assert result2["type"] == "create_entry"


# ---------------------------------------------------------------------------
# sensor.py — extra_state_attributes (address 타입)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sensor_location_extra_attributes(hass, mock_config_entry, kma_api_mock_factory):
    """sensor.{p}_location 의 extra_state_attributes 에 진단 정보가 포함되는지 검증"""
    from custom_components.kma_weather.const import DOMAIN

    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    kma_api_mock_factory("full_test")

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    loc = hass.states.get("sensor.test_location")
    assert loc is not None

    attrs = loc.attributes
    # extra_state_attributes 에서 반환하는 필드 검증
    assert "air_korea_station" in attrs
    # debug 필드는 coordinator 가 채워줄 때만 존재 (None 이어도 키는 존재)
    assert "short_term_nx" in attrs
    assert "short_term_ny" in attrs
    assert "latitude" in attrs
    assert "longitude" in attrs


# ---------------------------------------------------------------------------
# api_kma.py — _translate_grade (등급 변환 전체 케이스)
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
# api_kma.py — _get_sky_kor (PTY 우선 분기 전체)
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
        (None, None, "맑음"),   # 기본값 처리
    ])
    def test_sky_kor_mapping(self, sky, pty, expected):
        api = self._api()
        assert api._get_sky_kor(sky, pty) == expected


# ---------------------------------------------------------------------------
# coordinator.py — _haversine / _land_code (모듈 수준 헬퍼)
# ---------------------------------------------------------------------------
from custom_components.kma_weather.coordinator import _haversine, _land_code


def test_haversine_same_point():
    """같은 좌표는 거리 0 반환"""
    assert _haversine(37.5, 127.0, 37.5, 127.0) == pytest.approx(0.0)


def test_haversine_known_distance():
    """서울-부산 직선 거리 약 325km"""
    d = _haversine(37.5665, 126.9780, 35.1796, 129.0756)
    assert 310 < d < 340


@pytest.mark.parametrize("temp_id,expected_land", [
    ("11B10101", "11B00000"),
    ("11G00101", "11G00000"),
    ("11A00101", "11A00101"),   # 특수: 11A → 11A00101
    ("11E00101", "11E00101"),   # 특수: 11E → 11E00101
    ("11H10101", "11H10000"),
])
def test_land_code_mapping(temp_id, expected_land):
    assert _land_code(temp_id) == expected_landtest_coverage_boost
