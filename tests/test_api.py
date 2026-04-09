import pytest
import logging
from urllib.parse import quote
from custom_components.kma_weather.api_kma import KMAWeatherAPI

# --- aiohttp 통신을 완벽하게 흉내내는 Mock 클래스 ---
class MockAiohttpResponse:
    def __init__(self, json_data=None, should_raise=False):
        self._json_data = json_data or {}
        self._should_raise = should_raise

    def raise_for_status(self):
        if self._should_raise:
            # 에러 발생 상황 시뮬레이션
            raise Exception("HTTP 500 Internal Server Error")

    async def json(self, *args, **kwargs):
        return self._json_data

    # async with 구문을 지원하기 위한 필수 메서드
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

class MockAiohttpSession:
    def __init__(self, json_data=None, should_raise=False):
        self.json_data = json_data
        self.should_raise = should_raise
        self.last_kwargs = {}

    def get(self, url, **kwargs):
        self.last_kwargs = kwargs
        return MockAiohttpResponse(json_data=self.json_data, should_raise=self.should_raise)

# =========================================================

# --- 1. API 키 디코딩 검증 테스트 ---
def test_api_key_decoding():
    """URL 인코딩된 API 키가 정상적으로 디코딩되는지 확인"""
    encoded_key = quote("test_secret_key!@#")
    api = KMAWeatherAPI(None, encoded_key, "TEMP", "LAND")
    
    assert api.api_key == "test_secret_key!@#"

# --- 2. HTTP 에러 및 Bare Exception 처리 검증 테스트 ---
@pytest.mark.asyncio
async def test_fetch_http_error(caplog):
    """API 호출 중 HTTP 에러 발생 시 프로그램이 멈추지 않고 에러 로그를 남기는지 확인"""
    session = MockAiohttpSession(should_raise=True)
    api = KMAWeatherAPI(session, "TEST_KEY", "TEMP", "LAND")
    
    with caplog.at_level(logging.ERROR):
        result = await api._fetch("http://example.com", {})
    
    assert result is None
    # [수정] api_kma.py의 새로운 로그 문구와 일치시킵니다.
    assert "알 수 없는 API 오류" in caplog.text or "API 호출 실패" in caplog.text

# --- 3. Nominatim User-Agent 포함 여부 검증 테스트 ---
@pytest.mark.asyncio
async def test_nominatim_user_agent():
    """OpenStreetMap 주소 API 호출 시 헤더에 User-Agent가 정상적으로 포함되는지 확인"""
    # 서울 주소를 반환하는 가짜 세션 생성
    session = MockAiohttpSession(json_data={
        "address": {"city": "서울특별시", "borough": "강남구"}
    })
    api = KMAWeatherAPI(session, "TEST_KEY", "TEMP", "LAND")
    
    address = await api._get_address(37.56, 126.98)
    
    # 1. Mock 데이터가 제대로 파싱되어 주소 문자열이 나왔는가?
    assert address == "서울특별시 강남구"
    
    # 2. session.get이 호출될 때 우리가 지정한 User-Agent가 들어갔는가?
    # 2. session.get이 호출될 때 User-Agent가 포함되어 있는가?
    assert "headers" in session.last_kwargs
    user_agent = session.last_kwargs["headers"].get("User-Agent", "")
    
    # [수정] 특정 문자열 대신 'KMA-Weather'가 포함되어 있는지, 
    # 혹은 현재 소스 코드의 형식(HomeAssistant-KMA-Weather-...)을 따르는지 검증
    assert "KMA-Weather" in user_agent

# --- 4. Home Assistant UUID 기반 User-Agent 검증 테스트 ---
@pytest.mark.asyncio
async def test_nominatim_user_agent_with_hass_uuid():
    class MockHass:
        def __init__(self):
            self.installation_uuid = "12345678-1234-5678-1234-567812345678"

    hass = MockHass()

    session = MockAiohttpSession(json_data={
        "address": {"city": "서울특별시", "borough": "강남구"}
    })

    api = KMAWeatherAPI(
        session,
        "TEST_KEY",
        "TEMP",
        "LAND",
        hass=hass
    )

    address = await api._get_address(37.56, 126.98)

    assert address == "서울특별시 강남구"
    assert "headers" in session.last_kwargs

    user_agent = session.last_kwargs["headers"]["User-Agent"]
    assert "HomeAssistant-KMA-Weather" in user_agent
    assert "123456781234" in user_agent


# --- 5. Coordinator에서 hass 전달 검증 테스트 ---
from unittest.mock import MagicMock, patch
from custom_components.kma_weather.coordinator import (
    KMAWeatherUpdateCoordinator,
)

@pytest.mark.asyncio
async def test_coordinator_passes_hass_to_api():
    """Coordinator가 API 객체를 생성할 때 hass를 정확히 전달하는지 검증"""
    hass = MagicMock()
    # UUID 설정
    hass.installation_uuid = "12345678-1234-5678-1234-567812345678"

    entry = MagicMock()
    # 좌표 및 필수 데이터 설정
    entry.data = {
        "api_key": "TEST_KEY",
        "nx": 60,
        "ny": 127,
        "reg_id_temp": "11B10101",
        "reg_id_land": "11B00000",
    }
    entry.options = {}

    # Coordinator가 사용하는 KMAWeatherAPI 클래스를 패치
    with patch(
        "custom_components.kma_weather.coordinator.KMAWeatherAPI"
    ) as mock_api:
        mock_api.return_value = MagicMock()
        
        # Coordinator 인스턴스 생성
        KMAWeatherUpdateCoordinator(hass, entry)

        # 검증: API가 한 번 호출되었는가?
        mock_api.assert_called_once()
        
        # 검증: 호출 시 전달된 인자 중 hass가 포함되어 있는가?
        # call_args는 (args, kwargs) 튜플을 반환함
        _, kwargs = mock_api.call_args
        
        # coordinator.py에서 hass=hass 로 전달하므로 kwargs["hass"]를 체크
        assert kwargs.get("hass") is hass
