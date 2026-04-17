import pytest
from custom_components.kma_weather.api_kma import KMAWeatherAPI

@pytest.mark.asyncio
async def test_address_conversion_logic(hass, aioclient_mock):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    session = async_get_clientsession(hass)
    api = KMAWeatherAPI(session, "mock_key")

    lat, lon = 37.56, 126.98
    aioclient_mock.get(
        "https://nominatim.openstreetmap.org/reverse",
        params={"format": "json", "lat": str(lat), "lon": str(lon), "zoom": "16"},
        json={"address": {"city": "경기도", "borough": "화성시", "suburb": "봉담읍"}},
    )
    address = await api._get_address(lat, lon)
    assert address == "경기도 화성시 봉담읍"


@pytest.mark.asyncio
async def test_address_fallback_on_api_failure(hass, aioclient_mock):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    session = async_get_clientsession(hass)
    api = KMAWeatherAPI(session, "mock_key")

    lat, lon = 37.56, 126.98
    aioclient_mock.get(
        "https://nominatim.openstreetmap.org/reverse",
        status=500,
    )
    address = await api._get_address(lat, lon)
    assert address == "37.5600, 126.9800"
