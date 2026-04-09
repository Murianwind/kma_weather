import pytest
from unittest.mock import patch, AsyncMock
from custom_components.kma_weather.api_kma import KMAWeatherAPI

@pytest.mark.asyncio
async def test_address_conversion_logic(aioclient_mock):
    """좌표를 넣었을 때 API 응답을 파싱하여 주소를 잘 생성하는지 테스트"""
    
    # 테스트를 위한 API 인스턴스 생성 (최소한의 인자)
    api = KMAWeatherAPI(None, "mock_key", "reg_id", "reg_land")
    
    # 1. 기상청/카카오 등 주소 변환 API의 가상 응답 설정 (JSON 구조는 실제 사용 API에 맞춤)
    # 예: 주소 API가 호출될 때 "경기도 화성시"를 반환하도록 Mocking
    aioclient_mock.get(
        "https://apis.data.go.kr/B552584/MsrstnInfoInqireSvc/getNearbyMsrstnList*", # 예시 URL
        json={
            "response": {
                "body": {
                    "items": [{"stationName": "화성", "addr": "경기도 화성시 ..."}]
                }
            }
        }
    )

    # 2. 함수 호출 (서울 좌표)
    # 실제 api_kma.py에 정의된 주소 변환 메서드 이름이 _get_address 라면:
    address = await api._get_address(37.56, 126.98)

    # 3. 검증
    assert "경기도" in address or "서울" in address # 로직에 따라 기대값 설정
    assert address != "Unknown"
