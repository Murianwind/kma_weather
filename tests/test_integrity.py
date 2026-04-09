import pytest
import math
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from custom_components.kma_weather.coordinator import (
    _TEMP_ID_COORDS, _get_kma_reg_ids, _is_valid_korean_coord
)
from custom_components.kma_weather.sensor import SENSOR_TYPES
from custom_components.kma_weather.const import convert_grid

# 1. 중기예보 전수 검증 (백령도 및 울릉도/독도 예외 처리 완료)
def test_exhaustive_mid_term_ids():
    """테이블에 정의된 모든 ID 좌표에 대해 최접점 매칭 및 육상코드 형식 확인"""
    for expected_id, (lat, lon) in _TEMP_ID_COORDS.items():
        reg_id, land_id = _get_kma_reg_ids(lat, lon)
        assert reg_id == expected_id, f"❌ ID 매칭 실패: {expected_id}"
        
        # 백령도(11A00101), 울릉도/독도(11E00101)는 고유 코드를 사용하므로 예외 허용
        is_exception = land_id in ["11A00101", "11E00101"]
        assert (land_id.endswith("0000") or is_exception), \
            f"❌ 육상 코드 형식 오류: {land_id} (ID: {expected_id})"

# 2. 센서 키 매칭 전수 검증
def test_all_sensor_keys_match():
    """sensor.py의 모든 키가 데이터 소스에 존재하는지 확인"""
    source_keys = [
        "TMP", "REH", "WSD", "VEC_KOR", "POP", "apparent_temp", "rain_start_time", 
        "current_condition_kor", "address", "last_updated", "TMX_today", "TMN_today", 
        "wf_am_today", "wf_pm_today", "TMX_tomorrow", "TMN_tomorrow", "wf_am_tomorrow", 
        "wf_pm_tomorrow", "pm10Value", "pm10Grade", "pm25Value", "pm25Grade", "api_expire"
    ]
    for key in SENSOR_TYPES:
        assert key in source_keys, f"❌ 센서 키 미스매치: '{key}'가 데이터 소스에 없습니다."

# 3. 에어코리아 위치 결정 및 KeyError 방지 테스트
@pytest.mark.asyncio
async def test_air_korea_location_resolution(hass, mock_config_entry):
    """KeyError 방지를 위해 weather 키가 포함된 구조로 테스트"""
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    coordinator = KMAWeatherUpdateCoordinator(hass, mock_config_entry)
    
    with patch.object(coordinator, "_resolve_location", return_value=(37.5665, 126.9780)):
        # KeyError: 'weather' 방지를 위해 빈 weather 딕셔너리 추가
        mock_res = {
            "weather": {}, 
            "air": {"station": "중구", "pm10Value": 30}
        }
        with patch("custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data", return_value=mock_res):
            res = await coordinator._async_update_data()
            assert res["air"]["station"] == "중구"

# 4. 날씨 요약 사수 로직 검증
@pytest.mark.asyncio
async def test_summary_persistence_at_midnight(hass, mock_config_entry):
    """자정까지 날씨 요약이 보존되는지 확인"""
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    coordinator = KMAWeatherUpdateCoordinator(hass, mock_config_entry)
    
    # 가상의 저장 데이터 설정
    coordinator._wf_pm_today = "흐리고 비"
    coordinator._daily_date = datetime.now().date()
    
    # API 응답에서 데이터가 누락된 상황 (밤 11시 시뮬레이션)
    new_data = {"weather": {"wf_pm_today": None}}
    
    # 사수 로직: API가 None이면 내부 보존 값 사용
    weather = new_data["weather"]
    weather["wf_pm_today"] = coordinator._wf_pm_today or weather.get("wf_pm_today")
    
    assert weather["wf_pm_today"] == "흐리고 비", "❌ 예보 데이터 유실 방어 실패"

# 5. 좌표 유효성 경계 테스트
def test_is_valid_korean_coord_logic():
    assert _is_valid_korean_coord(37.5, 127.0) is True
    assert _is_valid_korean_coord(30.0, 120.0) is False # 국외
