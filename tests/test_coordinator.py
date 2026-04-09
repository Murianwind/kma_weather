import pytest
from custom_components.kma_weather.const import DOMAIN


@pytest.mark.asyncio
async def test_normal_weather_setup(hass, mock_config_entry, kma_api_mock_factory):
    """정상 데이터(서울 좌표)로 통합 구성요소가 정상 로드되는지 검증"""
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    # conftest.py 에 정의된 올바른 시나리오 키 사용 ("seoul_normal" → "full_test")
    kma_api_mock_factory("full_test")

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # 로드 성공 여부: coordinator 가 hass.data 에 등록되어야 함
    assert mock_config_entry.entry_id in hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_missing_data_setup(hass, mock_config_entry, kma_api_mock_factory):
    """일부 데이터가 누락된 상황에서도 통합 구성요소가 오류 없이 로드되는지 검증"""
    hass.config.latitude = 33.51
    hass.config.longitude = 126.52
    # conftest.py 에 정의된 올바른 시나리오 키 사용 ("jeju_abnormal" → "jeju_missing")
    kma_api_mock_factory("jeju_missing")

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.entry_id in hass.data[DOMAIN]
