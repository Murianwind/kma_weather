import pytest
import logging
from urllib.parse import quote
from unittest.mock import MagicMock, patch
from custom_components.kma_weather.api_kma import KMAWeatherAPI

class MockAiohttpResponse:
    def __init__(self, json_data=None, should_raise=False, status=200):
        self._json_data = json_data or {}
        self._should_raise = should_raise
        self.status = status  # _fetch의 response.status 체크 대응
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

def test_api_key_decoding():
    original_key = "test_secret_key!@#"
    encoded_key = quote(original_key)
    api = KMAWeatherAPI(None, encoded_key)
    assert api.api_key == original_key

@pytest.mark.asyncio
async def test_fetch_http_error(caplog):
    session = MockAiohttpSession(should_raise=True)
    api = KMAWeatherAPI(session, "TEST_KEY")
    with caplog.at_level(logging.ERROR):
        result = await api._fetch("http://example.com", {})
    assert result is None
    assert any(msg in caplog.text for msg in ["알 수 없는 API 오류", "API 호출 실패"])

@pytest.mark.asyncio
async def test_nominatim_user_agent():
    session = MockAiohttpSession(json_data={"address": {"city": "서울특별시", "borough": "강남구"}})
    api = KMAWeatherAPI(session, "TEST_KEY")
    address = await api._get_address(37.56, 126.98)
    assert address == "서울특별시 강남구"
    assert "headers" in session.last_kwargs
    assert "KMA-Weather" in session.last_kwargs["headers"].get("User-Agent", "")

@pytest.mark.asyncio
async def test_nominatim_user_agent_with_hass_uuid():
    class MockHass:
        installation_uuid = "12345678-1234-5678-1234-567812345678"
    session = MockAiohttpSession(json_data={"address": {"city": "서울특별시", "borough": "강남구"}})
    api = KMAWeatherAPI(session, "TEST_KEY", hass=MockHass())
    await api._get_address(37.56, 126.98)
    user_agent = session.last_kwargs["headers"]["User-Agent"]
    assert "HomeAssistant-KMA-Weather" in user_agent
    assert "123456781234" in user_agent

@pytest.mark.asyncio
async def test_coordinator_passes_hass_to_api(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {
        "api_key": "TEST_KEY", "nx": 60, "ny": 127,
        "reg_id_temp": "11B10101", "reg_id_land": "11B00000"
    }
    entry.options = {}
    entry.entry_id = "api_hass_test"
    with patch("custom_components.kma_weather.coordinator.KMAWeatherAPI") as mock_api:
        mock_api.return_value = MagicMock()
        KMAWeatherUpdateCoordinator(hass, entry)
    mock_api.assert_called_once()
    _, kwargs = mock_api.call_args
    assert kwargs.get("hass") is hass
