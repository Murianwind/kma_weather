import pytest
from unittest.mock import MagicMock
from custom_components.kma_weather.sensor import KMACustomSensor

@pytest.mark.asyncio
async def test_summary_and_min_temp_maintenance():
    # 1. Mock 데이터 설정
    coordinator = MagicMock()
    coordinator._daily_min_temp = 12.7  # 최저기온 설정
    # 10일치(forecast_daily)와 2시간간격(forecast_twice_daily) 데이터가 있다고 가정
    coordinator.data = {
        "weather": {
            "current_condition_kor": "맑음",
            "forecast_daily": [{"date": "2024-01-01", "temp": 10}],
            "forecast_twice_daily": [{"datetime": "2024-01-01T12:00:00", "temp": 11}],
            "address": "서울"
        },
        "air": {}
    }
    
    entry = MagicMock()
    entry.entry_id = "test_id"
    entry.data = {"prefix": "test"}
    entry.options = {}

    # 2. 오늘최저온도 센서 검증
    min_temp_sensor = KMACustomSensor(coordinator, "TMN_today", "test", entry)
    assert min_temp_sensor.native_value == 13 # 12.7 반올림 정수
    
    # 3. 날씨 요약 센서 속성 유지 검증
    summary_sensor = KMACustomSensor(coordinator, "weather_summary", "test", entry)
    attrs = summary_sensor.extra_state_attributes
    assert len(attrs["forecast_daily"]) == 1
    assert len(attrs["forecast_twice_daily"]) == 1
    assert attrs["today_min"] == 12.7
    
    print("✅ 문제 1 해결: 최저온도 정수 출력 및 예보 리스트 유지 확인 완료")
