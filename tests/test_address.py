import pytest
from custom_components.kma_weather.api_kma import KMAWeatherAPI

@pytest.mark.asyncio
async def test_address_conversion_logic(hass, aioclient_mock):
    """시나리오: OSM Nominatim API의 복잡한 응답을 사람이 읽기 좋은 주소 문자열로 변환함"""
    
    # Given: Nominatim API가 특정 좌표에 대해 '경기도 화성시 봉담읍' 데이터를 반환하도록 설정
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    session = async_get_clientsession(hass)
    api = KMAWeatherAPI(session, "mock_key", "reg_temp", "reg_land")
    
    lat, lon = 37.56, 126.98
    aioclient_mock.get(
        "https://nominatim.openstreetmap.org/reverse",
        params={"format": "json", "lat": str(lat), "lon": str(lon), "zoom": "16"},
        json={
            "address": {
                "city": "경기도",
                "borough": "화성시",
                "suburb": "봉담읍",
            }
        },
    )

    # When: 해당 좌표로 주소를 조회할 때
    address = await api._get_address(lat, lon)
    
    # Then: API 응답의 각 항목이 결합되어 "경기도 화성시 봉담읍"이 되어야 함
    assert address == "경기도 화성시 봉담읍"


@pytest.mark.asyncio
async def test_address_fallback_on_api_failure(hass, aioclient_mock):
    """시나리오: 주소 변환 API 호출이 실패할 경우, 위경도 숫자를 주소 대신 반환함 (Fallback)"""
    
    # Given: Nominatim API가 500 에러(서버 오류)를 반환하는 상황
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    session = async_get_clientsession(hass)
    api = KMAWeatherAPI(session, "mock_key", "reg_temp", "reg_land")

    lat, lon = 37.56, 126.98
    aioclient_mock.get(
        "https://nominatim.openstreetmap.org/reverse",
        status=500,
    )

    # When: 주소를 조회하려고 시도하면
    address = await api._get_address(lat, lon)
    
    # Then: 에러가 나는 대신 소수점 4자리까지 표현된 "위도, 경도" 문자열이 반환되어야 함
    assert address == "37.5600, 126.9800"
