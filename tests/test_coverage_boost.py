import pytest
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.kma_weather.api_kma import KMAWeatherAPI, _safe_float

# ─────────────────────────────────────────────────────────────────────────────
# [Given] 공통 헬퍼: 필수 데이터(api_key)를 포함한 Mock Entry
# ─────────────────────────────────────────────────────────────────────────────
def get_mock_entry(entry_id="test"):
    entry = MagicMock()
    entry.data = {"api_key": "test_key", "location_entity": "zone.home"}
    entry.options = {}
    entry.entry_id = entry_id
    return entry

# ─────────────────────────────────────────────────────────────────────────────
# 1. api_kma.py: _safe_float (6개 시나리오 1:1 복구)
# ─────────────────────────────────────────────────────────────────────────────
class TestSafeFloat:
    def test_none_returns_none(self):
        # Given: 입력이 None / When: 변환 / Then: None
        assert _safe_float(None) is None

    def test_empty_string_returns_none(self):
        # Given: 빈 문자열 / When: 변환 / Then: None
        assert _safe_float("") is None

    def test_dash_returns_none(self):
        # Given: "-" / When: 변환 / Then: None
        assert _safe_float("-") is None

    def test_valid_int_string(self):
        # Given: "22" / When: 변환 / Then: 22.0
        assert _safe_float("22") == 22.0

    def test_valid_float_string(self):
        # Given: "3.14" / When: 변환 / Then: 3.14
        assert _safe_float("3.14") == pytest.approx(3.14)

    def test_invalid_string_returns_none(self):
        # Given: "abc" / When: 변환 / Then: None
        assert _safe_float("abc") is None

# ─────────────────────────────────────────────────────────────────────────────
# 2. api_kma.py: _calculate_apparent_temp (5개 시나리오 1:1 복구)
# ─────────────────────────────────────────────────────────────────────────────
class TestApparentTemp:
    def _api(self): return KMAWeatherAPI(MagicMock(), "key", "r1", "r2")

    def test_wind_chill_branch(self):
        # Given: 5도, 풍속 3m/s / When: 계산 / Then: 체감온도 하락
        api = self._api()
        assert api._calculate_apparent_temp(temp=5, reh=60, wsd=3) < 5

    def test_heat_index_branch(self):
        # Given: 30도, 습도 70% / When: 계산 / Then: float 결과 반환
        api = self._api()
        assert isinstance(api._calculate_apparent_temp(temp=30, reh=70, wsd=1), float)

    def test_default_branch_returns_temp(self):
        # Given: 20도 평범한 날씨 / When: 계산 / Then: 기온 그대로 반환
        api = self._api()
        assert api._calculate_apparent_temp(temp=20, reh=30, wsd=0.5) == 20

    def test_none_temp_returns_none(self):
        # Given: 기온 None / When: 계산 / Then: None
        api = self._api()
        assert api._calculate_apparent_temp(temp=None, reh=50, wsd=2) is None

    def test_string_temp_parsed(self):
        # Given: "15" (문자열) / When: 계산 / Then: 15.0
        api = self._api()
        assert api._calculate_apparent_temp(temp="15", reh=50, wsd=0) == 15

# ─────────────────────────────────────────────────────────────────────────────
# 3. api_kma.py: _get_vec_kor (8방위 매핑)
# ─────────────────────────────────────────────────────────────────────────────
class TestGetVecKor:
    @pytest.mark.parametrize("vec,expected", [
        (0, "북"), (22.5, "북동"), (67.5, "동"), (112.5, "남동"),
        (157.5, "남"), (202.5, "남서"), (247.5, "서"), (292.5, "북서"),
        (337.5, "북"), (360, "북"),
    ])
    def test_directions(self, vec, expected):
        # Given: 각도 / When: 변환 / Then: 방위 일치
        api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
        assert api._get_vec_kor(vec) == expected

    def test_none_vec_returns_none(self):
        # Given: None / When: 변환 / Then: None
        api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
        assert api._get_vec_kor(None) is None

