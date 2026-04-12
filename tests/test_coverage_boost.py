import pytest
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.kma_weather.api_kma import KMAWeatherAPI, _safe_float

# ─────────────────────────────────────────────────────────────────────────────
# 1. _safe_float: 다양한 입력값에 대한 안전한 변환 검증
# ─────────────────────────────────────────────────────────────────────────────
class TestSafeFloat:
    def test_none_returns_none(self):
        # [Given/When/Then] None 입력 시 None 반환
        assert _safe_float(None) is None

    def test_empty_string_returns_none(self):
        # [Given/When/Then] 빈 문자열 입력 시 None 반환
        assert _safe_float("") is None

    def test_dash_returns_none(self):
        # [Given/When/Then] "-" 입력 시 None 반환
        assert _safe_float("-") is None

    def test_valid_int_string(self):
        # [Given/When/Then] 정수형 문자열 입력 시 float 반환
        assert _safe_float("22") == 22.0

    def test_valid_float_string(self):
        # [Given/When/Then] 소수점 문자열 입력 시 float 반환
        assert _safe_float("3.14") == pytest.approx(3.14)

    def test_invalid_string_returns_none(self):
        # [Given/When/Then] 잘못된 문자열 입력 시 None 반환
        assert _safe_float("abc") is None

# ─────────────────────────────────────────────────────────────────────────────
# 2. _calculate_apparent_temp: 기상 상황별 체감온도 산출 검증
# ─────────────────────────────────────────────────────────────────────────────
class TestApparentTemp:
    def _api(self):
        return KMAWeatherAPI(MagicMock(), "key", "r1", "r2")

    def test_wind_chill_branch(self):
        # [Given] 추운 조건 (10도 이하, 일정 풍속 이상)
        api = self._api()
        # [When] 체감온도 계산 시
        result = api._calculate_apparent_temp(temp=5, reh=60, wsd=3)
        # [Then] 풍속 냉각 효과가 반영되어 기온보다 낮게 산출되어야 함
        assert result is not None
        assert isinstance(result, float)
        assert result < 5

    def test_heat_index_branch(self):
        # [Given] 더운 조건 (25도 이상, 고습도)
        api = self._api()
        # [When/Then] 열지수 공식이 적용되어 산출되어야 함
        result = api._calculate_apparent_temp(temp=30, reh=70, wsd=1)
        assert result is not None
        assert isinstance(result, float)

    def test_default_branch_returns_temp(self):
        # [Given] 일반 조건 (냉각/열지수 범위 밖)
        api = self._api()
        # [When/Then] 원본 기온이 그대로 반환되어야 함
        result = api._calculate_apparent_temp(temp=20, reh=30, wsd=0.5)
        assert result == 20

    def test_none_temp_returns_none(self):
        # [Given] 기온 데이터가 없는 경우
        api = self._api()
        # [When/Then] None을 안전하게 반환해야 함
        assert api._calculate_apparent_temp(temp=None, reh=50, wsd=2) is None

    def test_string_temp_parsed(self):
        # [Given] 문자열로 된 기온 데이터
        api = self._api()
        # [When/Then] 내부에서 float로 변환하여 정상 계산해야 함
        result = api._calculate_apparent_temp(temp="15", reh=50, wsd=0)
        assert result == 15

# ─────────────────────────────────────────────────────────────────────────────
# 3. _get_vec_kor: 360도 방위각의 8방위 한글 변환 검증
# ─────────────────────────────────────────────────────────────────────────────
class TestGetVecKor:
    def _api(self):
        return KMAWeatherAPI(MagicMock(), "key", "r1", "r2")

    @pytest.mark.parametrize("vec,expected", [
        (0,      "북"),
        (22.5,  "북동"),
        (67.5,  "동"),
        (112.5, "남동"),
        (157.5, "남"),
        (202.5, "남서"),
        (247.5, "서"),
        (292.5, "북서"),
        (337.5, "북"),
        (360,    "북"),
    ])
    def test_directions(self, vec, expected):
        # [Given/When/Then] 방위각 입력 시 정확한 한글 방위 반환
        api = self._api()
        assert api._get_vec_kor(vec) == expected

    def test_none_vec_returns_none(self):
        # [Given/When/Then] 방위 데이터 부재 시 None 반환
        api = self._api()
        assert api._get_vec_kor(None) is None

