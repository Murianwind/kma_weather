import pytest
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.kma_weather.api_kma import KMAWeatherAPI, _safe_float

# ─────────────────────────────────────────────────────────────────────────────
# [Given] 공통 헬퍼: 필수 데이터(api_key)가 포함된 Mock Entry 생성
# ─────────────────────────────────────────────────────────────────────────────
def get_mock_entry(entry_id="test_entry"):
    entry = MagicMock()
    entry.data = {"api_key": "test_api_key", "location_entity": "zone.home"}
    entry.options = {}
    entry.entry_id = entry_id
    return entry

# ─────────────────────────────────────────────────────────────────────────────
# 1. api_kma.py: _safe_float 유틸리티 검증
# ─────────────────────────────────────────────────────────────────────────────
class TestSafeFloat:
    def test_none_returns_none(self):
        # Given: 입력값이 None일 때 / When: 호출하면 / Then: None 반환
        assert _safe_float(None) is None

    def test_empty_string_returns_none(self):
        # Given: 빈 문자열일 때 / When: 호출하면 / Then: None 반환
        assert _safe_float("") is None

    def test_dash_returns_none(self):
        # Given: "-"일 때 / When: 호출하면 / Then: None 반환
        assert _safe_float("-") is None

    def test_valid_int_string(self):
        # Given: "22"일 때 / When: 호출하면 / Then: 22.0 반환
        assert _safe_float("22") == 22.0

    def test_valid_float_string(self):
        # Given: "3.14"일 때 / When: 호출하면 / Then: 3.14 반환
        assert _safe_float("3.14") == pytest.approx(3.14)

    def test_invalid_string_returns_none(self):
        # Given: "abc"일 때 / When: 호출하면 / Then: None 반환
        assert _safe_float("abc") is None

# ─────────────────────────────────────────────────────────────────────────────
# 2. api_kma.py: 체감 온도 및 방위 로직
# ─────────────────────────────────────────────────────────────────────────────
class TestApparentTemp:
    def _api(self):
        return KMAWeatherAPI(MagicMock(), "test_key", "r1", "r2")

    def test_wind_chill_branch(self):
        # Given: 춥고 바람 부는 조건 / When: 체감온도 계산 / Then: 기온보다 낮음
        api = self._api()
        assert api._calculate_apparent_temp(temp=5, reh=60, wsd=3) < 5

    def test_heat_index_branch(self):
        # Given: 덥고 습한 조건 / When: 체감온도 계산 / Then: float 결과 반환
        api = self._api()
        assert isinstance(api._calculate_apparent_temp(temp=30, reh=70, wsd=1), float)

    def test_default_branch_returns_temp(self):
        # Given: 평범한 조건 / When: 체감온도 계산 / Then: 입력 기온과 동일
        api = self._api()
        assert api._calculate_apparent_temp(temp=20, reh=30, wsd=0.5) == 20

    def test_none_temp_returns_none(self):
        # Given: 기온이 없을 때 / When: 계산 시도 / Then: None 반환
        api = self._api()
        assert api._calculate_apparent_temp(temp=None, reh=50, wsd=2) is None

    def test_string_temp_parsed(self):
        # Given: 문자열 기온 / When: 계산 시도 / Then: 파싱 후 계산됨
        api = self._api()
        assert api._calculate_apparent_temp(temp="15", reh=50, wsd=0) == 15

class TestGetVecKor:
    @pytest.mark.parametrize("vec,expected", [
        (0, "북"), (22.5, "북동"), (67.5, "동"), (112.5, "남동"),
        (157.5, "남"), (202.5, "남서"), (247.5, "서"), (292.5, "북서"),
        (337.5, "북"), (360, "북"),
    ])
    def test_directions(self, vec, expected):
        # Given: 각도 입력 / When: 방위 변환 / Then: 기대값과 일치
        api = KMAWeatherAPI(MagicMock(), "test_key", "r1", "r2")
        assert api._get_vec_kor(vec) == expected

# ─────────────────────────────────────────────────────────────────────────────
# 3. api_kma.py: 대기질 캐시 및 API 연동
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_air_quality_cache_hit():
    """시나리오: 캐시된 측정소가 있으면 재조회 없이 데이터를 가져옴"""
    api = KMAWeatherAPI(MagicMock(), "test_key", "r1", "r2")
    api.lat, api.lon = 37.56, 126.98
    api._cached_station = "화성"
    api._cached_lat_lon = (37.56, 126.98)
    api._station_cache_time = datetime.now(ZoneInfo("Asia/Seoul"))

    async def mock_fetch(url, params=None, timeout=10):
        assert "MsrstnInfoInqireSvc" not in url # Then: 재조회 미발생 확인
        return {"response": {"body": {"items": [{"pm10Value": "40", "pm10Grade": "2"}]}}}
    
    api._fetch = mock_fetch
    result = await api._get_air_quality()
    assert result["station"] == "화성"

# ─────────────────────────────────────────────────────────────────────────────
# 4. coordinator.py: 저장소 복구 및 저장 (1:1 복구)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_restore_daily_temps_success(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    coord = KMAWeatherUpdateCoordinator(hass, get_mock_entry("restore_test"))
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    coord._store.async_load = AsyncMock(return_value={"date": today, "max": 28.5, "wf_am": "맑음"})

    await coord._restore_daily_temps()
    assert coord._daily_max_temp == 28.5
    assert coord._wf_am_today == "맑음"

@pytest.mark.asyncio
async def test_save_daily_temps(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    coord = KMAWeatherUpdateCoordinator(hass, get_mock_entry("save_test"))
    coord._daily_date = date(2025, 6, 1)
    coord._daily_max_temp = 30.0
    
    saved = {}
    coord._store.async_save = AsyncMock(side_effect=saved.update)
    await coord._save_daily_temps()
    assert saved["date"] == "20250601"
    assert saved["max"] == 30.0

# ─────────────────────────────────────────────────────────────────────────────
# 5. button.py: 수동 업데이트 및 쿨다운
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_button_press_cooldown(hass, kma_api_mock_factory):
    from custom_components.kma_weather.button import KMAUpdateButton
    kma_api_mock_factory("full_test")
    coord = MagicMock()
    coord.async_request_refresh = AsyncMock()
    button = KMAUpdateButton(coord, get_mock_entry())

    # 1회차 누름
    await button.async_press()
    # 2회차 누름 (3초 후 - 쿨다운 작동해야 함)
    button._last_press = datetime.now() - timedelta(seconds=3)
    await button.async_press()
    
    assert coord.async_request_refresh.call_count == 1

# ─────────────────────────────────────────────────────────────────────────────
# 6. config_flow.py: OptionsFlow 검증
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_options_flow(hass, mock_config_entry):
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], 
        user_input={"location_entity": "zone.home", "apply_date": "2025-01-01"}
    )
    assert result2["type"] == "create_entry"

# ─────────────────────────────────────────────────────────────────────────────
# 7. 기타 매핑 검증 (Grade, Sky)
# ─────────────────────────────────────────────────────────────────────────────
class TestTranslateGrade:
    @pytest.mark.parametrize("grade,expected", [("1", "좋음"), ("2", "보통"), (None, "정보없음")])
    def test_grades(self, grade, expected):
        api = KMAWeatherAPI(MagicMock(), "test_key", "r1", "r2")
        assert api._translate_grade(grade) == expected

def test_haversine_same_point():
    from custom_components.kma_weather.coordinator import _haversine
    assert _haversine(37.5, 127.0, 37.5, 127.0) == pytest.approx(0.0)
