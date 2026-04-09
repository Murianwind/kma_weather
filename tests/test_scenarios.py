import pytest
from homeassistant.util import dt as dt_util
from homeassistant.components.weather import (
    ATTR_FORECAST, ATTR_FORECAST_TIME, ATTR_FORECAST_TEMP, ATTR_FORECAST_CONDITION
)

@pytest.mark.asyncio
async def test_kma_full_scenarios(hass, mock_config_entry, kma_api_mock_factory, freezer):
    """8가지 통합 시나리오 테스트"""
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    kma_api_mock_factory("full_test")
    
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    p = "test" # prefix

    # --- 1. 모든 개별 센서 데이터 출력 검증 ---
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
        assert state is not None, f"{eid} 센서 누락"
        assert state.state == val

    # --- 2. 날씨 요약 (Forecast 10일치) 검증 ---
    # 서비스 호출을 통해 예보 데이터 가져오기 (최신 HA 방식)
    response = await hass.services.async_call(
        "weather", "get_forecasts", {"type": "twice_daily"},
        target={"entity_id": f"weather.{p}_weather"},
        blocking=True, return_response=True,
    )
    forecast = response[f"weather.{p}_weather"]["forecast"]
    assert len(forecast) >= 10
    assert forecast[0]["temperature"] == 25

    # --- 3. 00시 변환 시 주간부터 시작하는가? ---
    freezer.move_to("2026-04-09 00:00:00")
    # (첫 번째 예보의 datetime이 당일 00시인지 확인)
    assert forecast[0]["datetime"].endswith("T00:00:00Z")

    # --- 5 & 6. 위치 출력 및 변경 시 갱신 ---
    assert hass.states.get(f"sensor.{p}_location").state == "경기도 화성시"
    
    # 위치 변경 시뮬레이션
    with patch("custom_components.kma_weather.coordinator.get_address_from_coords", return_value="부산광역시"):
        hass.states.async_set("device_tracker.my_phone", "home", {"latitude": 35.1, "longitude": 129.0})
        await hass.async_block_till_done()
        assert hass.states.get(f"sensor.{p}_location").state == "부산광역시"

    # --- 7 & 8. 데이터 누락 및 복원 ---
    # 누락
    kma_api_mock_factory("jeju_missing")
    coordinator = hass.data["kma_weather"][mock_config_entry.entry_id]
    await coordinator.async_refresh()
    assert hass.states.get(f"sensor.{p}_temperature").state == "unknown"

    # 복원
    kma_api_mock_factory("full_test")
    await coordinator.async_refresh()
    assert hass.states.get(f"sensor.{p}_temperature").state == "22"

    # 정리
    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
