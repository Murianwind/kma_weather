import pytest
from unittest.mock import patch, AsyncMock
from custom_components.kma_weather.api_kma import KMAWeatherAPI

@pytest.mark.asyncio
async def test_address_conversion_logic(hass, aioclient_mock):
    # KMAWeatherAPI 인스턴스 생성
    from custom_components.kma_weather.api_kma import KMAWeatherAPI
    api = KMAWeatherAPI(hass.helpers.aiohttp_client.async_get_clientsession(hass), "mock_key", "reg_id", "reg_land")

    # 카카오 API 또는 사용 중인 주소 API의 응답을 모방
    # 실제 api_kma.py에서 호출하는 URL 주소와 일치해야 합니다.
    aioclient_mock.get(
        "https://dapi.kakao.com/v2/local/geo/coord2address.json*", 
        json={
            "documents": [
                {
                    "address": {"address_name": "경기도 화성시 봉담읍"},
                    "road_address": {"address_name": "경기도 화성시 봉담읍 ..."}
                }
            ]
        }
    )

    address = await api._get_address(37.56, 126.98)
    
    # 이제 '37.56, 126.98'이 아니라 '경기도 화성시 봉담읍'이 반환되어야 함
    assert "경기도" in address
    assert "화성시" in address
