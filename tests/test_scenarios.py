import pytest
from unittest.mock import patch, AsyncMock
from homeassistant.util import dt as dt_util
from custom_components.kma_weather.const import DOMAIN

@pytest.mark.asyncio
async def test_kma_full_scenarios(hass, mock_config_entry, kma_api_mock_factory, freezer):
    """8가지 통합 시나리오 테스트"""
    
    # 기본 좌표 설정
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    
    # 초기 데이터 및 셋업
    kma_api_mock_factory("full_test")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    p = "test"

    # --- 시나리오 1. 개별 센서 검증 ---
    state = hass.states.get(f"sensor.{p}_temperature")
    assert state.state == "22"

    # --- 시나리오 2. 예보 10일치 검증 ---
    response = await hass.services.async_call(
        "weather", "get_forecasts", {"type": "twice_daily"},
        target={"entity_id": f"weather.{p}_weather"},
        blocking=True, return_response=True,
    )
    forecast = response[f"weather.{p}_weather"]["forecast"]
    assert len(forecast) >= 10

    # --- 시나리오 5 & 6. 현재 위치 출력 및 변경 시 갱신 ---
    assert hass.states.get(f"sensor.{p}_location").state == "경기도 화성시"

    # 6. 위치 변경 시뮬레이션
    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]
    
    # 클래스가 아닌 코디네이터 내부의 api 인스턴스 메서드를 직접 패치
    with patch.object(coordinator.api, "_get_address", AsyncMock(return_value="부산광역시")):
        # 좌표 변경
        hass.states.async_set("device_tracker.my_phone", "home", {"latitude": 35.1, "longitude": 129.0})
        await hass.async_block_till_done()

        # 데이터 갱신 강제 실행
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        # 이제 확실히 부산광역시가 나와야 함
        assert hass.states.get(f"sensor.{p}_location").state == "부산광역시"

    # --- 시나리오 7 & 8. 데이터 누락 및 복원 ---
    kma_api_mock_factory("jeju_missing")
    await coordinator.async_refresh()
    assert hass.states.get(f"sensor.{p}_temperature").state == "unknown"

    kma_api_mock_factory("full_test")
    await coordinator.async_refresh()
    assert hass.states.get(f"sensor.{p}_temperature").state == "22"

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
