import pytest
from unittest.mock import AsyncMock, patch
from homeassistant import config_entries

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

    # Weather 엔티티는 온도를 state가 아닌 속성으로 제공하지 않을 수 있음
    attrs = state.attributes
    assert "temperature_unit" in attrs
    assert "wind_speed_unit" in attrs
    assert "supported_features" in attrs

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
    forecast = response[entity_id]["forecast"]
    assert isinstance(forecast, list)
    assert len(forecast) > 0


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
    hass, mock_config_entry
):
    """API 실패 시 예외 처리 테스트."""
    with patch(
        "custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data",
        new=AsyncMock(side_effect=Exception("API Error")),
    ):
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(
            mock_config_entry.entry_id
        )
        await hass.async_block_till_done()

        state = hass.states.get("sensor.test_temperature")
        assert state is not None
        assert state.state in ("unknown", "unavailable")


@pytest.mark.asyncio
async def test_sensor_recovery_after_api_restore(
    hass, mock_config_entry, kma_api_mock_factory
):
    """API 장애 후 복구 시 센서가 정상 동작하는지 테스트."""
    # 초기 정상 데이터
    kma_api_mock_factory("full_test")

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(
        mock_config_entry.entry_id
    )
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][
        mock_config_entry.entry_id
    ]

    # 초기 상태 확인
    initial_state = hass.states.get("sensor.test_temperature")
    assert initial_state is not None
    assert initial_state.state == "22"

    # API 실패 시뮬레이션
    with patch(
        "custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data",
        new=AsyncMock(side_effect=Exception("Temporary Error")),
    ):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    failed_state = hass.states.get("sensor.test_temperature")
    assert failed_state is not None
    assert failed_state.state in ("unknown", "unavailable", "22")

    # 정상 데이터 복구
    kma_api_mock_factory("full_test")
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    recovered_state = hass.states.get("sensor.test_temperature")
    assert recovered_state is not None
    assert recovered_state.state == "22"
