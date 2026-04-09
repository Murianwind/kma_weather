import pytest
from unittest.mock import patch, AsyncMock
from homeassistant.util import dt as dt_util
from custom_components.kma_weather.const import DOMAIN
try:
    from tests.conftest import MOCK_SCENARIOS
except ImportError:
    # 로컬 실행 환경에 따라 임포트 경로가 달라질 수 있으므로 fallback 추가
    from conftest import MOCK_SCENARIOS

@pytest.mark.asyncio
async def test_kma_full_scenarios(hass, mock_config_entry, kma_api_mock_factory, freezer):
    """8가지 통합 시나리오 테스트 + 안정성(Fault Tolerance) 전수 검증"""
    
    # 기본 좌표 설정
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98
    
    # 초기 데이터 및 셋업
    kma_api_mock_factory("full_test")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    p = "test"
    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]

    # --- 시나리오 1. 개별 센서 검증 ---
    state = hass.states.get(f"sensor.{p}_temperature")
    assert state.state == "22"
    assert state.attributes.get("unit_of_measurement") == "°C"
    assert state.attributes.get("device_class") == "temperature"

    assert hass.states.get(f"sensor.{p}_humidity").state == "60"
    assert hass.states.get(f"sensor.{p}_wind_speed").state == "3"
    assert hass.states.get(f"sensor.{p}_precipitation_prob").state == "10"
    assert hass.states.get(f"sensor.{p}_condition").state == "맑음"

    # --- 시나리오 2. 예보 10일치 검증 ---
    response = await hass.services.async_call(
        "weather", "get_forecasts", {"type": "twice_daily"},
        target={"entity_id": f"weather.{p}_weather"},
        blocking=True, return_response=True,
    )
    forecast = response[f"weather.{p}_weather"]["forecast"]
    assert len(forecast) >= 10
    
    # 첫 번째 예보 아이템 상세 체크 (KeyError 방어 적용)
    f0 = forecast[0]
    assert "temperature" in f0
    assert f0["temperature"] == 24
    if "templow" in f0:
        assert f0["templow"] == 18
    assert f0["condition"] == "partlycloudy"

    # --- 시나리오 3. 미세먼지 센서 검증 ---
    pm10 = hass.states.get(f"sensor.{p}_pm10")
    assert pm10.state == "35"
    assert pm10.attributes.get("icon") == "mdi:blur"
    
    pm25 = hass.states.get(f"sensor.{p}_pm25")
    assert pm25.state == "20"
    
    assert hass.states.get(f"sensor.{p}_pm10_grade").state == "보통"
    assert hass.states.get(f"sensor.{p}_pm25_grade").state == "보통"

    # --- 시나리오 4. 부가 센서 및 진단 정보 검증 ---
    assert hass.states.get(f"sensor.{p}_apparent_temperature").state == "23"
    assert hass.states.get(f"sensor.{p}_wind_direction").state == "북서"
    
    location_sensor = hass.states.get(f"sensor.{p}_location")
    assert location_sensor.state == "경기도 화성시"
    assert location_sensor.attributes.get("air_korea_station") == "종로구"

    # --- 시나리오 5 & 6. 현재 위치 출력 및 변경 시 갱신 ---
    # 5. 초기 위치 확인 (경기도 화성시)
    assert hass.states.get(f"sensor.{p}_location").state == "경기도 화성시"

    # 6. 위치 변경 시뮬레이션 (화성 -> 부산)
    with patch("custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data", new_callable=AsyncMock) as mock_fetch:
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

        # 코디네이터 리프레시 강제 실행
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        # 부산광역시로 변경되었는지 확인
        assert hass.states.get(f"sensor.{p}_location").state == "부산광역시"

    # --- 시나리오 7 & 8. 데이터 누락 및 복원 ---
    # 7. 데이터 누락 상황 (제주 미싱 데이터 주입)
    kma_api_mock_factory("jeju_missing")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(f"sensor.{p}_temperature").state == "unknown"

    # 8. 데이터 정상 복원
    kma_api_mock_factory("full_test")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(f"sensor.{p}_temperature").state == "22"

    # --- 시나리오 10. 모든 센서 안정성(Fault Tolerance) 전수 검사 ---
    # 요구사항: 어떤 데이터가 들어와도 unavailable이 되지 않고 unknown이 되어야 함
    from custom_components.kma_weather.sensor import SENSOR_TYPES

    polluted_data = {
        "weather": {key: "-" for key in SENSOR_TYPES},
        "air": {key: "-" for key in SENSOR_TYPES}
    }
    
    with patch("custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data", new_callable=AsyncMock) as mock_polluted:
        mock_polluted.return_value = polluted_data
        
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        for sensor_type, details in SENSOR_TYPES.items():
            entity_id = f"sensor.{p}_{details[4]}"
            state = hass.states.get(entity_id)
            
            if state:
                # [핵심] 절대 unavailable 상태가 되면 안 됨
                assert state.state != "unavailable", f"Failure: {entity_id} is unavailable!"
                # [핵심] 수치형/문자형 무관하게 쓰레기 데이터("-")는 unknown 처리
                assert state.state == "unknown", f"Failure: {entity_id} state is {state.state}, expected unknown"

    # --- 시나리오 11. 예기치 않은 데이터(Garbage) 주입 시 강건성 확인 ---
    garbage_data = {
        "weather": {key: "BAD_DATA_999" for key in SENSOR_TYPES},
        "air": {key: "BAD_DATA_999" for key in SENSOR_TYPES}
    }
    with patch("custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data", new_callable=AsyncMock) as mock_garbage:
        mock_garbage.return_value = garbage_data
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        for sensor_type, details in SENSOR_TYPES.items():
            entity_id = f"sensor.{p}_{details[4]}"
            state = hass.states.get(entity_id)
            if state:
                # 단위가 지정된 센서(수치형)는 문자열 처리 실패 시 unknown이 되어야 함
                if details[1] is not None:
                    assert state.state == "unknown", f"Failure: Numeric sensor {entity_id} didn't handle garbage properly"
                
                # 어떤 경우에도 엔티티 자체가 죽으면 안 됨
                assert state.state != "unavailable"

    # 통합 테스트 종료 및 언로드
    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
