import pytest
from custom_components.kma_weather.const import DOMAIN

@pytest.mark.asyncio
async def test_normal_seoul_weather(hass, mock_config_entry, kma_api_mock_factory):
    """1. 서울 정상 데이터 테스트"""
    kma_api_mock_factory("seoul_normal") # 서울 데이터 주입!
    
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # 온도가 22.5도로 나오는지, 미세먼지 등급이 1(좋음)인지 확인
    assert hass.states.get("sensor.test_current_temperature").state == "22.5"
    assert hass.states.get("sensor.test_pm10_grade").state == "좋음" # 내부 번역 로직 검증


@pytest.mark.asyncio
async def test_abnormal_jeju_missing_data(hass, mock_config_entry, kma_api_mock_factory):
    """3. 제주 데이터 일부 누락 테스트"""
    kma_api_mock_factory("jeju_abnormal") # 누락된 제주 데이터 주입!
    
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # 데이터가 Null일 때 에러를 뿜고 죽지 않는지, unknown으로 잘 처리되는지 확인
    assert hass.states.get("sensor.test_current_temperature").state in ["unknown", "unavailable"]


@pytest.mark.asyncio
async def test_dokdo_api_error(hass, mock_config_entry, kma_api_mock_factory):
    """4. 기상청 서버 다운(에러) 테스트"""
    kma_api_mock_factory("error") # 에러 상황 강제 발생!
    
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # 서버 다운 시 컴포넌트가 로드 실패 처리를 하거나 엔티티를 unavailable로 만드는지 검증
    # (컴포넌트 설계에 따라 달라짐)
