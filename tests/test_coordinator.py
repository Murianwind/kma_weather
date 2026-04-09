import pytest
from unittest.mock import patch, MagicMock
from homeassistant.util import dt as dt_util
from custom_components.kma_weather.const import DOMAIN

# 1. 모든 센서 출력 및 데이터 정합성 테스트
async def test_all_sensors_output(hass, mock_kma_api):
    """모든 엔티티가 생성되고 API 데이터가 올바르게 매핑되는지 확인"""
    entry = MockConfigEntry(domain=DOMAIN, data={"api_key": "test", "location_mode": "zone", "prefix": "test"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # 현재 온도 센서 검증
    state = hass.states.get("sensor.test_current_temperature")
    assert state is not None
    assert state.state == "20" # mock_kma_api에서 설정한 값

# 2. 위치 변경 인식 테스트
async def test_location_tracking(hass, mock_kma_api):
    """device_tracker 위치 변경 시 데이터 갱신 여부 확인"""
    # 좌표 변경 시뮬레이션
    with patch("custom_components.kma_weather.coordinator.get_address_from_coords", return_value="새로운 주소"):
        hass.states.async_set("device_tracker.my_phone", "home", {"latitude": 37.5, "longitude": 127.0})
        await hass.async_block_till_done()
        
        location_state = hass.states.get("sensor.test_current_location")
        assert location_state.state == "새로운 주소"

# 3 & 4. 시간 흐름 및 API 업데이트 주기 테스트
async def test_update_on_time_flow(hass, mock_kma_api, freezegun):
    """시간 경과에 따라 다음 예보 데이터를 정상적으로 가져오는지 확인"""
    # 오전 8시 데이터 확인
    freezegun.move_to("2026-04-09 08:00:00")
    await async_fire_time_changed(hass, dt_util.utcnow())
    
    # 기상청 단기예보 발표 시간(예: 02:00, 05:00...) 이후 데이터 갱신 검증
    # 에어코리아 매시각 15분 내외 업데이트 로직 검증 등

# 5. 데이터 부재 시 처리 (Edge Case)
async def test_api_failure_handling(hass, aioclient_mock):
    """API 서버 다운(500 에러) 시 엔티티 상태 확인"""
    aioclient_mock.get("http://apis.data.go.kr/...", status=500)
    
    # 데이터가 없을 때 "unknown" 또는 직전 값을 유지하는지 확인
    state = hass.states.get("sensor.test_current_temperature")
    assert state.state in ["unknown", "unavailable"]
