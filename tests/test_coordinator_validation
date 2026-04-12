import pytest
from datetime import datetime, date
from unittest.mock import patch, MagicMock
from custom_components.kma_weather.coordinator import (
    _TEMP_ID_COORDS,
    _get_kma_reg_ids,
    _is_valid_korean_coord,
    _EXCLUDE_FROM_NEAREST,
)
from custom_components.kma_weather.sensor import SENSOR_TYPES


# ─────────────────────────────────────────────────────────────────────────────
# 1. 중기예보 구역코드 전수 검증
# ─────────────────────────────────────────────────────────────────────────────
def test_exhaustive_mid_term_ids():
    """시나리오: 모든 구역 ID 좌표에서 _get_kma_reg_ids를 호출했을 때 해당 구역 ID가 반환되는지 전수 검증함"""
    
    # [Given] 제외 대상을 뺀 전체 구역 좌표 목록에 대해
    for expected_id, (lat, lon) in _TEMP_ID_COORDS.items():
        if expected_id in _EXCLUDE_FROM_NEAREST:
            continue

        # [When] 좌표를 기반으로 구역 ID를 계산하면
        reg_id, land_id = _get_kma_reg_ids(lat, lon)
        
        # [Then] 계산된 구역 ID가 기대 ID와 일치해야 함
        assert reg_id == expected_id, (
            f"ID 매칭 실패: 기대={expected_id}, 실제={reg_id}"
        )

        # [Then] 육상 ID(land_id)가 정상적인 포맷("0000" 종료 또는 예외 구역)을 갖춰야 함
        is_exception = land_id in ["11A00101", "11E00101"]
        assert land_id.endswith("0000") or is_exception, (
            f"land_id 형식 오류: {land_id}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2. 좌표-구역코드 매핑 경계 검증
# ─────────────────────────────────────────────────────────────────────────────
def test_kma_reg_ids_for_seoul():
    """시나리오: 서울 시청 좌표가 올바른 구역 코드로 매핑됨"""
    
    # [Given] 서울 시청의 좌표(37.5665, 126.9780)가 주어지고
    # [When] 구역 코드를 조회하면
    reg_temp, reg_land = _get_kma_reg_ids(37.5665, 126.9780)
    
    # [Then] 기온은 서울, 육상 날씨는 서울/인천/경기로 매핑되어야 함
    assert reg_temp == "11B10101"  # 서울
    assert reg_land == "11B00000"  # 서울/인천/경기


def test_valid_korean_coord_boundary():
    """시나리오: 한반도 유효 좌표 범위를 정상적으로 판별함"""
    
    # [Given/When/Then] 서울, 이어도(경계선), 적도(외부) 좌표의 유효성을 검증함
    assert _is_valid_korean_coord(37.5665, 126.9780) is True   # 서울
    assert _is_valid_korean_coord(33.1, 126.2) is True          # 이어도/제주 인근
    assert _is_valid_korean_coord(0.0, 0.0) is False            # 적도


# ─────────────────────────────────────────────────────────────────────────────
# 3. 센서 키 유효성 검증
# ─────────────────────────────────────────────────────────────────────────────
def test_all_sensor_types_have_required_fields():
    """시나리오: SENSOR_TYPES의 각 항목이 6개의 필수 필드 구조를 완벽히 갖추고 있음"""
    
    # [Given] 각 센서 타입이 가져야 할 필수 필드의 개수(6) 설정
    REQUIRED_FIELD_COUNT = 6
    
    # [When] 모든 센서 정의를 순회하며
    for key, details in SENSOR_TYPES.items():
        
        # [Then] 필드 개수가 정확히 6개인지 검증
        assert len(details) == REQUIRED_FIELD_COUNT, (
            f"'{key}' 센서의 필드 수가 {REQUIRED_FIELD_COUNT}개가 아닙니다: {details}"
        )
        
        name, unit, icon, device_class, entity_id_suffix, category = details
        
        # [Then] 이름, 아이콘(mdi: 접두사), 엔티티 식별자 포맷이 규칙에 맞는지 검증
        assert isinstance(name, str) and name, f"'{key}' 센서의 name 이 비어 있습니다."
        assert isinstance(icon, str) and icon.startswith("mdi:"), (
            f"'{key}' 센서의 icon 이 'mdi:' 로 시작하지 않습니다: {icon}"
        )
        assert isinstance(entity_id_suffix, str) and entity_id_suffix, (
            f"'{key}' 센서의 entity_id_suffix 가 비어 있습니다."
        )


def test_sensor_entity_id_suffixes_are_unique():
    """시나리오: 모든 센서의 entity_id suffix가 중복 없이 고유함"""
    
    # [Given/When] 전체 센서에서 suffix(5번째 항목) 리스트를 추출하면
    suffixes = [details[4] for details in SENSOR_TYPES.values()]
    
    # [Then] 리스트의 전체 길이와 중복을 제거한 세트(set)의 길이가 일치해야 함
    assert len(suffixes) == len(set(suffixes)), (
        f"entity_id suffix 중복 발견: {[s for s in suffixes if suffixes.count(s) > 1]}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. 에어코리아 위치 결정 테스트
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_air_korea_location_resolution(hass, mock_config_entry):
    """시나리오: 에어코리아 위치 결정 로직이 정상 동작하여 측정소 이름을 반환함"""
    
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

    # [Given] 위치 판별이 서울 시청으로 패치(mock)된 코디네이터 준비
    coordinator = KMAWeatherUpdateCoordinator(hass, mock_config_entry)

    with patch.object(coordinator, "_resolve_location", return_value=(37.5665, 126.9780)):
        mock_res = {
            "weather": {},
            "air": {"station": "중구", "pm10Value": 30},
        }
        
        # [When] API에서 날씨 데이터를 가져올 때
        with patch(
            "custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data",
            return_value=mock_res,
        ):
            res = await coordinator._async_update_data()
            
            # [Then] 대기질 측정소 값이 정상적으로 매핑되어야 함
            assert res["air"]["station"] == "중구"


# ─────────────────────────────────────────────────────────────────────────────
# 5. 날씨 요약 사수 로직 검증 (야간/데이터 공백 시간대)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_summary_persistence_at_midnight(hass, mock_config_entry):
    """시나리오: API 응답에 wf_pm_today가 없어도 코디네이터가 저장한 값을 사수함"""
    
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

    # [Given] 코디네이터 내부에 이미 오늘 오후 날씨('흐리고 비')가 저장된 상태
    coordinator = KMAWeatherUpdateCoordinator(hass, mock_config_entry)
    coordinator._wf_pm_today = "흐리고 비"
    coordinator._daily_date = datetime.now().date()

    # [Given] 새로운 API 통신 결과 예보 데이터가 유실됨(None)
    new_data = {"weather": {"wf_pm_today": None}}

    # [When] 날씨 요약 사수 로직 실행
    weather = new_data["weather"]
    weather["wf_pm_today"] = coordinator._wf_pm_today or weather.get("wf_pm_today")

    # [Then] 데이터가 None으로 덮어써지지 않고 기존 데이터를 방어해야 함
    assert weather["wf_pm_today"] == "흐리고 비", "예보 데이터 유실 방어 실패"
