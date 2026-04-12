import pytest
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import unquote

# 필요한 모든 클래스 전역 import (NameError 방지)
from custom_components.kma_weather.api_kma import KMAWeatherAPI, _safe_float
from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator, _haversine, _land_code
from custom_components.kma_weather.button import KMAUpdateButton
from custom_components.kma_weather.const import DOMAIN
from pytest_homeassistant_custom_component.common import MockConfigEntry

# ─────────────────────────────────────────────────────────────────────────────
# [Given] 공통 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
def get_mock_entry(entry_id="test"):
    entry = MagicMock()
    entry.data = {"api_key": "test_key", "location_entity": "zone.home", "prefix": "test"}
    entry.options = {}
    entry.entry_id = entry_id
    return entry

# ─────────────────────────────────────────────────────────────────────────────
# 1. api_kma.py: _safe_float (6개 시나리오)
# ─────────────────────────────────────────────────────────────────────────────
class TestSafeFloat:
    def test_none_returns_none(self):
        # Given: None 입력 / When: 호출 / Then: None
        assert _safe_float(None) is None

    def test_empty_string_returns_none(self):
        # Given: 빈 문자열 / When: 호출 / Then: None
        assert _safe_float("") is None

    def test_dash_returns_none(self):
        # Given: "-" / When: 호출 / Then: None
        assert _safe_float("-") is None

    def test_valid_int_string(self):
        # Given: "22" / When: 호출 / Then: 22.0
        assert _safe_float("22") == 22.0

    def test_valid_float_string(self):
        # Given: "3.14" / When: 호출 / Then: 3.14
        assert _safe_float("3.14") == pytest.approx(3.14)

    def test_invalid_string_returns_none(self):
        # Given: "abc" / When: 호출 / Then: None
        assert _safe_float("abc") is None

# ─────────────────────────────────────────────────────────────────────────────
# 2. api_kma.py: _calculate_apparent_temp (5개 시나리오)
# ─────────────────────────────────────────────────────────────────────────────
class TestApparentTemp:
    def _api(self): return KMAWeatherAPI(MagicMock(), "key", "r1", "r2")

    def test_wind_chill_branch(self):
        # Given: 추운 날씨 / When: 계산 / Then: 체감온도 하락
        api = self._api()
        assert api._calculate_apparent_temp(temp=5, reh=60, wsd=3) < 5

    def test_heat_index_branch(self):
        # Given: 더운 날씨 / When: 계산 / Then: float 결과
        api = self._api()
        assert isinstance(api._calculate_apparent_temp(temp=30, reh=70, wsd=1), float)

    def test_default_branch_returns_temp(self):
        # Given: 평온한 날씨 / When: 계산 / Then: 기온 유지
        api = self._api()
        assert api._calculate_apparent_temp(temp=20, reh=30, wsd=0.5) == 20

    def test_none_temp_returns_none(self):
        # Given: 기온 없음 / When: 계산 / Then: None
        api = self._api()
        assert api._calculate_apparent_temp(temp=None, reh=50, wsd=2) is None

    def test_string_temp_parsed(self):
        # Given: 문자열 "15" / When: 계산 / Then: 15.0
        api = self._api()
        assert api._calculate_apparent_temp(temp="15", reh=50, wsd=0) == 15

# ─────────────────────────────────────────────────────────────────────────────
# 3. api_kma.py: 대기질 조회 (4개 시나리오 - TypeError 해결)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_air_quality_cache_hit():
    # Given: 유효한 캐시 데이터 존재
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    api.lat, api.lon = 37.56, 126.98
    api._cached_station, api._cached_lat_lon = "화성", (37.56, 126.98)
    api._station_cache_time = datetime.now(ZoneInfo("Asia/Seoul"))

    # When: 조회 시도 (인자 개수 불일치 문제 수정: params, timeout 추가)
    async def mock_fetch(url, params=None, timeout=10):
        assert "MsrstnInfoInqireSvc" not in url
        return {"response": {"body": {"items": [{"pm10Value": "40", "pm10Grade": "2"}]}}}
    
    api._fetch = mock_fetch
    result = await api._get_air_quality()

    # Then: 캐시된 역명 확인
    assert result["station"] == "화성"

@pytest.mark.asyncio
async def test_air_quality_no_station_items():
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
    api._fetch = AsyncMock(return_value={"response": {"body": {"items": []}}})
    assert await api._get_air_quality() == {}

@pytest.mark.asyncio
async def test_air_quality_no_air_data_items():
    api = KMAWeatherAPI(MagicMock(), "key", "r1", "r2")
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
    api._fetch = AsyncMock(return_value=None)
    assert await api._get_air_quality() == {}

# ─────────────────────────────────────────────────────────────────────────────
# 4. coordinator.py: 저장소 복구 (3개 시나리오 - NameError 해결)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_restore_daily_temps_success(hass):
    coord = KMAWeatherUpdateCoordinator(hass, get_mock_entry())
    today_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    coord._store.async_load = AsyncMock(return_value={
        "date": today_str, "max": 28.5, "min": 12.0, "wf_am": "맑음", "wf_pm": "흐림"
    })
    await coord._restore_daily_temps()
    assert coord._daily_max_temp == 28.5
    assert coord._wf_am_today == "맑음"

@pytest.mark.asyncio
async def test_restore_daily_temps_date_mismatch(hass):
    coord = KMAWeatherUpdateCoordinator(hass, get_mock_entry())
    coord._store.async_load = AsyncMock(return_value={"date": "20200101", "max": 99.0})
    await coord._restore_daily_temps()
    assert coord._daily_max_temp is None

@pytest.mark.asyncio
async def test_restore_daily_temps_empty_store(hass):
    coord = KMAWeatherUpdateCoordinator(hass, get_mock_entry())
    coord._store.async_load = AsyncMock(return_value=None)
    await coord._restore_daily_temps()
    assert coord._store_loaded is True

# ─────────────────────────────────────────────────────────────────────────────
# 5. button.py: 수동 업데이트 및 쿨다운 (2개 시나리오)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_button_press_triggers_refresh(hass, kma_api_mock_factory):
    hass.config.latitude, hass.config.longitude = 37.56, 126.98
    entry = MockConfigEntry(domain=DOMAIN, data={"api_key": "k", "prefix": "btn"}, entry_id="b1")
    kma_api_mock_factory("full_test")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.async_request_refresh = AsyncMock()
    
    # 서비스 호출을 통한 버튼 누름
    await hass.services.async_call("button", "press", target={"entity_id": "button.btn_manual_update"}, blocking=True)
    coordinator.async_request_refresh.assert_called_once()

@pytest.mark.asyncio
async def test_button_press_cooldown(hass):
    coord = MagicMock()
    coord.async_request_refresh = AsyncMock()
    button = KMAUpdateButton(coord, get_mock_entry())
    
    await button.async_press() # 1회
    button._last_press = datetime.now() - timedelta(seconds=3)
    await button.async_press() # 2회 (쿨다운 중)
    assert coord.async_request_refresh.call_count == 1
