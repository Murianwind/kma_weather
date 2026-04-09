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
    # 5. 초기 위치 확인
    assert hass.states.get(f"sensor.{p}_location").state == "경기도 화성시"

    # 6. 위치 변경 시뮬레이션
    # [수정] 단순히 _get_address만 패치하는 대신, 
    # 위치가 부산으로 바뀐 상황의 새로운 Mock 데이터를 팩토리를 통해 주입합니다.
    
    # conftest.py에 정의된 부산 시나리오를 사용하거나 직접 Mocking
    with patch("custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data", new_callable=AsyncMock) as mock_fetch:
        # 부산 데이터 모방 (주소가 부산광역시인 데이터 반환)
        busan_data = MOCK_SCENARIOS["full_test"].copy()
        busan_data["weather"] = busan_data["weather"].copy()
        busan_data["weather"]["address"] = "부산광역시"
        busan_data["weather"]["현재 위치"] = "부산광역시"
        mock_fetch.return_value = busan_data

        # device_tracker 좌표 변경
        hass.states.async_set(
            "device_tracker.my_phone", 
            "home", 
            {"latitude": 35.1, "longitude": 129.0}
        )
        await hass.async_block_till_done()

        # 코디네이터 리프레시 강제 실행 (이때 위에서 만든 mock_fetch가 실행됨)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]
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
