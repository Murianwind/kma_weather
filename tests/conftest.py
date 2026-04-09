import pytest
from unittest.mock import patch
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """커스텀 컴포넌트를 테스트 환경에서 사용할 수 있도록 허용"""
    yield

@pytest.fixture
def mock_kma_api():
    """기상청 API 응답을 가상으로 꾸며냄 (Mock)"""
    with patch("custom_components.kma_weather.api_kma.KmaAPI.get_weather") as mock:
        # 기본 성공 응답 샘플
        mock.return_value = {
            "current_temp": 21.0,
            "humidity": 45,
            "sky_condition": "맑음",
            "rain_type": "없음",
            "pm10": 35,
            "pm25": 15,
        }
        yield mock

@pytest.fixture
def mock_config_entry():
    """기본 테스트용 설정 엔트리"""
    return MockConfigEntry(
        domain="kma_weather",
        title="집 날씨",
        data={
            "api_key": "valid_mock_key",
            "location_mode": "zone",
            "prefix": "test_home",
            "zone_id": "zone.home"
        },
        entry_id="12345"
    )
