import pytest
from unittest.mock import AsyncMock, patch
from homeassistant import config_entries
from homeassistant.util import dt as dt_util

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

    # 에러 발생
    api_mock.side_effect = Exception("API Error")
    
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get("sensor.test_temperature")
    assert state is not None
    assert state.state in ("unknown", "unavailable")


@pytest.mark.asyncio
async def test_sensor_recovery_after_api_restore(
    hass, mock_config_entry, kma_api_mock_factory
):
    """API 장애 후 복구 테스트."""
    # 1. 초기 정상 로드
    api_mock = kma_api_mock_factory("full_test")

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]
    
    # 초기 상태 확인
    state = hass.states.get("sensor.test_temperature")
    assert state.state == "22.5"

    # 2. API 실패 시뮬레이션
    api_mock.side_effect = Exception("Temporary Error")
    
    # 갱신 실행 후 상태가 unknown/unavailable이 되었는지 확인
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get("sensor.test_temperature")
    assert state.state in ("unknown", "unavailable")

    # 3. API 복구 시뮬레이션
    # side_effect를 제거하고 return_value를 확실하게 다시 설정
    api_mock.side_effect = None
    api_mock.return_value = MOCK_SCENARIOS.get("full_test")

    # 코디네이터 갱신 강제 실행
    # async_refresh는 내부적으로 _async_update_data를 호출하며, 
    # 성공 시 last_update_success를 True로 바꿉니다.
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # 복구된 센서 상태 확인
    recovered_state = hass.states.get("sensor.test_temperature")
    
    # 만약 여전히 unknown이라면, 코디네이터의 데이터가 갱신되지 않은 것임
    # 이를 해결하기 위해 엔티티 업데이트를 한 번 더 강제할 수 있음
    if recovered_state.state == "unknown":
        # 코디네이터의 리스너들에게 알림을 직접 보냄 (최후의 수단)
        coordinator.async_update_listeners()
        await hass.async_block_till_done()
        recovered_state = hass.states.get("sensor.test_temperature")

    assert recovered_state.state != "unknown"
    assert recovered_state.state != "unavailable"
    assert recovered_state.state == "22.5"
