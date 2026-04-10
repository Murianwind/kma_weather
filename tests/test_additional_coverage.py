import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from homeassistant import config_entries
from homeassistant.const import STATE_UNKNOWN, STATE_UNAVAILABLE

from custom_components.kma_weather.const import DOMAIN

try:
    from tests.conftest import MOCK_SCENARIOS
except ImportError:
    from conftest import MOCK_SCENARIOS


@pytest.mark.asyncio
async def test_config_flow(hass):
    """Config Flow가 정상적으로 시작되는지 테스트."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "user"


@pytest.mark.asyncio
async def test_config_entry_setup_and_unload(
    hass, mock_config_entry, kma_api_mock_factory
):
    """Config Entry 설정 및 언로드 테스트."""
    kma_api_mock_factory("full_test")

    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(
        mock_config_entry.entry_id
    )
    await hass.async_block_till_done()

    assert DOMAIN in hass.data
    assert mock_config_entry.entry_id in hass.data[DOMAIN]

    assert await hass.config_entries.async_unload(
        mock_config_entry.entry_id
    )
    await hass.async_block_till_done()

    assert mock_config_entry.entry_id not in hass.data.get(
        DOMAIN, {}
    )


@pytest.mark.asyncio
async def test_weather_entity_and_forecast(
    hass, mock_config_entry, kma_api_mock_factory
):
    """Weather 엔티티 및 예보 서비스 테스트."""
    kma_api_mock_factory("full_test")

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(
        mock_config_entry.entry_id
    )
    await hass.async_block_till_done()

    entity_id = "weather.test_weather"
    state = hass.states.get(entity_id)

    assert state is not None

    attrs = state.attributes
    assert "supported_features" in attrs
    assert "temperature_unit" in attrs

    # 예보 서비스 호출
    response = await hass.services.async_call(
        "weather",
        "get_forecasts",
        {"type": "twice_daily"},
        target={"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )

    assert entity_id in response
    forecast = response[entity_id].get("forecast", [])
    assert isinstance(forecast, list)

    if forecast:
        first = forecast[0]
        assert "condition" in first
        assert "temperature" in first


@pytest.mark.asyncio
async def test_coordinator_refresh(
    hass, mock_config_entry, kma_api_mock_factory
):
    """DataUpdateCoordinator 갱신 테스트."""
    kma_api_mock_factory("full_test")

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(
        mock_config_entry.entry_id
    )
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][
        mock_config_entry.entry_id
    ]

    assert coordinator.data is not None

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success


@pytest.mark.asyncio
async def test_api_failure_handling(
    hass, mock_config_entry, kma_api_mock_factory
):
    """API 실패 시 센서 상태 검증."""
    api_mock = kma_api_mock_factory("full_test")

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(
        mock_config_entry.entry_id
    )
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][
        mock_config_entry.entry_id
    ]

    api_mock.side_effect = Exception("API Error")
    
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get("sensor.test_temperature")
    assert state is not None
    assert state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE)


@pytest.mark.asyncio
async def test_sensor_recovery_after_api_restore(
    hass, mock_config_entry, kma_api_mock_factory
):
    """API 장애 후 복구 테스트."""
    # 1. 초기 정상 로드 및 설정
    api_mock = kma_api_mock_factory("full_test")

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]

    # [핵심 수정] 센서 상태가 업데이트될 때까지 강제 갱신 유도
    # 코디네이터의 리스너를 호출하여 센서 엔티티가 상태를 다시 쓰도록 만듭니다.
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get("sensor.test_temperature")
    
    # 만약 여전히 unknown이라면, 아주 잠시 기다린 후 다시 확인합니다.
    if state.state == STATE_UNKNOWN:
        for _ in range(5):
            await asyncio.sleep(0.1)
            state = hass.states.get("sensor.test_temperature")
            if state.state != STATE_UNKNOWN:
                break

    assert state.state == "22"

    # 2. API 실패 시뮬레이션
    api_mock.side_effect = Exception("Temporary Error")
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get("sensor.test_temperature")
    assert state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE)

    # 3. API 복구 시뮬레이션
    api_mock.side_effect = None
    api_mock.return_value = MOCK_SCENARIOS.get("full_test")

    # 복구 데이터 반영
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # 복구된 상태 확인 (마찬가지로 강제 리스너 업데이트 및 대기 적용)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    
    recovered_state = hass.states.get("sensor.test_temperature")
    
    if recovered_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        for _ in range(5):
            await asyncio.sleep(0.1)
            recovered_state = hass.states.get("sensor.test_temperature")
            if recovered_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                break

    assert recovered_state.state == "22"
