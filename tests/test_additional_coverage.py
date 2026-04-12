import pytest
import logging
from urllib.parse import quote
from unittest.mock import MagicMock, patch
from custom_components.kma_weather.api_kma import KMAWeatherAPI

# --- 테스트를 위한 Mock 객체들 ---

class MockAiohttpResponse:
    def __init__(self, json_data=None, should_raise=False):
        self._json_data = json_data or {}
        self._should_raise = should_raise

    def raise_for_status(self):
        if self._should_raise:
            raise Exception("HTTP 500 Internal Server Error")

    async def json(self, *args, **kwargs): return self._json_data
    async def __aenter__(self): return self
    async def __aexit__(self, exc_type, exc, tb): pass

class MockAiohttpSession:
    def __init__(self, json_data=None, should_raise=False):
        self.json_data = json_data
        self.should_raise = should_raise
        self.last_kwargs = {}

    def get(self, url, **kwargs):
        self.last_kwargs = kwargs
        return MockAiohttpResponse(json_data=self.json_data, should_raise=self.should_raise)

# --- 테스트 케이스 ---

def test_api_key_decoding():
    """시나리오: URL 인코딩된 API 키를 자동으로 디코딩함"""
    
    # Given: 특수문자가 포함되어 인코딩된 API 키가 주어졌을 때
    encoded_key = quote("test_secret_key!@#")
    
    # When: API 객체를 초기화하면
    api = KMAWeatherAPI(None, encoded_key, "TEMP", "LAND")
    
    # Then: 내부적으로는 디코딩된 원래의 키가 저장되어야 함
    assert api.api_key == "test_secret_key!@#"


@pytest.mark.asyncio
async def test_fetch_http_error(caplog):
    """시나리오: API 호출 중 HTTP 오류 발생 시 에러를 로그에 남기고 None을 반환함"""
    
    # Given: HTTP 오류를 일으키도록 설정된 세션과 API 객체
    session = MockAiohttpSession(should_raise=True)
    api = KMAWeatherAPI(session, "TEST_KEY", "TEMP", "LAND")
    
    # When: 데이터를 가져오려고 시도할 때
    with caplog.at_level(logging.ERROR):
        result = await api._fetch("http://example.com", {})
    
    # Then: 결과는 None이어야 하며, 로그에 적절한 에러 메시지가 남아야 함
    assert result is None
    assert any(msg in caplog.text for msg in ["알 수 없는 API 오류", "API 호출 실패"])


@pytest.mark.asyncio
async def test_nominatim_user_agent():
    """시나리오: 주소 검색(Nominatim) 시 기본 User-Agent 헤더를 포함함"""
    
    # Given: 주소 데이터를 반환하는 세션과 API 객체
    session = MockAiohttpSession(json_data={"address": {"city": "서울특별시", "borough": "강남구"}})
    api = KMAWeatherAPI(session, "TEST_KEY", "TEMP", "LAND")
    
    # When: 좌표로 주소를 조회할 때
    address = await api._get_address(37.56, 126.98)
    
    # Then: 주소가 올바르게 조합되고, 요청 헤더에 'KMA-Weather'가 포함되어야 함
    assert address == "서울특별시 강남구"
    assert "headers" in session.last_kwargs
    assert "KMA-Weather" in session.last_kwargs["headers"].get("User-Agent", "")


@pytest.mark.asyncio
async def test_nominatim_user_agent_with_hass_uuid():
    """시나리오: Home Assistant 환경에서 호출 시 UUID가 포함된 User-Agent를 사용함"""
    
    # Given: Hass UUID를 가진 Mock Hass 객체와 API 객체
    class MockHass:
        installation_uuid = "12345678-1234-5678-1234-567812345678"
    
    session = MockAiohttpSession(json_data={"address": {"city": "서울특별시", "borough": "강남구"}})
    api = KMAWeatherAPI(session, "TEST_KEY", "TEMP", "LAND", hass=MockHass())
    
    # When: 주소를 조회할 때
    await api._get_address(37.56, 126.98)
    
    # Then: User-Agent에 HomeAssistant 명칭과 UUID의 일부가 포함되어야 함
    user_agent = session.last_kwargs["headers"]["User-Agent"]
    assert "HomeAssistant-KMA-Weather" in user_agent
    assert "123456781234" in user_agent


@pytest.mark.asyncio
async def test_coordinator_passes_hass_to_api(hass):
    """시나리오: 코디네이터 생성 시 Hass 인스턴스를 API 객체에 올바르게 전달함"""
    
    # Given: 테스트용 config entry 설정
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {
        "api_key": "TEST_KEY", "nx": 60, "ny": 127,
        "reg_id_temp": "11B10101", "reg_id_land": "11B00000"
    }
    entry.options = {}
    entry.entry_id = "api_hass_test"

    # When: 코디네이터를 생성할 때 (API 생성을 패치하여 감시)
    with patch("custom_components.kma_weather.coordinator.KMAWeatherAPI") as mock_api:
        mock_api.return_value = MagicMock()
        KMAWeatherUpdateCoordinator(hass, entry)
    
    # Then: KMAWeatherAPI가 호출되어야 하며, 인자로 받은 hass가 전달되어야 함
    mock_api.assert_called_once()
    _, kwargs = mock_api.call_args
    assert kwargs.get("hass") is hass
