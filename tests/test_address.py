import pytest
from custom_components.kma_weather.api_kma import KMAWeatherAPI

@pytest.mark.asyncio
async def test_address_conversion_logic(hass, aioclient_mock):
    """OSM Nominatim API 응답 파싱 로직 검증"""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    
    session = async_get_clientsession(hass)
    api = KMAWeatherAPI(session, "mock_key", "reg_temp", "reg_land")

    # URL 파라미터까지 고려하여 Mock 설정
    aioclient_mock.get(
        "https://nominatim.openstreetmap.org/reverse?format=json&lat=37.56&lon=126.98&zoom=16",
        json={
            "address": {
                "city": "경기도",
                "borough": "화성시",
                "suburb": "봉담읍"
            }
        }
    )

    address = await api._get_address(37.56, 126.98)
    assert address == "경기도 화성시 봉담읍"
