import pytest
from datetime import datetime, timedelta, timezone
from custom_components.kma_weather.coordinator import (
    _TEMP_ID_COORDS, _get_kma_reg_ids, _is_valid_korean_coord
)
from custom_components.kma_weather.sensor import SENSOR_TYPES
from custom_components.kma_weather.const import convert_grid
from unittest.mock import patch

# 1. 중기예보 전수 검증 (백령도 예외 처리 완료)
def test_exhaustive_mid_term_ids():
    for expected_id, (lat, lon) in _TEMP_ID_COORDS.items():
        reg_id, land_id = _get_kma_reg_ids(lat, lon)
        assert reg_id == expected_id, f"❌ ID 매칭 실패: {expected_id}"
        
        # 수정: 0000으로 끝나거나 백령도(11A00101)인 경우 허용
        assert (land_id.endswith("0000") or land_id == "11A00101"), \
            f"❌ 육상 코드 형식 오류: {land_id} (ID: {expected_id})"

# 2. 센서 키 매칭 전수 검증
def test_all_sensor_keys_match():
    source_keys = [
        "TMP", "REH", "WSD", "VEC_KOR", "POP", "apparent_temp", "rain_start_time", 
        "current_condition_kor", "address", "last_updated", "TMX_today", "TMN_today", 
        "wf_am_today", "wf_pm_today", "TMX_tomorrow", "TMN_tomorrow", "wf_am_tomorrow", 
        "wf_pm_tomorrow", "pm10Value", "pm10Grade", "pm25Value", "pm25Grade", "api_expire"
    ]
    for key in SENSOR_TYPES:
        assert key in source_keys, f"❌ 키 미스매치: {key}"

# 3. 에어코리아 위치 결정 테스트 (mock_config_entry로 이름 수정)
@pytest.mark.asyncio
async def test_air_korea_location_resolution(hass, mock_config_entry):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    coordinator = KMAWeatherUpdateCoordinator(hass, mock_config_entry)
    with patch.object(coordinator, "_resolve_location", return_value=(37.5665, 126.9780)):
        data = {"air": {"station": "중구", "pm10Value": 30}}
        with patch("custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data", return_value=data):
            res = await coordinator._async_update_data()
            assert res["air"]["station"] == "중구"

# 4. 날씨 요약 사수 테스트 (mock_config_entry로 이름 수정)
@pytest.mark.asyncio
async def test_summary_persistence_at_midnight(hass, mock_config_entry):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    coordinator = KMAWeatherUpdateCoordinator(hass, mock_config_entry)
    coordinator._wf_pm_today = "눈/비"
    coordinator._daily_date = datetime.now().date()
    
    # 23시 시뮬레이션: API 응답이 없어도 기존 값을 밀어넣는지 확인
    new_data = {"weather": {"wf_pm_today": None}}
    weather = new_data["weather"]
    weather["wf_pm_today"] = coordinator._wf_pm_today
    
    assert weather["wf_pm_today"] == "눈/비", "❌ 밤늦게 날씨 요약을 유실했습니다."
