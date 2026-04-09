import pytest
from custom_components.kma_weather.const import DOMAIN

@pytest.mark.asyncio
async def test_normal_seoul_weather(hass, mock_config_entry, kma_api_mock_factory):
    """1. 서울 정상 데이터 테스트"""
    # [핵심 수정] Pytest 가상 환경의 기본 좌표를 한국(서울)으로 설정
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    
    # Mock 데이터 주입
    kma_api_mock_factory("seoul_normal") 
    
    # 컴포넌트 셋업
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # [핵심 수정] 올바른 엔티티 ID로 변경 (sensor.test_temperature)
    temp_state = hass.states.get("sensor.test_temperature")
    
    # 센서가 정상적으로 생성되었는지 확실히 체크
    assert temp_state is not None, "온도 센서가 생성되지 않았습니다."
    assert temp_state.state == "22.5" # 모방 데이터와 일치하는지 확인


@pytest.mark.asyncio
async def test_abnormal_jeju_missing_data(hass, mock_config_entry, kma_api_mock_factory):
    """3. 제주 데이터 일부 누락 테스트"""
    hass.config.latitude = 33.51 # 제주도 좌표
    hass.config.longitude = 126.52
    
    kma_api_mock_factory("jeju_abnormal")
    
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # 데이터가 Null일 때 센서 상태 확인
    temp_state = hass.states.get("sensor.test_temperature")
    assert temp_state is not None, "온도 센서가 생성되지 않았습니다."
    
    # 값이 None일 때 HA는 보통 "unknown" 또는 "unavailable" 상태가 됨
    assert temp_state.state in ["unknown", "unavailable"]
