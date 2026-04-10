import pytest
from unittest.mock import MagicMock
from homeassistant.const import UnitOfTemperature, PERCENTAGE, UnitOfSpeed
from homeassistant.components.sensor import SensorDeviceClass
from custom_components.kma_weather.sensor import KMACustomSensor, SENSOR_TYPES

@pytest.mark.asyncio
async def test_kma_sensor_integrity_and_formatting():
    """
    모든 결함을 검출하기 위한 강화된 통합 테스트:
    1. 불필요한 센서 제거 확인
    2. Unknown 센서 데이터 매핑 확인
    3. 온도(정수), 풍속(소수점 1자리) 출력 형식 확인
    4. 미세먼지 단위 및 데이터 무결성 확인
    """
    
    # --- [검증 1] Problem 1: 불필요한 센서(weather_summary) 제거 확인 ---
    assert "weather_summary" not in SENSOR_TYPES, "❌ 'weather_summary' 센서가 제거되지 않았습니다. (sensor.home_summary 생성 원인)"

    # --- [준비] Mock 데이터 설정 ---
    coordinator = MagicMock()
    # 소수점 데이터 주입하여 반올림 결함(Problem 3, 4) 유도
    coordinator.data = {
        "weather": {
            "TMP": "22.6",               # 현재온도
            "apparent_temp": "23.4",     # 체감온도
            "TMX_today": "25.7",         # 오늘최고
            "TMN_today": "15.2",         # 오늘최저
            "TMX_tomorrow": "24.3",      # 내일최고
            "TMN_tomorrow": "14.8",      # 내일최저
            "WSD": "7.58",               # 현재풍속 (소수점 2자리)
            "REH": "45.0",
            "POP": "10",
            "rain_start_time": "14:00",  # 비시작시간
            "wf_am_tomorrow": "맑음",    # 내일오전
            "wf_pm_tomorrow": "흐림",    # 내일오후
            "current_condition_kor": "맑음",
        },
        "air": {
            "pm10Value": "35.4",
            "pm25Value": "15.7",
            "pm10Grade": "보통",
            "pm25Grade": "좋음",
            "station": "테스트측정소"
        }
    }
    # 코디네이터 내부 계산 변수 (오늘 최고/최저)
    coordinator._daily_max_temp = 25.7
    coordinator._daily_min_temp = 15.2

    entry = MagicMock()
    entry.entry_id = "test_id"
    entry.data = {"prefix": "test"}
    entry.options = {}

    # --- [검증 2] Problem 2: Unknown 센서 데이터 매핑 확인 ---
    # 누락되었던 내일 데이터 및 비 시작 시간 검증
    target_sensors = {
        "rain_start_time": "14:00",
        "wf_am_tomorrow": "맑음",
        "wf_pm_tomorrow": "흐림",
        "TMX_tomorrow": 24, # (24.3 반올림 정수)
        "TMN_tomorrow": 15  # (14.8 반올림 정수)
    }
    
    for s_type, expected in target_sensors.items():
        sensor = KMACustomSensor(coordinator, s_type, "test", entry)
        val = sensor.native_value
        assert val is not None, f"❌ 센서 '{s_type}'의 값이 Unknown입니다."
        assert val == expected, f"❌ 센서 '{s_type}'의 값이 기대값({expected})과 다릅니다: {val}"

    # --- [검증 3] Problem 3 & 4: 정수 및 소수점 출력 형식 확인 ---
    # 1. 온도 그룹: 정수 출력 확인 (Problem 3)
    temp_sensors = ["TMP", "apparent_temp", "TMX_today", "TMN_today", "TMX_tomorrow", "TMN_tomorrow"]
    for s_type in temp_sensors:
        sensor = KMACustomSensor(coordinator, s_type, "test", entry)
        val = sensor.native_value
        assert isinstance(val, int), f"❌ 온도 센서 '{s_type}'이 정수가 아닙니다: {val} (type: {type(val)})"

    # 2. 풍속: 소수점 첫째자리 확인 (7.58 -> 7.6) (Problem 4)
    wsd_sensor = KMACustomSensor(coordinator, "WSD", "test", entry)
    assert wsd_sensor.native_value == 7.6, f"❌ 풍속이 소수점 첫째자리로 반올림되지 않았습니다: {wsd_sensor.native_value}"

    # --- [검증 4] Problem 4: 미세먼지 농도 소수점 1자리 및 단위 확인 ---
    pm10_sensor = KMACustomSensor(coordinator, "pm10Value", "test", entry)
    assert pm10_sensor.native_value == 35.4
    assert pm10_sensor.native_unit_of_measurement == "µg/m³", "❌ 미세먼지 단위가 올바르지 않습니다."

    print("✅ 모든 결함 검출 및 보강 테스트 완료")
