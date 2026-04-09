import pytest
from unittest.mock import patch, MagicMock
from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

# 테스트 데이터: 주요 지점별 기대 측정소 (예시)
AIR_STATION_TEST_CASES = [
    {"name": "서울 시청", "lat": 37.5665, "lon": 126.9780, "expected_station": "중구"},
    {"name": "대전 시청", "lat": 36.3504, "lon": 127.3845, "expected_station": "둔산동"},
    {"name": "부산 시청", "lat": 35.1795, "lon": 129.0756, "expected_station": "연산동"},
]

@pytest.mark.async_with_loop
async def test_air_korea_station_resolution(hass, mock_entry):
    """GPS 좌표에 따른 에어코리아 측정소 결정 로직 검증"""
    coordinator = KMAWeatherUpdateCoordinator(hass, mock_entry)
    
    for case in AIR_STATION_TEST_CASES:
        with patch("custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data") as mock_fetch:
            # API 응답 시뮬레이션: 해당 지역 측정소 이름 포함
            mock_fetch.return_value = {
                "weather": {"TMP": 20},
                "air": {"station": case["expected_station"], "pm10Value": 30}
            }

            # 1. 특정 위치로 좌표 설정
            with patch.object(coordinator, "_resolve_location", return_value=(case["lat"], case["lon"])):
                await coordinator._async_update_data()
                
                # 2. 코디네이터 데이터에 측정소 이름이 정확히 저장되었는지 확인
                air_data = coordinator.data.get("air", {})
                assert air_data.get("station") == case["expected_station"], \
                    f"❌ {case['name']} 측정소 매칭 오류: 기대값 {case['expected_station']}, 실제값 {air_data.get('station')}"

def test_air_korea_data_key_mapping():
    """에어코리아 데이터가 센서에서 사용하는 키와 일치하는지 전수 검사"""
    from custom_components.kma_weather.sensor import SENSOR_TYPES
    
    # 코디네이터가 생성해야 하는 에어코리아 관련 키 리스트
    required_air_keys = ["pm10Value", "pm10Grade", "pm25Value", "pm25Grade"]
    
    for key in required_air_keys:
        assert key in SENSOR_TYPES, f"❌ 에어코리아 키 미스매치: '{key}'가 SENSOR_TYPES에 정의되어 있지 않습니다."
