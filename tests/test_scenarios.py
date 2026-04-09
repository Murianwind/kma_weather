import pytest
from unittest.mock import patch, AsyncMock
from homeassistant.util import dt as dt_util
from custom_components.kma_weather.const import DOMAIN
try:
    from tests.conftest import MOCK_SCENARIOS
except ImportError:
    from conftest import MOCK_SCENARIOS

@pytest.mark.asyncio
async def test_kma_full_scenarios(hass, mock_config_entry, kma_api_mock_factory, freezer):
    """모든 통합 시나리오 테스트"""
    
    # 기본 좌표 설정
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    
    # 초기 데이터 및 셋업
    kma_api_mock_factory("full_test")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    p = "test"
    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]

    # --- 시나리오 1. 개별 센서 검증 ---
    state = hass.states.get(f"sensor.{p}_temperature")
    assert state.state == "22"
    assert state.attributes.get("unit_of_measurement") == "°C"
    # 수정된 device_class 확인
    assert state.attributes.get("device_class") == "temperature"

    # --- 시나리오 2. 예보 10일치 검증 ---
    response = await hass.services.async_call(
        "weather", "get_forecasts", {"type": "twice_daily"},
        target={"entity_id": f"weather.{p}_weather"},
        blocking=True, return_response=True,
    )
    forecast = response[f"weather.{p}_weather"]["forecast"]
    assert len(forecast) >= 10
    
    # KeyError 방지 및 데이터 확인
    f0 = forecast[0]
    assert "temperature" in f0
    if "templow" in f0:
        assert f0["templow"] is not None

    # --- 시나리오 5 & 6. 위치 출력 및 변경 시 갱신 ---
    assert hass.states.get(f"sensor.{p}_location").state == "경기도 화성시"

    with patch("custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data", new_callable=AsyncMock) as mock_fetch:
        busan_data = MOCK_SCENARIOS["full_test"].copy()
        busan_data["weather"] = busan_data["weather"].copy()
        busan_data["weather"]["address"] = "부산광역시"
        mock_fetch.return_value = busan_data

        hass.states.async_set("device_tracker.my_phone", "home", {"latitude": 35.1, "longitude": 129.0})
        await hass.async_block_till_done()
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        assert hass.states.get(f"sensor.{p}_location").state == "부산광역시"

    # --- 시나리오 7 & 8. 데이터 누락 및 복원 ---
    kma_api_mock_factory("jeju_missing")
    await coordinator.async_refresh()
    assert hass.states.get(f"sensor.{p}_temperature").state == "unknown"

    kma_api_mock_factory("full_test")
    await coordinator.async_refresh()
    assert hass.states.get(f"sensor.{p}_temperature").state == "22"

    # --- 시나리오 10 & 11. 안정성 전수 검사 (unavailable 방지) ---
    from custom_components.kma_weather.sensor import SENSOR_TYPES

    polluted_data = {"weather": {key: "-" for key in SENSOR_TYPES}, "air": {key: "-" for key in SENSOR_TYPES}}
    
    with patch("custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data", new_callable=AsyncMock) as mock_polluted:
        mock_polluted.return_value = polluted_data
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        for sensor_type, details in SENSOR_TYPES.items():
            entity_id = f"sensor.{p}_{details[4]}"
            state = hass.states.get(entity_id)
            if state:
                assert state.state != "unavailable"
                assert state.state == "unknown"

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
