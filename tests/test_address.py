import pytest
from custom_components.kma_weather.api_kma import KMAWeatherAPI


@pytest.mark.asyncio
async def test_address_conversion_logic(hass, aioclient_mock):
    """OSM Nominatim API 응답 파싱 로직 검증"""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    api = KMAWeatherAPI(session, "mock_key", "reg_temp", "reg_land")

    # aioclient_mock 은 URL 과 params 를 분리해서 등록해야 매칭됩니다.
    # (URL 에 쿼리 파라미터를 포함시키면 aiohttp 가 params 를 별도로 붙일 때 매칭 실패)
    aioclient_mock.get(
        "https://nominatim.openstreetmap.org/reverse",
        params={"format": "json", "lat": "37.56", "lon": "126.98", "zoom": "16"},
        json={
            "address": {
                "city": "경기도",
                "borough": "화성시",
                "suburb": "봉담읍",
            }
        },
    )

    address = await api._get_address(37.56, 126.98)
    assert address == "경기도 화성시 봉담읍"


@pytest.mark.asyncio
async def test_address_fallback_on_api_failure(hass, aioclient_mock):
    """Nominatim API 호출 실패 시 위경도 문자열로 fallback 하는지 검증"""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    api = KMAWeatherAPI(session, "mock_key", "reg_temp", "reg_land")

    # 응답 없음 → _fetch 가 None 반환 → fallback 경로
    aioclient_mock.get(
        "https://nominatim.openstreetmap.org/reverse",
        status=500,
    )

    address = await api._get_address(37.56, 126.98)
    assert address == "37.5600, 126.9800"
