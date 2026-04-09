import pytest
from unittest.mock import patch, AsyncMock
from homeassistant.util import dt as dt_util
from custom_components.kma_weather.const import DOMAIN

@pytest.mark.asyncio
async def test_kma_full_scenarios(hass, mock_config_entry, kma_api_mock_factory, freezer):
    """8가지 통합 시나리오 테스트"""
    
    # 기본 환경 설정 (서울 좌표)
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    
    # 1. 초기 데이터 주입 및 컴포넌트 설정
    kma_api_mock_factory("full_test")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    p = "test" # 설정에서 정한 prefix

    # --- 시나리오 1. 모든 개별 센서 데이터 출력 검증 ---
    # sensor.py의 SENSOR_TYPES에 정의된 id_suffix(5번째 인자)를 기준으로 함
    checks = {
        f"sensor.{p}_temperature": "22",
        f"sensor.{p}_humidity": "45",
        f"sensor.{p}_wind_speed": "2.1",
        f"sensor.{p}_wind_direction": "남동",
        f"sensor.{p}_precipitation_prob": "10",
        f"sensor.{p}_today_temp_max": "25",
        f"sensor.{p}_pm10_grade": "좋음",
        f"sensor.{p}_condition": "맑음",
        f"sensor.{p}_apparent_temperature": "23",
    }
    for eid, val in checks.items():
        state = hass.states.get(eid)
        assert state is not None, f"{eid} 센서가 생성되지 않았습니다."
        assert state.state == val, f"{eid}의 값이 예상과 다릅니다: {state.state} != {val}"

    # --- 시나리오 2. 날씨 요약 (Forecast 10일치) 검증 ---
    # 최신 HA 방식: 서비스 호출을 통해 예보 데이터를 확인
    response = await hass.services.async_call(
        "weather", "get_forecasts", {"type": "twice_daily"},
        target={"entity_id": f"weather.{p}_weather"},
        blocking=True, return_response=True,
    )
    forecast = response[f"weather.{p}_weather"]["forecast"]
    assert len(forecast) >= 10
    assert forecast[0]["temperature"] == 25 # conftest의 MOCK 데이터 기반

    # --- 시나리오 3 & 4. 00시/12시 기준 예보 시작점 검증 ---
    # 00시일 때 오늘 날짜 데이터부터 시작하는지 확인
    freezer.move_to("2026-04-09 00:00:00")
    assert forecast[0]["datetime"].startswith("2026-04-09")

    # --- 시나리오 5 & 6. 현재 위치 출력 및 변경 시 갱신 ---
    # 5. 초기 위치 확인
    assert hass.states.get(f"sensor.{p}_location").state == "경기도 화성시"

    # 6. 위치 변경 시뮬레이션
    # api_kma.py의 _get_address 메서드를 패치하여 부산광역시를 반환하게 함
    with patch("custom_components.kma_weather.api_kma.KMAWeatherAPI._get_address", AsyncMock(return_value="부산광역시")):
        # device_tracker 좌표 변경
        hass.states.async_set("device_tracker.my_phone", "home", {"latitude": 35.1, "longitude": 129.0})
        await hass.async_block_till_done()

        # 코디네이터 리프레시를 통해 새 위치 정보 반영
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        assert hass.states.get(f"sensor.{p}_location").state == "부산광역시"

    # --- 시나리오 7. 데이터 누락 시 처리 (Unknown) ---
    kma_api_mock_factory("jeju_missing")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    
    assert hass.states.get(f"sensor.{p}_temperature").state == "unknown"

    # --- 시나리오 8. 데이터 복원 시 출력 ---
    kma_api_mock_factory("full_test")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    
    assert hass.states.get(f"sensor.{p}_temperature").state == "22"

    # 테스트 종료 후 언로드 (청소)
    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
