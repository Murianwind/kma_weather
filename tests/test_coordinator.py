import pytest
from homeassistant.config_entries import ConfigEntryState
from custom_components.kma_weather.const import DOMAIN

@pytest.mark.asyncio
async def test_normal_seoul_weather(hass, mock_config_entry, kma_api_mock_factory):
    """1. 서울 정상 데이터 테스트 (정수 변환 반영)"""
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    
    kma_api_mock_factory("seoul_normal") 
    
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # 통합 구성요소가 정상 로드되었는지 먼저 확인
    assert mock_config_entry.state == ConfigEntryState.LOADED

    # [수정] sensor.py의 int() 변환 로직에 따라 22.5 -> "22"로 기대값 변경
    temp_state = hass.states.get("sensor.test_temperature")
    assert temp_state is not None
    assert temp_state.state == "22" 


@pytest.mark.asyncio
async def test_abnormal_jeju_missing_data(hass, mock_config_entry, kma_api_mock_factory):
    """3. 제주 데이터 일부 누락 테스트"""
    hass.config.latitude = 33.51
    hass.config.longitude = 126.52
    
    kma_api_mock_factory("jeju_abnormal")
    
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.LOADED

    # 데이터가 None일 때 센서 상태 확인
    temp_state = hass.states.get("sensor.test_temperature")
    assert temp_state is not None
    
    # 값이 None일 때 HA 센서의 기본값은 "unknown"
    assert temp_state.state == "unknown"
