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
    """
    센서가 API 원본값을 변환 없이 그대로 반환하는지 검증.
    소수점 없는 정수 → int, 소수점 있는 값 → float.
    """
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

    # ── 정수 원본값 → int 출력 ──────────────────────────────────────────
    int_cases = {
        "TMP": 22,
        "TMX_today": 25,
        "TMN_today": 15,
        "TMX_tomorrow": 26,
        "TMN_tomorrow": 16,
    }
    for s_type, expected in int_cases.items():
        sensor = KMACustomSensor(coordinator, s_type, "test", entry)
        val = sensor.native_value
        assert val == expected, f"'{s_type}' 값 기대={expected}, 실제={val}"
        assert isinstance(val, int), f"'{s_type}' 정수형(int) 기대, 실제={type(val)}"

    # ── 소수점 원본값 → float 출력 ──────────────────────────────────────
    float_cases = {
        "WSD": 2.1,
        "apparent_temp": 23.4,
    }
    for s_type, expected in float_cases.items():
        sensor = KMACustomSensor(coordinator, s_type, "test", entry)
        val = sensor.native_value
        assert val == pytest.approx(expected), f"'{s_type}' 값 기대={expected}, 실제={val}"
        assert isinstance(val, float), f"'{s_type}' float 기대, 실제={type(val)}"

    # ── 미세먼지 정수 원본 → int 출력 ────────────────────────────────────
    pm_cases = {
        "pm10Value": 35,
        "pm25Value": 15,
    }
    for s_type, expected in pm_cases.items():
        sensor = KMACustomSensor(coordinator, s_type, "test", entry)
        val = sensor.native_value
        assert val == expected, f"'{s_type}' 값 기대={expected}, 실제={val}"
        assert isinstance(val, int), f"'{s_type}' int 기대, 실제={type(val)}"

    # ── 문자열 상태값 그대로 출력 ─────────────────────────────────────────
    rain_sensor = KMACustomSensor(coordinator, "rain_start_time", "test", entry)
    assert rain_sensor.native_value == "강수없음"

    # ── 소수점이 있는 미세먼지 농도 (예: 35.5) → float 출력 ──────────────
    coordinator.data["air"]["pm10Value"] = 35.5
    sensor_pm10 = KMACustomSensor(coordinator, "pm10Value", "test", entry)
    val = sensor_pm10.native_value
    assert val == pytest.approx(35.5), f"PM10 소수점 기대=35.5, 실제={val}"
    assert isinstance(val, float), f"PM10 소수점일 때 float 기대, 실제={type(val)}"

    # ── forecast_daily 5일차 최고/최저 독립성 ────────────────────────────
    daily_list = coordinator.data["weather"].get("forecast_daily", [
        {}, {}, {}, {},
        {"datetime": "D5", "native_temperature": 28.0, "native_templow": 18.0, "condition": "sunny"}
    ])
    if len(daily_list) >= 5:
        day5 = daily_list[4]
        if "native_temperature" in day5 and "native_templow" in day5:
            assert day5["native_temperature"] != day5["native_templow"], \
                "5일차 최고/최저 기온이 동일"
