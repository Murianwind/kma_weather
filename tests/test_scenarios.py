"""
통합 시나리오 테스트.
센서 출력값은 API 원본 자릿수 그대로 검증한다.
  - 기상청 정수 원본 → int 출력
  - 기상청 소수점 원본 → float 출력 (변환 없음)
"""
import pytest
from unittest.mock import patch, AsyncMock
from custom_components.kma_weather.const import DOMAIN

try:
    from tests.conftest import MOCK_SCENARIOS
except ImportError:
    from conftest import MOCK_SCENARIOS


def calculate_pm10_grade(value) -> str:
    try:
        val = int(float(value))
        if val <= 50: return "좋음"
        elif val <= 100: return "보통"
        elif val <= 150: return "나쁨"
        return "매우나쁨"
    except (ValueError, TypeError):
        return "정보없음"


def calculate_pm25_grade(value) -> str:
    try:
        val = int(float(value))
        if val <= 15: return "좋음"
        elif val <= 35: return "보통"
        elif val <= 75: return "나쁨"
        return "매우나쁨"
    except (ValueError, TypeError):
        return "정보없음"


@pytest.mark.asyncio
async def test_kma_full_scenarios(hass, mock_config_entry, kma_api_mock_factory, freezer):
    """
    통합 시나리오 테스트.
    센서값은 API 원본 자릿수 그대로 출력된다.
    """
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98

    kma_api_mock_factory("full_test")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    p = "test"
    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]

    # ── 시나리오 1: 개별 센서 값 검증 (API 원본 그대로) ──────────────────
    # TMP=22 (정수) → "22"
    state = hass.states.get(f"sensor.{p}_temperature")
    assert state is not None, "온도 센서가 없음"
    assert state.state == "22", f"온도 기대='22', 실제='{state.state}'"
    assert state.attributes.get("unit_of_measurement") == "°C"
    assert state.attributes.get("device_class") == "temperature"

    # REH=45 (정수) → "45"
    state_reh = hass.states.get(f"sensor.{p}_humidity")
    assert state_reh is not None, "습도 센서가 없음"
    assert state_reh.state == "45", f"습도 기대='45', 실제='{state_reh.state}'"

    # WSD=2.1 (소수점 1자리) → "2.1"
    state_wsd = hass.states.get(f"sensor.{p}_wind_speed")
    assert state_wsd is not None, "풍속 센서가 없음"
    assert state_wsd.state == "2.1", f"풍속 기대='2.1', 실제='{state_wsd.state}'"

    # POP=10 (정수) → "10"
    assert hass.states.get(f"sensor.{p}_precipitation_prob").state == "10"

    # apparent_temp=23.4 (소수점 1자리) → "23.4"
    state_apparent = hass.states.get(f"sensor.{p}_apparent_temperature")
    assert state_apparent is not None, "체감온도 센서가 없음"
    assert state_apparent.state == "23.4", f"체감온도 기대='23.4', 실제='{state_apparent.state}'"

    # ── 시나리오 2: 예보 10일치 ─────────────────────────────────────────
    response = await hass.services.async_call(
        "weather", "get_forecasts",
        {"type": "twice_daily"},
        target={"entity_id": f"weather.{p}_weather"},
        blocking=True, return_response=True,
    )
    forecast = response[f"weather.{p}_weather"]["forecast"]
    assert len(forecast) >= 10

    f0 = forecast[0]
    assert "temperature" in f0
    assert f0["temperature"] is not None
    if "templow" in f0:
        assert f0["templow"] is not None
    assert f0["condition"] is not None

    # ── 시나리오 3: 미세먼지 센서 ─────────────────────────────────────────
    # pm10Value=35 (정수) → "35"
    pm10 = hass.states.get(f"sensor.{p}_pm10")
    assert pm10 is not None, "PM10 센서가 없음"
    assert pm10.state == "35", f"PM10 기대='35', 실제='{pm10.state}'"
    assert pm10.attributes.get("unit_of_measurement") == "µg/m³"
    assert pm10.attributes.get("icon") == "mdi:blur"

    # pm25Value=15 (정수) → "15"
    pm25 = hass.states.get(f"sensor.{p}_pm25")
    assert pm25 is not None, "PM25 센서가 없음"
    assert pm25.state == "15", f"PM25 기대='15', 실제='{pm25.state}'"

    # 등급 검증
    expected_pm10_grade = calculate_pm10_grade(pm10.state)
    actual_pm10_grade = hass.states.get(f"sensor.{p}_pm10_grade").state
    assert actual_pm10_grade == expected_pm10_grade

    expected_pm25_grade = calculate_pm25_grade(pm25.state)
    actual_pm25_grade = hass.states.get(f"sensor.{p}_pm25_grade").state
    assert actual_pm25_grade == expected_pm25_grade

    # ── 시나리오 4: 위치 진단 센서 속성 ──────────────────────────────────
    loc_state = hass.states.get(f"sensor.{p}_location")
    assert loc_state is not None
    expected_station = MOCK_SCENARIOS["full_test"].get("air", {}).get(
        "stationName", loc_state.attributes.get("air_korea_station")
    )
    assert loc_state.attributes.get("air_korea_station") == expected_station

    # ── 시나리오 5 & 6: 현재 위치 출력 및 이동 시 갱신 ──────────────────
    assert hass.states.get(f"sensor.{p}_location").state == "경기도 화성시"

    with patch(
        "custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data",
        new_callable=AsyncMock,
    ) as mock_fetch:
        busan_data = MOCK_SCENARIOS["full_test"].copy()
        busan_data["weather"] = busan_data["weather"].copy()
        busan_data["weather"]["address"] = "부산광역시"
        busan_data["weather"]["현재 위치"] = "부산광역시"
        mock_fetch.return_value = busan_data

        hass.states.async_set(
            "device_tracker.my_phone", "home",
            {"latitude": 35.1, "longitude": 129.0},
        )
        await hass.async_block_till_done()
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        assert hass.states.get(f"sensor.{p}_location").state == "부산광역시"

    # ── 시나리오 7 & 8: 데이터 누락 및 복원 ─────────────────────────────
    kma_api_mock_factory("jeju_missing")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(f"sensor.{p}_temperature").state == "unknown"

    kma_api_mock_factory("full_test")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(f"sensor.{p}_temperature").state == "22"

    # ── 시나리오 10: 모든 센서 안정성 전수 검사 — "-" 값 주입 ───────────
    from custom_components.kma_weather.sensor import SENSOR_TYPES

    polluted_data = {
        "weather": {key: "-" for key in SENSOR_TYPES},
        "air": {key: "-" for key in SENSOR_TYPES},
    }

    with patch(
        "custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data",
        new_callable=AsyncMock,
    ) as mock_polluted:
        mock_polluted.return_value = polluted_data
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        for sensor_type, details in SENSOR_TYPES.items():
            if sensor_type in ["last_updated", "api_expire"]:
                continue
            entity_id = f"sensor.{p}_{details[4]}"
            state = hass.states.get(entity_id)
            if state:
                assert state.state != "unavailable", f"센서 {entity_id}가 unavailable 상태"
                assert state.state == "unknown", f"센서 {entity_id}가 {state.state} (unknown 기대)"

    # ── 시나리오 11: 가비지 데이터 주입 시 강건성 검증 ──────────────────
    garbage_data = {
        "weather": {key: "BAD_DATA" for key in SENSOR_TYPES},
        "air": {key: "BAD_DATA" for key in SENSOR_TYPES},
    }

    with patch(
        "custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data",
        new_callable=AsyncMock,
    ) as mock_garbage:
        mock_garbage.return_value = garbage_data
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        for sensor_type, details in SENSOR_TYPES.items():
            if sensor_type in ["last_updated", "api_expire"]:
                continue
            entity_id = f"sensor.{p}_{details[4]}"
            state = hass.states.get(entity_id)
            if state:
                if details[1] is not None:
                    assert state.state == "unknown"
                assert state.state != "unavailable"

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