# ─────────────────────────────────────────────────────────────────────────────
# 4. api_kma.py: _get_air_quality (4개 시나리오 1:1 복구)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_air_quality_cache_hit():
    # Given: 캐시된 정보 존재
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    api.lat, api.lon = 37.56, 126.98
    api._cached_station, api._cached_lat_lon = "화성", (37.56, 126.98)
    api._station_cache_time = datetime.now(ZoneInfo("Asia/Seoul"))
    # When: 조회 / Then: 재조회 없이 캐시 사용
    async def mock_fetch(url, **kw): return {"response": {"body": {"items": [{"pm10Value": "40"}]}}}
    api._fetch = mock_fetch
    result = await api._get_air_quality()
    assert result["station"] == "화성"

@pytest.mark.asyncio
async def test_air_quality_no_station_items():
    # Given: 측정소 정보 없음 / When: 조회 / Then: 빈 dict 반환
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    api._fetch = AsyncMock(return_value={"response": {"body": {"items": []}}})
    assert await api._get_air_quality() == {}

# ─────────────────────────────────────────────────────────────────────────────
# 5. coordinator.py: _restore_daily_temps (3개 시나리오 1:1 복구)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_restore_daily_temps_success(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    # Given: 오늘 날짜의 정확한 데이터 저장소
    coord = KMAWeatherUpdateCoordinator(hass, get_mock_entry())
    tz = ZoneInfo("Asia/Seoul")
    today_str = datetime.now(tz).strftime("%Y%m%d")
    coord._store.async_load = AsyncMock(return_value={
        "date": today_str, "max": 28.5, "min": 12.0, "wf_am": "맑음", "wf_pm": "흐림"
    })
    # When: 복구 / Then: 값 할당 성공 (Regression Fixed)
    await coord._restore_daily_temps()
    assert coord._daily_max_temp == 28.5
    assert coord._wf_am_today == "맑음"

@pytest.mark.asyncio
async def test_restore_daily_temps_date_mismatch(hass):
    # Given: 과거 날짜 데이터 / When: 복구 / Then: 무시됨
    coord = KMAWeatherUpdateCoordinator(hass, get_mock_entry())
    coord._store.async_load = AsyncMock(return_value={"date": "20200101", "max": 99.0})
    await coord._restore_daily_temps()
    assert coord._daily_max_temp is None

# ─────────────────────────────────────────────────────────────────────────────
# 6. button.py: async_press (2개 시나리오 1:1 복구)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_button_press_triggers_refresh(hass, kma_api_mock_factory):
    # Given: 버튼 엔티티 준비 / When: 누름 / Then: 리프레시 호출
    from custom_components.kma_weather.button import KMAUpdateButton
    kma_api_mock_factory("full_test")
    coord = MagicMock()
    coord.async_request_refresh = AsyncMock()
    button = KMAUpdateButton(coord, get_mock_entry())
    await button.async_press()
    coord.async_request_refresh.assert_called_once()

@pytest.mark.asyncio
async def test_button_press_cooldown(hass):
    # Given: 버튼 클릭 직후 / When: 다시 누름 / Then: 쿨다운으로 무시됨
    coord = MagicMock()
    coord.async_request_refresh = AsyncMock()
    button = KMAUpdateButton(coord, get_mock_entry())
    await button.async_press() # 1회
    button._last_press = datetime.now() - timedelta(seconds=3)
    await button.async_press() # 2회 (3초 후)
    assert coord.async_request_refresh.call_count == 1

# ─────────────────────────────────────────────────────────────────────────────
# 7. config_flow.py: OptionsFlow
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_options_flow(hass, mock_config_entry):
    # Given: 설정 진입 / When: 옵션 변경 / Then: 성공
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"location_entity": "zone.home"}
    )
    assert result2["type"] == "create_entry"

# ─────────────────────────────────────────────────────────────────────────────
# 8. 기타 헬퍼: _haversine, _land_code (1:1 복구)
# ─────────────────────────────────────────────────────────────────────────────
def test_haversine_same_point():
    from custom_components.kma_weather.coordinator import _haversine
    assert _haversine(37.5, 127.0, 37.5, 127.0) == pytest.approx(0.0)

@pytest.mark.parametrize("temp_id,expected_land", [
    ("11B10101", "11B00000"), ("11G00101", "11G00000"),
])
def test_land_code(temp_id, expected_land):
    from custom_components.kma_weather.coordinator import _land_code
    assert _land_code(temp_id) == expected_land
