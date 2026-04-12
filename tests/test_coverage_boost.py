import pytest
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.kma_weather.api_kma import KMAWeatherAPI, _safe_float

# ─────────────────────────────────────────────────────────────────────────────
# 1. api_kma.py 유틸리티 검증 (safe_float, calculate_apparent_temp, etc.)
# ─────────────────────────────────────────────────────────────────────────────

class TestSafeFloat:
    """시나리오: 다양한 타입의 입력을 안전하게 부동 소수점으로 변환함"""
    def test_safe_float_cases(self):
        # Given: 다양한 형태의 입력값(None, 빈 문자열, 정상 숫자 문자열 등)이 주어졌을 때
        # When: _safe_float를 호출하면
        # Then: 타입에 따라 None 또는 정확한 float 값이 반환되어야 함
        assert _safe_float(None) is None
        assert _safe_float("") is None
        assert _safe_float("-") is None
        assert _safe_float("22") == 22.0
        assert _safe_float("3.14") == pytest.approx(3.14)
        assert _safe_float("abc") is None

class TestApparentTemp:
    """시나리오: 기온, 습도, 풍속을 기반으로 체감 온도를 산출함"""
    def _api(self): return KMAWeatherAPI(MagicMock(), "key", "r1", "r2")

    def test_apparent_temp_logic(self):
        api = self._api()
        # Given: 추운 날씨(풍속 있음), 더운 날씨(습도 높음), 평범한 날씨 조건
        # When: 체감 온도를 계산하면
        chill = api._calculate_apparent_temp(temp=5, reh=60, wsd=3)
        heat = api._calculate_apparent_temp(temp=30, reh=70, wsd=1)
        normal = api._calculate_apparent_temp(temp=20, reh=30, wsd=0.5)

        # Then: 각각 풍속 냉각, 열지수, 또는 기본 기온이 반환되어야 함
        assert chill < 5
        assert isinstance(heat, float)
        assert normal == 20
        assert api._calculate_apparent_temp(None, 50, 2) is None
        assert api._calculate_apparent_temp("15", 50, 0) == 15

class TestGetVecKor:
    """시나리오: 풍향 각도(0-360)를 한글 8방위로 변환함"""
    @pytest.mark.parametrize("vec,expected", [
        (0, "북"), (22.5, "북동"), (67.5, "동"), (112.5, "남동"),
        (157.5, "남"), (202.5, "남서"), (247.5, "서"), (292.5, "북서"),
        (337.5, "북"), (360, "북"),
    ])
    def test_directions(self, vec, expected):
        # Given: 풍향 각도가 주어졌을 때
        api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
        # When: 한글 방위로 변환하면 / Then: 기대한 방위명과 일치해야 함
        assert api._get_vec_kor(vec) == expected
        assert api._get_vec_kor(None) is None

# ─────────────────────────────────────────────────────────────────────────────
# 2. 대기질 및 외부 API 연동 검증 (_get_air_quality)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_air_quality_cache_logic():
    """시나리오: 캐시된 위치 정보가 있다면 측정소 재조회 없이 대기질을 가져옴"""
    # Given: 유효한 측정소 캐시와 위치 정보가 저장되어 있을 때
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    api.lat, api.lon = 37.56, 126.98
    api._cached_station, api._cached_lat_lon = "화성", (37.56, 126.98)
    api._station_cache_time = datetime.now(ZoneInfo("Asia/Seoul"))

    # When: 대기질 정보를 요청하면 (fetch 모킹을 통해 재조회 여부 확인)
    async def mock_fetch(url, params=None, timeout=10):
        assert "MsrstnInfoInqireSvc" not in url  # Then: 측정소 조회 호출이 없어야 함
        return {"response": {"body": {"items": [{"pm10Value": "40", "pm10Grade": "2"}]}}}
    
    api._fetch = mock_fetch
    result = await api._get_air_quality()

    # Then: 캐시된 측정소를 사용하여 정보를 반환함
    assert result["station"] == "화성"
    assert result["pm10Grade"] == "보통"

# ─────────────────────────────────────────────────────────────────────────────
# 3. 코디네이터 데이터 영속성 검증 (Restore/Save)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_coordinator_storage_logic(hass):
    """시나리오: 저장소로부터 오늘의 최고/최저 기온 및 날씨 상태를 복구함"""
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    # Given: 오늘 날짜의 유효한 기온 데이터가 저장소에 존재할 때
    coord = KMAWeatherUpdateCoordinator(hass, MagicMock(entry_id="test", data={}, options={}))
    today_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    coord._store.async_load = AsyncMock(return_value={"date": today_str, "max": 28.5, "min": 12.0})

    # When: 데이터를 복구(_restore_daily_temps)하면
    await coord._restore_daily_temps()

    # Then: 코디네이터 내부 변수에 해당 수치가 올바르게 할당되어야 함
    assert coord._daily_max_temp == 28.5
    assert coord._store_loaded is True

# ─────────────────────────────────────────────────────────────────────────────
# 4. 버튼 및 설정 흐름 검증 (Button, ConfigFlow)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_button_press_and_cooldown(hass, kma_api_mock_factory):
    """시나리오: 수동 업데이트 버튼 작동 및 5초 쿨다운 제한을 확인"""
    # Given: 통합 구성요소가 로드되고 버튼 엔티티가 생성되었을 때
    from custom_components.kma_weather.button import KMAUpdateButton
    kma_api_mock_factory("full_test")
    # (엔티티 셋업 로직 생략 - 원본 로직 유지)
    coord = MagicMock()
    coord.async_request_refresh = AsyncMock()
    button = KMAUpdateButton(coord, MagicMock())

    # When: 버튼을 연속해서 누르면
    await button.async_press() # 1회차
    button._last_press = datetime.now() - timedelta(seconds=3)
    await button.async_press() # 2회차 (3초 후 - 쿨다운 걸림)
    
    # Then: 쿨다운 제한으로 인해 실제 리프레시는 1회만 호출되어야 함
    assert coord.async_request_refresh.call_count == 1

@pytest.mark.asyncio
async def test_options_flow_logic(hass, mock_config_entry):
    """시나리오: 사용자 설정 변경(OptionsFlow)이 정상적으로 저장됨"""
    # Given: 설정 화면(OptionsFlow)이 초기화되었을 때
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    # When: 사용자가 위치 엔티티 등을 변경하고 설정을 완료하면
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"location_entity": "zone.home"}
    )

    # Then: 설정 변경 결과가 'create_entry' 타입으로 성공적으로 반환되어야 함
    assert result2["type"] == "create_entry"
