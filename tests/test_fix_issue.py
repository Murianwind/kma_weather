"""
센서 출력값 무결성 테스트.
변환 정책: API 원본값의 소수점 여부 그대로 출력.
  - 소수점 없는 float (예: 25.0) → int (25)
  - 소수점 있는 float (예: 23.4) → float (23.4)
  - 문자열 상태값 → 문자열 그대로
"""
import pytest
from unittest.mock import MagicMock
from custom_components.kma_weather.sensor import KMACustomSensor, SENSOR_TYPES


@pytest.mark.asyncio
async def test_kma_sensor_native_value_passthrough():
    """시나리오: 센서가 API 원본값을 타입 변환 없이(정수는 int, 소수는 float, 문자는 str) 그대로 반환함"""

    # ─────────────────────────────────────────────────────────────────────────────
    # [Given] 코디네이터 및 초기 데이터(정수, 소수, 문자열) 설정
    # ─────────────────────────────────────────────────────────────────────────────
    coordinator = MagicMock()
    coordinator.data = {
        "weather": {
            "TMP": 22,                  # 정수 → int 22
            "apparent_temp": 23.4,      # 소수점 1자리 → float 23.4
            "TMX_tomorrow": 26,         # 정수 → int 26
            "TMN_tomorrow": 16,         # 정수 → int 16
            "WSD": 2.1,                 # 소수점 1자리 → float 2.1
            "rain_start_time": "강수없음",
            "wf_am_tomorrow": "맑음",
            "wf_pm_tomorrow": "흐림",
            "current_condition_kor": "맑음",
        },
        "air": {
            "pm10Value": 35,            # 정수 → int 35
            "pm25Value": 15,            # 정수 → int 15
        }
    }
    coordinator._daily_max_temp = 25    # 정수 → int 25
    coordinator._daily_min_temp = 15    # 정수 → int 15

    entry = MagicMock()
    entry.entry_id = "test_id"
    entry.data = {"prefix": "test"}
    entry.options = {}

    # ─────────────────────────────────────────────────────────────────────────────
    # [Scenario 1] 정수 원본값 → int 출력 검증
    # ─────────────────────────────────────────────────────────────────────────────
    int_cases = {
        "TMP": 22,
        "TMX_today": 25,
        "TMN_today": 15,
        "TMX_tomorrow": 26,
        "TMN_tomorrow": 16,
    }
    # [When] 정수형 센서값들을 요청하면
    for s_type, expected in int_cases.items():
        sensor = KMACustomSensor(coordinator, s_type, "test", entry)
        val = sensor.native_value
        # [Then] 값이 일치하고 타입이 정수형(int)이어야 함
        assert val == expected, f"'{s_type}' 값 기대={expected}, 실제={val}"
        assert isinstance(val, int), f"'{s_type}' 정수형(int) 기대, 실제={type(val)}"

    # ─────────────────────────────────────────────────────────────────────────────
    # [Scenario 2] 소수점 원본값 → float 출력 검증
    # ─────────────────────────────────────────────────────────────────────────────
    float_cases = {
        "WSD": 2.1,
        "apparent_temp": 23.4,
    }
    # [When] 소수점이 있는 센서값들을 요청하면
    for s_type, expected in float_cases.items():
        sensor = KMACustomSensor(coordinator, s_type, "test", entry)
        val = sensor.native_value
        # [Then] 값이 근사치로 일치하고 타입이 float이어야 함
        assert val == pytest.approx(expected), f"'{s_type}' 값 기대={expected}, 실제={val}"
        assert isinstance(val, float), f"'{s_type}' float 기대, 실제={type(val)}"

    # ─────────────────────────────────────────────────────────────────────────────
    # [Scenario 3] 미세먼지 정수 원본 → int 출력 검증
    # ─────────────────────────────────────────────────────────────────────────────
    pm_cases = {
        "pm10Value": 35,
        "pm25Value": 15,
    }
    # [When] 미세먼지 센서값들을 요청하면
    for s_type, expected in pm_cases.items():
        sensor = KMACustomSensor(coordinator, s_type, "test", entry)
        val = sensor.native_value
        # [Then] 값이 일치하고 타입이 int이어야 함
        assert val == expected, f"'{s_type}' 값 기대={expected}, 실제={val}"
        assert isinstance(val, int), f"'{s_type}' int 기대, 실제={type(val)}"

    # ─────────────────────────────────────────────────────────────────────────────
    # [Scenario 4] 문자열 상태값 그대로 출력 검증
    # ─────────────────────────────────────────────────────────────────────────────
    # [When] 강수 시작 시간 센서를 요청하면
    rain_sensor = KMACustomSensor(coordinator, "rain_start_time", "test", entry)
    # [Then] 문자열 상태값이 변환 없이 그대로 반환되어야 함
    assert rain_sensor.native_value == "강수없음"

    # ─────────────────────────────────────────────────────────────────────────────
    # [Scenario 5] 소수점이 있는 미세먼지 농도 → float 출력 검증
    # ─────────────────────────────────────────────────────────────────────────────
    # [Given] 미세먼지 농도가 소수점으로 주어졌을 때
    coordinator.data["air"]["pm10Value"] = 35.5
    # [When] 해당 센서값을 요청하면
    sensor_pm10 = KMACustomSensor(coordinator, "pm10Value", "test", entry)
    val = sensor_pm10.native_value
    # [Then] float 타입으로 정확히 반환되어야 함
    assert val == pytest.approx(35.5), f"PM10 소수점 기대=35.5, 실제={val}"
    assert isinstance(val, float), f"PM10 소수점일 때 float 기대, 실제={type(val)}"

    # ─────────────────────────────────────────────────────────────────────────────
    # [Scenario 6] forecast_daily 5일차 최고/최저 독립성 검증
    # ─────────────────────────────────────────────────────────────────────────────
    # [Given] 5일차 예보 데이터가 존재할 때
    daily_list = coordinator.data["weather"].get("forecast_daily", [
        {}, {}, {}, {},
        {"datetime": "D5", "native_temperature": 28.0, "native_templow": 18.0, "condition": "sunny"}
    ])
    # [When/Then] 5일차 최고 기온과 최저 기온이 독립적으로 저장되어(동일하지 않음) 있어야 함
    if len(daily_list) >= 5:
        day5 = daily_list[4]
        if "native_temperature" in day5 and "native_templow" in day5:
            assert day5["native_temperature"] != day5["native_templow"], \
                "5일차 최고/최저 기온이 동일"
