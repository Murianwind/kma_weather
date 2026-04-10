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
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98

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

    assert mock_config_entry.entry_id not in hass.data.get(DOMAIN, {})


@pytest.mark.asyncio
async def test_weather_entity_and_forecast(
    hass, mock_config_entry, kma_api_mock_factory
):
    """Weather 엔티티 및 예보 서비스 테스트."""
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98

    kma_api_mock_factory("full_test")

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = "weather.test_weather"
    state = hass.states.get(entity_id)
    assert state is not None

    attrs = state.attributes
    assert "supported_features" in attrs
    assert "temperature_unit" in attrs

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
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98

    kma_api_mock_factory("full_test")

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]
    assert coordinator.data is not None

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success


@pytest.mark.asyncio
async def test_api_failure_handling(
    hass, mock_config_entry, kma_api_mock_factory
):
    """API 실패 시 센서 상태 검증 — 캐시 없을 때 unknown 확인."""
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98

    kma_api_mock_factory("full_test")

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]

    # coordinator는 API 실패 시 _cached_data를 반환하므로,
    # unknown 상태를 유도하려면 캐시를 먼저 비워야 함
    coordinator._cached_data = None
    with patch(
        "custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data",
        new=AsyncMock(side_effect=Exception("API Error")),
    ):
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
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98

    # 정상 데이터로 초기 설정
    kma_api_mock_factory("full_test")

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]

    initial_state = hass.states.get("sensor.test_temperature")
    assert initial_state is not None
    assert initial_state.state not in ("unknown", "unavailable"), (
        f"초기 설정 후 센서가 정상 값을 가져야 합니다. 실제: {initial_state.state}"
    )

    # 캐시 제거 후 API 실패 유도
    # (캐시가 있으면 coordinator가 캐시를 반환하여 unknown이 되지 않음)
    coordinator._cached_data = None
    with patch(
        "custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data",
        new=AsyncMock(side_effect=Exception("Temporary Error")),
    ):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    failed_state = hass.states.get("sensor.test_temperature")
    assert failed_state is not None
    assert failed_state.state in ("unknown", "unavailable"), (
        f"API 실패 후 센서가 unknown/unavailable이어야 합니다. 실제: {failed_state.state}"
    )

    # 복구 — kma_api_mock_factory 재호출 대신 patch 컨텍스트 매니저 직접 사용
    # (kma_api_mock_factory는 patch.start()를 쓰므로 재호출로는 기존 patch가 교체되지 않음)
    with patch(
        "custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data",
        new=AsyncMock(return_value=MOCK_SCENARIOS["full_test"]),
    ):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        recovered_state = hass.states.get("sensor.test_temperature")
        assert recovered_state is not None
        assert recovered_state.state not in ("unknown", "unavailable"), (
            f"API 복구 후 센서가 정상 값을 가져야 합니다. 실제: {recovered_state.state}"
        )
        # 온도 센서는 항상 정수여야 함
        assert recovered_state.state.lstrip("-").isdigit(), (
            f"온도 값이 정수여야 합니다. 실제: {recovered_state.state}"
        )
