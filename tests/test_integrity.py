import pytest
import math
from custom_components.kma_weather.coordinator import (
    _TEMP_ID_COORDS, 
    _get_kma_reg_ids, 
    _land_code,
    _is_valid_korean_coord
)
from custom_components.kma_weather.sensor import SENSOR_TYPES
from custom_components.kma_weather.const import convert_grid

# 1. 중기예보 구역 ID 전수 검증
def test_exhaustive_mid_term_ids():
    """테이블에 정의된 모든 ID 좌표에 대해 최접점 매칭이 되는지 확인"""
    for expected_id, (lat, lon) in _TEMP_ID_COORDS.items():
        # 해당 좌표를 입력했을 때 자기 자신이 가장 가까운 ID로 나와야 함
        reg_id, land_id = _get_kma_reg_ids(lat, lon)
        assert reg_id == expected_id, f"❌ ID 매칭 실패: 좌표({lat}, {lon}) -> 기대값 {expected_id}, 실제값 {reg_id}"
        
        # 육상예보 코드(Land ID)가 반드시 존재해야 함
        assert land_id is not None, f"❌ 육상 코드 누락: ID {expected_id}"
        assert land_id.endswith("00000") or land_id == expected_id, f"❌ 육상 코드 형식 오류: {land_id}"

# 2. 단기예보 격자 변환 경계 검증 (대한민국 4극점)
@pytest.mark.parametrize("name, lat, lon", [
    ("최북단(강원 고성)", 38.61, 128.35),
    ("최남단(마라도)", 33.11, 126.26),
    ("최서단(격렬비열도)", 36.57, 125.55),
    ("최동단(독도)", 37.24, 131.86),
])
def test_short_term_grid_boundaries(name, lat, lon):
    """대한민국 외곽 지역 좌표가 유효한 격자값으로 변환되는지 확인"""
    nx, ny = convert_grid(lat, lon)
    assert 1 <= nx <= 149, f"❌ {name} NX 범위 초과: {nx}"
    assert 1 <= ny <= 253, f"❌ {name} NY 범위 초과: {ny}"

# 3. 센서 키 매칭 전수 검증
def test_all_sensor_keys_match_coordinator():
    """sensor.py의 모든 SENSOR_TYPES 키가 실제 데이터 구조에 존재하는지 검증"""
    # coordinator가 생성하는 모든 데이터 키 리스트 시뮬레이션
    weather_data_keys = [
        "TMP", "REH", "WSD", "VEC_KOR", "POP", "apparent_temp", 
        "rain_start_time", "current_condition_kor", "address", 
        "last_updated", "TMX_today", "TMN_today", "wf_am_today", 
        "wf_pm_today", "TMX_tomorrow", "TMN_tomorrow", 
        "wf_am_tomorrow", "wf_pm_tomorrow"
    ]
    air_data_keys = [
        "pm10Value", "pm10Grade", "pm25Value", "pm25Grade"
    ]
    diagnostic_keys = ["api_expire"]

    all_source_keys = weather_data_keys + air_data_keys + diagnostic_keys

    for sensor_key in SENSOR_TYPES:
        assert sensor_key in all_source_keys, f"❌ 센서 키 미스매치: sensor.py의 '{sensor_key}'가 데이터 소스에 없습니다."

# 4. 좌표 유효성 검사 로직 전수 검증
def test_is_valid_korean_coord_logic():
    """좌표 유효성 검사 경계값 테스트"""
    assert _is_valid_korean_coord(37.0, 127.0) is True  # 내륙
    assert _is_valid_korean_coord(30.0, 120.0) is False # 국외
    assert _is_valid_korean_coord(float('nan'), 127.0) is False
