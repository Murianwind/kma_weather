import pytest
from custom_components.kma_weather.const import DOMAIN

@pytest.mark.asyncio
async def test_normal_seoul_weather(hass, mock_config_entry, kma_api_mock_factory):
    """1. 서울 정상 데이터 테스트"""
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    
    kma_api_mock_factory("seoul_normal") 
    
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    temp_state = hass.states.get("sensor.test_temperature")
    
    assert temp_state is not None, "온도 센서가 생성되지 않았습니다."
    assert temp_state.state == "22.5"

    # 테스트 종료 시 컴포넌트를 언로드해 백그라운드 스레드 정리
    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_abnormal_jeju_missing_data(hass, mock_config_entry, kma_api_mock_factory):
    """3. 제주 데이터 일부 누락 테스트"""
    hass.config.latitude = 33.51
    hass.config.longitude = 126.52
    
    kma_api_mock_factory("jeju_abnormal")
    
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    temp_state = hass.states.get("sensor.test_temperature")
    assert temp_state is not None, "온도 센서가 생성되지 않았습니다."
    assert temp_state.state in ["unknown", "unavailable"]

    # 테스트 종료 시 컴포넌트를 언로드해 백그라운드 스레드 정리
    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
