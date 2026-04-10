import pytest
from unittest.mock import MagicMock
from custom_components.kma_weather.sensor import KMACustomSensor

@pytest.mark.asyncio
async def test_summary_and_min_temp_maintenance():
    """시나리오 1: 최저온도 반올림 및 요약 센서 속성 유지 검증"""
    # 1. Mock 데이터 설정
    coordinator = MagicMock()
    coordinator._daily_min_temp = 12.7  # 최저기온 설정
    coordinator._daily_max_temp = 22.0
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

    # 2. 오늘최저온도 센서 검증 (12.7 -> 13 정수 반올림 확인)
    min_temp_sensor = KMACustomSensor(coordinator, "TMN_today", "test", entry)
    assert min_temp_sensor.native_value == 13
    
    # 3. 날씨 요약 센서 속성 유지 검증
    summary_sensor = KMACustomSensor(coordinator, "weather_summary", "test", entry)
    attrs = summary_sensor.extra_state_attributes
    assert len(attrs["forecast_daily"]) == 1
    assert len(attrs["forecast_twice_daily"]) == 1
    assert attrs["today_min"] == 12.7
    
    print("✅ 문제 1 해결: 최저온도 정수 출력 및 예보 리스트 유지 확인 완료")


@pytest.mark.asyncio
async def test_sensor_decimal_formats_and_units():
    """시나리오 2: 풍속/미세먼지 소수점 출력 및 단위(µg/m³) 검증"""
    # 1. Mock 데이터 설정
    coordinator = MagicMock()
    coordinator.data = {
        "weather": {
            "WSD": "7.58",      # 풍속 (소수점 둘째자리 입력)
            "TMP": "22.4"       # 온도
        },
        "air": {
            "pm10Value": "35",  # 미세먼지 (정수 입력)
            "pm25Value": "15.23", # 초미세먼지
            "pm10Grade": "2",
            "pm25Grade": "1"
        }
    }
    
    entry = MagicMock()
    entry.entry_id = "test_id"
    entry.data = {"prefix": "test"}
    entry.options = {}

    # 2. 풍속 검증: 소수점 첫째자리까지 반올림 (7.58 -> 7.6)
    wsd_sensor = KMACustomSensor(coordinator, "WSD", "test", entry)
    assert wsd_sensor.native_value == 7.6

    # 3. 온도 검증: 여전히 정수 유지 (22.4 -> 22)
    tmp_sensor = KMACustomSensor(coordinator, "TMP", "test", entry)
    assert tmp_sensor.native_value == 22

    # 4. 미세먼지 농도 검증: 소수점 첫째자리 강제 (35 -> 35.0, 15.23 -> 15.2)
    pm10_sensor = KMACustomSensor(coordinator, "pm10Value", "test", entry)
    assert pm10_sensor.native_value == 35.0
    
    pm25_sensor = KMACustomSensor(coordinator, "pm25Value", "test", entry)
    assert pm25_sensor.native_value == 15.2

    # 5. 단위 검증: µg/m³ (마이크로 기호 표준 준수 확인)
    assert pm10_sensor.native_unit_of_measurement == "µg/m³"
    
    # 6. 등급 검증: '정보없음'이 아닌 정상 등급 출력 확인
    pm25_grade_sensor = KMACustomSensor(coordinator, "pm25Grade", "test", entry)
    assert pm25_grade_sensor.native_value == "좋음"

    print("✅ 문제 2, 3, 4 해결: 풍속/미세먼지 소수점 및 단위/등급 확인 완료")
