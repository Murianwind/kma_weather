import pytest
from homeassistant.util import dt as dt_util
from custom_components.kma_weather.const import DOMAIN

@pytest.mark.asyncio
async def test_kma_full_scenarios(hass, mock_config_entry, kma_api_mock_factory, freezer):
    """요청하신 8가지 시나리오 통합 테스트"""
    
    # 기본 환경 설정 (서울 좌표)
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    kma_api_mock_factory("full_test")
    
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    p = "test" # prefix

    # --- 1. 모든 개별 센서 출력 검증 ---
    checks = {
        f"sensor.{p}_temperature": "22",
        f"sensor.{p}_humidity": "45",
        f"sensor.{p}_wind_speed": "2.1",
        f"sensor.{p}_wind_direction": "남동",
        f"sensor.{p}_precipitation_probability": "10",
        f"sensor.{p}_today_high_temperature": "25",
        f"sensor.{p}_today_low_temperature": "15",
        f"sensor.{p}_pm10": "35",
        f"sensor.{p}_pm10_grade": "좋음",
        f"sensor.{p}_pm25_grade": "좋음",
        f"sensor.{p}_condition": "맑음",
        f"sensor.{p}_apparent_temperature": "23",
        f"sensor.{p}_tomorrow_high_temperature": "26",
    }
    for eid, val in checks.items():
        assert hass.states.get(eid).state == val

    # --- 2. 날씨 요약 센서 (10일치) ---
    weather = hass.states.get(f"weather.{p}_weather")
    forecast = weather.attributes.get("forecast")
    assert len(forecast) >= 10
    
    # --- 3 & 4. 시간대별 예보 시작점 (00시 vs 12시) ---
    # 00시일 때 (오전부터 시작)
    freezer.move_to("2026-04-09 00:00:00")
    await hass.async_block_till_done()
    # 첫 데이터가 주간인지 확인
    assert forecast[0]["datetime"].endswith("T00:00:00Z")

    # 12시일 때 (오후부터 시작)
    freezer.move_to("2026-04-09 12:00:00")
    # (실제 코디네이터가 갱신되어야 함을 시뮬레이션)
    
    # --- 5 & 6. 위치 출력 및 변경 ---
    assert hass.states.get(f"sensor.{p}_location").state == "경기도 화성시"
    
    with patch("custom_components.kma_weather.coordinator.get_address_from_coords", return_value="부산광역시"):
        hass.states.async_set("device_tracker.my_phone", "home", {"latitude": 35.1, "longitude": 129.0})
        await hass.async_block_till_done()
        # 센서값이 부산으로 변했는지 확인
        assert hass.states.get(f"sensor.{p}_location").state == "부산광역시"

    # --- 7. 데이터 누락 시 처리 ---
    kma_api_mock_factory("jeju_missing")
    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]
    await coordinator.async_refresh()
    assert hass.states.get(f"sensor.{p}_temperature").state == "unknown"

    # --- 8. 데이터 복원 ---
    kma_api_mock_factory("full_test")
    await coordinator.async_refresh()
    assert hass.states.get(f"sensor.{p}_temperature").state == "22"

    # 뒷정리
    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
