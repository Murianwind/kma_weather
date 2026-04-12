import pytest
from custom_components.kma_weather.const import DOMAIN

@pytest.mark.asyncio
async def test_normal_weather_setup(hass, mock_config_entry, kma_api_mock_factory):
    """시나리오: 정상 데이터(서울 좌표)로 통합 구성요소가 정상 로드됨"""
    
    # [Given] 서울 좌표 설정 및 정상 데이터 시나리오(full_test) 모킹
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    kma_api_mock_factory("full_test")

    # [When] 구성요소를 Home Assistant에 추가하고 셋업을 수행하면
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # [Then] 코디네이터가 DOMAIN 데이터 영역에 정상적으로 등록되어야 함
    assert mock_config_entry.entry_id in hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_missing_data_setup(hass, mock_config_entry, kma_api_mock_factory):
    """시나리오: 일부 데이터가 누락된 상황에서도 통합 구성요소가 오류 없이 로드됨"""
    
    # [Given] 제주 좌표 설정 및 데이터 누락 시나리오(jeju_missing) 모킹
    hass.config.latitude = 33.51
    hass.config.longitude = 126.52
    kma_api_mock_factory("jeju_missing")

    # [When] 구성요소를 등록하고 셋업 프로세스를 완료하면
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # [Then] 데이터가 불완전하더라도 구성요소 자체는 정상 로드되어야 함
    assert mock_config_entry.entry_id in hass.data[DOMAIN]
