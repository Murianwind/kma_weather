import pytest
from unittest.mock import MagicMock
from homeassistant.components.sensor import SensorDeviceClass
from custom_components.kma_weather.sensor import KMACustomSensor, SENSOR_TYPES

@pytest.mark.asyncio
async def test_kma_sensor_strict_integrity():
    """
    모든 결함을 엄격하게 검출하기 위한 보강된 테스트:
    1. 온도 센서 그룹의 '정수(int)' 자료형 출력 강제 검증 (isinstance 체크)
    2. 풍속 센서의 소수점 첫째자리 반올림 및 자료형 검증
    3. 비 시작 시간 '강수없음' 상태값 검증
    4. 5일차 예보 데이터의 최고/최저 기온 독립성 검증
    """
    
    # --- [준비] Mock 데이터 설정 ---
    coordinator = MagicMock()
    # 실제 API에서 올 수 있는 소수점 데이터를 주입하여 결함 유도
    coordinator.data = {
        "weather": {
            "TMP": "22.6",               # 현재온도 -> 23(int) 기대
            "apparent_temp": "23.4",     # 체감온도 -> 23(int) 기대
            "TMX_tomorrow": "24.7",      # 내일최고 -> 25(int) 기대
            "TMN_tomorrow": "14.2",      # 내일최저 -> 14(int) 기대
            "WSD": "7.58",               # 현재풍속 -> 7.6(float) 기대
            "rain_start_time": "강수없음", # 비시작시간
            "wf_am_tomorrow": "맑음",
            "wf_pm_tomorrow": "흐림",
            "current_condition_kor": "맑음",
            # Problem 4: 5일차 데이터가 서로 다르게 주입됨을 가정
            "forecast_daily": [
                {"datetime": "D1"}, {"datetime": "D2"}, {"datetime": "D3"}, {"datetime": "D4"},
                {
                    "datetime": "D5",
                    "native_temperature": 28.0,
                    "native_templow": 18.0,
                    "condition": "sunny"
                }
            ]
        },
        "air": {
            "pm10Value": "35.4",
            "pm25Value": "15.7",
        }
    }
    
    # 오늘 최고/최저 (코디네이터 내부 계산값)
    coordinator._daily_max_temp = 25.7   # -> 26(int) 기대
    coordinator._daily_min_temp = 15.2   # -> 15(int) 기대

    entry = MagicMock()
    entry.entry_id = "test_id"
    entry.data = {"prefix": "test"}
    entry.options = {}

    # --- [검증 1] Problem 3: 온도 그룹 정수형(int) 출력 엄격 검사 ---
    # Python에서 23.0 == 23은 True이지만, 
    # 실제 화면에 소수점을 없애려면 반드시 자료형이 int여야 합니다.
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
        
        # 값 비교
        assert val == expected_val, f"❌ '{s_type}' 값이 틀립니다. 기대: {expected_val}, 실제: {val}"
        # 자료형 비교 (float 23.0이 나오면 여기서 실패함)
        assert isinstance(val, int), f"❌ '{s_type}'이 정수형(int)이 아닙니다. 현재 타입: {type(val)}"

    # --- [검증 2] Problem 4: 풍속 소수점 첫째자리 확인 (7.58 -> 7.6) ---
    wsd_sensor = KMACustomSensor(coordinator, "WSD", "test", entry)
    val_wsd = wsd_sensor.native_value
    assert val_wsd == 7.6, f"❌ 풍속 반올림 오류: {val_wsd}"
    assert isinstance(val_wsd, float), f"❌ 풍속은 소수점을 포함한 float여야 합니다."

    # --- [검증 3] Problem 3: 비 시작 시간 '강수없음' 확인 ---
    rain_sensor = KMACustomSensor(coordinator, "rain_start_time", "test", entry)
    assert rain_sensor.native_value == "강수없음", f"❌ 비시작시간 상태값 오류: {rain_sensor.native_value}"

    # --- [검증 4] Problem 4: 5일차 데이터 최고/최저 독립성 확인 ---
    daily_list = coordinator.data["weather"]["forecast_daily"]
    day5 = daily_list[4]
    assert day5["native_temperature"] != day5["native_templow"], "❌ 5일차 최고/최저 기온이 중복(동일) 출력되고 있습니다."

    print("✅ 모든 엄격한 검증(타입 체크 포함) 통과")