# ─────────────────────────────────────────────────────────────────────────────
# 4. 중기예보 한글 상태 변환 (수정됨: 원문 유지 정책 반영)
# ─────────────────────────────────────────────────────────────────────────────
class TestTranslateMidCondition:
    def _api(self):
        return KMAWeatherAPI(MagicMock(), "key", "r1", "r2")

    @pytest.mark.parametrize("wf,expected_kor", [
        ("맑음",       "맑음"),
        ("구름많음",   "구름많음"),
        ("흐림",       "흐림"),
        ("비",         "비"),
        ("눈",         "눈"),
        # [Fix] "구름많고 비"는 KOR_TO_CONDITION의 Key이므로 early return되어 원문이 유지됨
        ("구름많고 비", "구름많고 비"), 
        ("흐리고 눈",   "흐리고 눈"),
        ("예상외문장 비", "비"), # Key가 없을 때만 키워드 검색 작동
    ])
    def test_kor_mapping(self, wf, expected_kor):
        # [Given/When/Then] 중기예보 문구 입력 시 적절한 한글 대표 상태로 변환
        api = self._api()
        assert api._translate_mid_condition_kor(wf) == expected_kor

    def test_translate_mid_condition_wrapper(self):
        # [Given/When/Then] 최종적으로 HA 표준 영문 상태값으로 변환되는지 확인
        api = self._api()
        result = api._translate_mid_condition("맑음")
        assert result == "sunny"

    def test_get_condition_wrapper(self):
        # [Given/When/Then] SKY/PTY 코드를 영문 상태로 올바르게 변환하는지 확인
        api = self._api()
        assert api._get_condition("1", "0") == "sunny"
        assert api._get_condition("4", "0") == "cloudy"
        assert api._get_condition("1", "1") == "rainy"

# ─────────────────────────────────────────────────────────────────────────────
# 5. _wgs84_to_tm: 좌표계 변환 무결성 검증
# ─────────────────────────────────────────────────────────────────────────────
class TestWgs84ToTm:
    def test_seoul_tm_coords(self):
        # [Given] 서울 시청 좌표
        api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
        # [When] TM 좌표로 변환 시
        x, y = api._wgs84_to_tm(37.5665, 126.9780)
        # [Then] 한국 표준 TM 좌표 범위 내에 위치해야 함
        assert 100_000 < x < 500_000
        assert 300_000 < y < 700_000

# ─────────────────────────────────────────────────────────────────────────────
# 6. _get_air_quality: 에어코리아 측정소 캐시 및 예외 처리
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_air_quality_cache_hit():
    # [Given] 동일 위치의 캐시된 측정소 정보가 있을 때
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
        # [Then] 측정소 목록 API를 다시 호출하지 않아야 함
        assert "MsrstnInfoInqireSvc" not in url, "캐시 HIT인데 측정소 재조회 발생"
        return air_json

    api._fetch = mock_fetch
    # [When] 대기질 조회 수행
    result = await api._get_air_quality()
    # [Then] 캐시된 측정소 이름과 함께 데이터 반환
    assert result["station"] == "화성"
    assert result["pm10Grade"] == "보통"

@pytest.mark.asyncio
async def test_air_quality_no_station_items():
    # [Given] 주변 측정소가 발견되지 않는 경우
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    api.lat, api.lon = 37.56, 126.98
    async def mock_fetch(url, params=None, timeout=10):
        if "MsrstnInfoInqireSvc" in url:
            return {"response": {"body": {"items": []}}}
        return {}
    api._fetch = mock_fetch
    # [When] 조회 수행
    result = await api._get_air_quality()
    # [Then] 빈 결과 반환
    assert result == {}

@pytest.mark.asyncio
async def test_air_quality_no_air_data_items():
    # [Given] 측정소는 있으나 측정 데이터가 없는 경우
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    api.lat, api.lon = 37.56, 126.98
    async def mock_fetch(url, params=None, timeout=10):
        if "MsrstnInfoInqireSvc" in url:
            return {"response": {"body": {"items": [{"stationName": "중구"}]}}}
        return {"response": {"body": {"items": []}}}
    api._fetch = mock_fetch
    # [When] 조회 수행
    result = await api._get_air_quality()
    # [Then] 측정소 이름만 담긴 결과 반환
    assert result == {"station": "중구"}

@pytest.mark.asyncio
async def test_air_quality_fetch_returns_none():
    # [Given] 네트워크 에러 등으로 API가 None을 반환할 때
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    api.lat, api.lon = 37.56, 126.98
    async def mock_fetch(url, params=None, timeout=10):
        return None
    api._fetch = mock_fetch
    # [When] 조회 수행
    result = await api._get_air_quality()
    # [Then] 에러 없이 빈 결과 반환
    assert result == {}

