import pytest
from custom_components.kma_weather.api_kma import KMAWeatherAPI

@pytest.mark.asyncio
async def test_address_conversion_logic(hass, aioclient_mock):
    """OSM Nominatim API 응답을 모방하여 주소 변환 로직 검증"""
    
    # 1. API 인스턴스 준비
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    session = async_get_clientsession(hass)
    api = KMAWeatherAPI(session, "mock_key", "reg_temp", "reg_land")

    # 2. Nominatim URL 패턴에 대한 가짜 응답 설정
    aioclient_mock.get(
        "https://nominatim.openstreetmap.org/reverse*",
        json={
            "address": {
                "city": "경기도",
                "borough": "화성시",
                "suburb": "봉담읍"
            }
        }
    )

    # 3. 함수 실행 (서울 좌표를 넣어도 Mock 데이터 때문에 화성시가 나와야 함)
    address = await api._get_address(37.5600, 126.9800)

    # 4. 검증: 코드가 parts를 " ".join() 하므로 아래와 같이 나와야 함
    assert address == "경기도 화성시 봉담읍"
