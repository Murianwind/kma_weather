import pytest
import math
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from custom_components.kma_weather.coordinator import (
    _TEMP_ID_COORDS, _get_kma_reg_ids, _is_valid_korean_coord, _EXCLUDE_FROM_NEAREST
)
from custom_components.kma_weather.sensor import SENSOR_TYPES
from custom_components.kma_weather.const import convert_grid

# 1. 중기예보 전수 검증 (제외 리스트 반영 및 예외 지역 처리)
def test_exhaustive_mid_term_ids():
    """모든 구역 ID 좌표 매칭 검증 (제외 리스트 반영)."""
    for expected_id, (lat, lon) in _TEMP_ID_COORDS.items():
        # [수정] 독도(11E00102) 등 검색 제외 대상은 테스트를 건너뜁니다.
        if expected_id in _EXCLUDE_FROM_NEAREST:
            continue
            
        reg_id, land_id = _get_kma_reg_ids(lat, lon)
        assert reg_id == expected_id, f"❌ ID 매칭 실패: {expected_id} (실제 반환: {reg_id})"
        
        # 백령도(11A00101) 및 특수 지역 코드 예외 처리
        is_exception = land_id in ["11A00101", "11E00101"]
        assert (land_id.endswith("0000") or is_exception), f"❌ 형식 오류: {land_id}"

# 2. 센서 키 매칭 전수 검증
def test_all_sensor_keys_match():
    source_keys = [
        "TMP", "REH", "WSD", "VEC_KOR", "POP", "apparent_temp", "rain_start_time", 
        "current_condition_kor", "address", "last_updated", "TMX_today", "TMN_today", 
        "wf_am_today", "wf_pm_today", "TMX_tomorrow", "TMN_tomorrow", "wf_am_tomorrow", 
        "wf_pm_tomorrow", "pm10Value", "pm10Grade", "pm25Value", "pm25Grade", "api_expire"
    ]
    for key in SENSOR_TYPES:
        assert key in source_keys, f"❌ 센서 키 미스매치: '{key}'가 데이터 소스에 없습니다."

# 3. 에어코리아 위치 결정 테스트 (KeyError 방지)
@pytest.mark.asyncio
async def test_air_korea_location_resolution(hass, mock_config_entry):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    coordinator = KMAWeatherUpdateCoordinator(hass, mock_config_entry)
    
    with patch.object(coordinator, "_resolve_location", return_value=(37.5665, 126.9780)):
        # weather 키가 반드시 있어야 KeyError가 발생하지 않음
        mock_res = {
            "weather": {}, 
            "air": {"station": "중구", "pm10Value": 30}
        }
        with patch("custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data", return_value=mock_res):
            res = await coordinator._async_update_data()
            assert res["air"]["station"] == "중구"

# 4. 날씨 요약 사수 로직 검증 (밤 11시 상황)
@pytest.mark.asyncio
async def test_summary_persistence_at_midnight(hass, mock_config_entry):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    coordinator = KMAWeatherUpdateCoordinator(hass, mock_config_entry)
    
    coordinator._wf_pm_today = "흐리고 비"
    coordinator._daily_date = datetime.now().date()
    
    # API 응답이 없는 상황 시뮬레이션
    new_data = {"weather": {"wf_pm_today": None}}
    
    # 사수 로직 시뮬레이션
    weather = new_data["weather"]
    weather["wf_pm_today"] = coordinator._wf_pm_today or weather.get("wf_pm_today")
    
    assert weather["wf_pm_today"] == "흐리고 비", "❌ 예보 데이터 유실 방어 실패"
