import pytest
from unittest.mock import patch, AsyncMock
from custom_components.kma_weather.const import DOMAIN

try:
    from tests.conftest import MOCK_SCENARIOS
except ImportError:
    # 로컬 실행 환경에 따라 임포트 경로가 달라질 수 있으므로 fallback 추가
    from conftest import MOCK_SCENARIOS


def calculate_pm10_grade(value: int) -> str:
    """PM10 등급 계산 (통합 로직 기준)."""
    if value <= 50:
        return "좋음"
    elif value <= 100:
        return "보통"
    elif value <= 150:
        return "나쁨"
    return "매우나쁨"


def calculate_pm25_grade(value: int) -> str:
    """PM2.5 등급 계산 (통합 로직 기준)."""
    if value <= 15:
        return "좋음"
    elif value <= 35:
        return "보통"
    elif value <= 75:
        return "나쁨"
    return "매우나쁨"


@pytest.mark.asyncio
async def test_kma_full_scenarios(
    hass, mock_config_entry, kma_api_mock_factory, freezer
):
    """
    통합 시나리오 테스트:
    1~8번 기본 기능 검증 및 10~11번 모든 센서 안정성(Fault Tolerance) 전수 검사
    """

    # 0. 기본 설정 및 초기화
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98

    # 초기 데이터(서울) 로드 및 셋업
    kma_api_mock_factory("full_test")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    p = "test"
    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]

    # --- 시나리오 1. 개별 센서 검증 ---
    state = hass.states.get(f"sensor.{p}_temperature")
    assert state is not None
    assert state.state == "22"
    assert state.attributes.get("unit_of_measurement") == "°C"
    assert state.attributes.get("device_class") == "temperature"

    state_reh = hass.states.get(f"sensor.{p}_humidity")
    assert state_reh is not None
    assert state_reh.state == "45"

    assert hass.states.get(f"sensor.{p}_wind_speed").state == "7"
    assert hass.states.get(f"sensor.{p}_precipitation_prob").state == "10"

    # --- 시나리오 2. 예보 10일치 검증 ---
    response = await hass.services.async_call(
        "weather",
        "get_forecasts",
        {"type": "twice_daily"},
        target={"entity_id": f"weather.{p}_weather"},
        blocking=True,
        return_response=True,
    )

    forecast = response[f"weather.{p}_weather"]["forecast"]
    assert len(forecast) >= 10

    f0 = forecast[0]
    assert "temperature" in f0
    assert f0["temperature"] is not None
    if "templow" in f0:
        assert f0["templow"] is not None
    assert f0["condition"] is not None

    # --- 시나리오 3. 미세먼지 센서 및 아이콘 검증 ---
    pm10 = hass.states.get(f"sensor.{p}_pm10")
    assert pm10 is not None
    assert pm10.state == "35"
    assert pm10.attributes.get("icon") == "mdi:blur"

    pm25 = hass.states.get(f"sensor.{p}_pm25")
    assert pm25 is not None
    assert pm25.state == "15"

    # PM10 등급 검증
    expected_pm10_grade = calculate_pm10_grade(int(pm10.state))
    actual_pm10_grade = hass.states.get(f"sensor.{p}_pm10_grade").state
    assert actual_pm10_grade == expected_pm10_grade

    # PM2.5 등급 검증
    expected_pm25_grade = calculate_pm25_grade(int(pm25.state))
    actual_pm25_grade = hass.states.get(f"sensor.{p}_pm25_grade").state
    assert actual_pm25_grade == expected_pm25_grade

    # --- 시나리오 4. 체감온도 및 추가 속성 검증 ---
    assert hass.states.get(
        f"sensor.{p}_apparent_temperature"
    ).state == "23"

    # 위치 진단 센서의 속성 확인
    loc_state = hass.states.get(f"sensor.{p}_location")
    assert loc_state is not None
   
    # Mock 데이터 기반 검증
    expected_station = MOCK_SCENARIOS["full_test"].get("air", {}).get(
        "stationName",
        loc_state.attributes.get("air_korea_station")
    )

    assert loc_state.attributes.get("air_korea_station") == expected_station

    # --- 시나리오 5 & 6. 현재 위치 출력 및 변경 시 갱신 ---
    assert hass.states.get(
        f"sensor.{p}_location"
    ).state == "경기도 화성시"

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
            "device_tracker.my_phone",
            "home",
            {"latitude": 35.1, "longitude": 129.0},
        )
        await hass.async_block_till_done()

        await coordinator.async_refresh()
        await hass.async_block_till_done()

        assert hass.states.get(
            f"sensor.{p}_location"
        ).state == "부산광역시"

    # --- 시나리오 7 & 8. 데이터 누락 및 복원 ---
    kma_api_mock_factory("jeju_missing")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(
        f"sensor.{p}_temperature"
    ).state == "unknown"

    kma_api_mock_factory("full_test")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(
        f"sensor.{p}_temperature"
    ).state == "22"

    # --- 시나리오 10. 모든 센서 안정성(Fault Tolerance) 전수 검사 ---
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
                assert state.state != "unavailable", (
                    f"센서 {entity_id}가 unavailable 상태입니다!"
                )
                assert state.state == "unknown", (
                    f"센서 {entity_id}가 {state.state}입니다. "
                    "(unknown 기대)"
                )

    # --- 시나리오 11. 가비지 데이터(Garbage) 주입 시 강건성 검증 ---
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

@pytest.mark.asyncio
async def test_kma_sensor_formatting(hass, mock_config_entry, kma_api_mock_factory):
    """센서 출력 형식(정수/소수점) 통합 검증"""
    kma_api_mock_factory("full_test")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    p = "test"
    # 1. 온도/습도: 정수 확인
    assert hass.states.get(f"sensor.{p}_temperature").state == "22"
    assert hass.states.get(f"sensor.{p}_humidity").state == "45"

    # 2. 풍속: 소수점 첫째자리 확인 (데이터가 7.6일 때)
    state_wsd = hass.states.get(f"sensor.{p}_wind_speed")
    assert state_wsd.state == "7.6"

    # 3. 미세먼지 농도: 소수점 첫째자리 확인 (데이터가 15일 때 -> 15.0)
    state_pm25 = hass.states.get(f"sensor.{p}_pm25")
    assert state_pm25.state == "15.0"
    assert state_pm25.attributes.get("unit_of_measurement") == "µg/m³"
    
    print("✅ 모든 센서 출력 형식 검증 완료")

    # 정리
    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