# ─────────────────────────────────────────────────────────────────────────────
# 7. coordinator: 데이터 복구 및 저장 검증
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_restore_daily_temps_success(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    # [Given] 오늘 날짜의 저장된 파일 데이터가 있을 때
    entry = MagicMock(data={"api_key": "key", "location_entity": ""}, options={}, entry_id="restore_test")
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    today_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    coord._store.async_load = AsyncMock(return_value={
        "date": today_str, "max": 28.5, "min": 12.0, "wf_am": "맑음", "wf_pm": "구름많음",
    })
    # [When] 복구 실행
    await coord._restore_daily_temps()
    # [Then] 메모리 변수에 정상 로드되어야 함
    assert coord._daily_max_temp == 28.5
    assert coord._daily_min_temp == 12.0
    assert coord._wf_am_today == "맑음"
    assert coord._wf_pm_today == "구름많음"
    assert coord._store_loaded is True

@pytest.mark.asyncio
async def test_restore_daily_temps_date_mismatch(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    # [Given] 저장된 데이터가 과거 날짜일 때
    entry = MagicMock(entry_id="restore_mismatch", data={"api_key": "k", "location_entity": ""}, options={})
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store.async_load = AsyncMock(return_value={"date": "20200101", "max": 99.0, "min": -99.0})
    # [When] 복구 실행
    await coord._restore_daily_temps()
    # [Then] 데이터가 무시되고 None으로 유지되어야 함
    assert coord._daily_max_temp is None
    assert coord._daily_min_temp is None
    assert coord._store_loaded is True

@pytest.mark.asyncio
async def test_save_daily_temps(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    # [Given] 오늘 날짜의 통계 데이터가 준비된 상태
    entry = MagicMock(entry_id="save_test", data={"api_key": "k", "location_entity": ""}, options={})
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._daily_date = date(2025, 6, 1)
    coord._daily_max_temp, coord._daily_min_temp = 30.0, 18.0
    coord._wf_am_today, coord._wf_pm_today = "맑음", "흐림"
    saved = {}
    coord._store.async_save = AsyncMock(side_effect=lambda data: saved.update(data))
    # [When] 저장 실행
    await coord._save_daily_temps()
    # [Then] 파일 시스템 스토리지에 정확한 포맷으로 저장되어야 함
    assert saved["date"] == "20250601"
    assert saved["max"] == 30.0
    assert saved["min"] == 18.0

# ─────────────────────────────────────────────────────────────────────────────
# 8. coordinator: 위치 해결 (Location Resolution) 검증
# ─────────────────────────────────────────────────────────────────────────────
def test_resolve_location_uses_cached_coords_when_entity_invalid():
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    # [Given] 설정된 엔티티가 유효하지 않은 좌표(0,0)를 줄 때
    entry = MagicMock(data={"api_key": "k", "location_entity": "zone.home"}, options={}, entry_id="cache_fallback")
    hass = MagicMock()
    state = MagicMock(attributes={"latitude": 0.0, "longitude": 0.0})
    hass.states.get.return_value = state
    # [When] 위치 해결 시도
    coord = KMAWeatherUpdateCoordinator.__new__(KMAWeatherUpdateCoordinator)
    coord.hass, coord.entry, coord._last_lat, coord._last_lon = hass, entry, 35.1, 129.0
    lat, lon = coord._resolve_location()
    # [Then] 직전에 성공했던(Cache) 좌표를 유지해야 함
    assert lat == 35.1
    assert lon == 129.0

# ─────────────────────────────────────────────────────────────────────────────
# 9. button & config_flow & sensor: 사용자 인터페이스 검증
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_button_press_cooldown(hass, kma_api_mock_factory):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.kma_weather.const import DOMAIN
    from custom_components.kma_weather.button import KMAUpdateButton
    # [Given] 버튼 생성 및 1회 업데이트 완료
    entry = MockConfigEntry(domain=DOMAIN, data={"api_key": "k", "prefix": "cool", "location_entity": ""}, entry_id="cool_test")
    kma_api_mock_factory("full_test")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.async_request_refresh = AsyncMock()
    button = KMAUpdateButton(coordinator, entry)
    # [When] 연속해서 누를 경우
    await button.async_press() # 1회
    button._last_press = datetime.now() - timedelta(seconds=3)
    await button.async_press() # 2회 (쿨다운 중)
    # [Then] 쿨다운(5초) 제한으로 인해 실제 리프레시는 1회만 발생해야 함
    assert coordinator.async_request_refresh.call_count == 1

@pytest.mark.asyncio
async def test_options_flow(hass, mock_config_entry, kma_api_mock_factory):
    # [Given] 통합구성요소 설정 메뉴(OptionsFlow) 진입
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    # [When] 옵션 단계 수행
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"location_entity": "zone.home", "expire_date": "2026-12-31", "apply_date": "2025-01-01"}
    )
    # [Then] 설정 결과가 "create_entry"로 성공해야 함
    assert result2["type"] == "create_entry"

# ─────────────────────────────────────────────────────────────────────────────
# 10. 유틸리티 헬퍼 함수 검증
# ─────────────────────────────────────────────────────────────────────────────
from custom_components.kma_weather.coordinator import _haversine, _land_code

def test_haversine_known_distance():
    # [Given/When/Then] 서울 시청과 부산 시청 사이의 거리가 대략 320km 내외인지 확인
    d = _haversine(37.5665, 126.9780, 35.1796, 129.0756)
    assert 310 < d < 340

class TestLandCodeMapping:
    @pytest.mark.parametrize("temp_id,expected_land", [
        ("11B10101", "11B00000"),
        ("11A00101", "11A00101"),
        ("11H10101", "11H10000"),
    ])
    def test_land_code(self, temp_id, expected_land):
        # [Given/When/Then] 기온 구역 ID 기반 육상 구역 ID 변환 규칙 확인
        assert _land_code(temp_id) == expected_land

class TestTranslateGrade:
    def _api(self): return KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    @pytest.mark.parametrize("grade,expected", [
        ("1", "좋음"), (None, "정보없음"), ("5", "정보없음")
    ])
    def test_all_grades(self, grade, expected):
        # [Given/When/Then] 에어코리아 등급 코드를 한글로 변환
        assert self._api()._translate_grade(grade) == expected
