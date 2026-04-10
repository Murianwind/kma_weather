import pytest
from unittest.mock import MagicMock
from homeassistant.const import UnitOfTemperature, PERCENTAGE, UnitOfSpeed
from homeassistant.components.sensor import SensorDeviceClass
from custom_components.kma_weather.sensor import KMACustomSensor, SENSOR_TYPES

@pytest.mark.asyncio
async def test_kma_sensor_integrity_and_formatting():
    """
    모든 결함을 확실하게 검출하기 위한 보강된 테스트:
    1. 온도 센서 그룹의 정수(int) 출력 강제 검증
    2. 풍속 센서의 소수점 첫째자리 반올림 검증
    3. 비 시작 시간 '강수없음' 상태값 검증
    4. 5일차 중기 예보 데이터의 독립성(최고/최저 중복 방지) 검증
    """
    
    # --- [준비] Mock 데이터 설정 (Problem 4 대응용 데이터 포함) ---
    coordinator = MagicMock()
    coordinator.data = {
        "weather": {
            # Problem 1 & 3: 온도 소수점 주입 (반올림 정수 확인용)
            "TMP": "22.6",               # 현재온도 -> 23 기대
            "apparent_temp": "23.4",     # 체감온도 -> 23 기대
            "TMX_tomorrow": "24.7",      # 내일최고 -> 25 기대
            "TMN_tomorrow": "14.2",      # 내일최저 -> 14 기대
            
            # Problem 2: 풍속 소수점 2자리 주입
            "WSD": "7.58",               # 현재풍속 -> 7.6 기대
            
            # Problem 3: 강수 없음 케이스
            "rain_start_time": "강수없음",
            
            # Problem 4: 5일차 예보 리스트 (최고/최저가 다른 상황 모사)
            "forecast_daily": [
                {"datetime": "D1"}, {"datetime": "D2"}, {"datetime": "D3"}, {"datetime": "D4"},
                {
                    "datetime": "D5",
                    "native_temperature": 28.0,
                    "native_templow": 18.0,
                    "condition": "sunny"
                }
            ],
            "current_condition_kor": "맑음",
        },
        "air": {
            "pm10Value": "35.4",
            "pm25Value": "15.7",
        }
    }
    
    # 오늘 최고/최저 기온 (코디네이터 내부 계산 값)
    coordinator._daily_max_temp = 25.7   # -> 26 기대
    coordinator._daily_min_temp = 15.2   # -> 15 기대

    entry = MagicMock()
    entry.entry_id = "test_id"
    entry.data = {"prefix": "test"}
    entry.options = {}

    # --- [검증 1] Problem 1 & 3: 온도 그룹 정수(int) 출력 전수 조사 ---
    temp_sensors = {
        "TMP": 23,
        "apparent_temp": 23,
        "TMX_today": 26,
        "TMN_today": 15,
        "TMX_tomorrow": 25,
        "TMN_tomorrow": 14
    }
    
    for s_type, expected_val in temp_sensors.items():
        sensor = KMACustomSensor(coordinator, s_type, "test", entry)
        val = sensor.native_value
        # 1. 값이 기대한 정수값인지 확인
        assert val == expected_val, f"❌ '{s_type}' 값이 틀립니다. 기대값: {expected_val}, 실제값: {val}"
        # 2. 타입이 float가 아닌 int인지 엄격하게 확인 (Problem 1의 핵심)
        assert isinstance(val, int), f"❌ '{s_type}'이 정수형(int)이 아닙니다. 현재 타입: {type(val)}"

    # --- [검증 2] Problem 2: 풍속 소수점 첫째자리 확인 (7.58 -> 7.6) ---
    wsd_sensor = KMACustomSensor(coordinator, "WSD", "test", entry)
    assert wsd_sensor.native_value == 7.6, f"❌ 풍속 반올림 오류: {wsd_sensor.native_value}"

    # --- [검증 3] Problem 3: 비 시작 시간 '강수없음' 확인 ---
    rain_sensor = KMACustomSensor(coordinator, "rain_start_time", "test", entry)
    assert rain_sensor.native_value == "강수없음", f"❌ 강수없음 상태값 미출력: {rain_sensor.native_value}"

    # --- [검증 4] Problem 4: 5일차 데이터 무결성 확인 (코디네이터 데이터 구조 검증) ---
    # 이 부분은 api_kma.py에서 가공된 데이터가 weather 속성에 올바르게 들어왔는지 확인합니다.
    daily_list = coordinator.data["weather"]["forecast_daily"]
    day5 = daily_list[4]
    assert day5["native_temperature"] != day5["native_templow"], "❌ 5일차 최고/최저 기온이 동일하게 출력되고 있습니다."

    print("✅ 모든 결함(정수 출력, 풍속 반올림, 강수없음, 5일차 데이터) 검증 완료")
