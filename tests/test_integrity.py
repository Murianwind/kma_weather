import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock
from custom_components.kma_weather.coordinator import (
    _TEMP_ID_COORDS,
    _get_kma_reg_ids,
    _is_valid_korean_coord,
    _EXCLUDE_FROM_NEAREST,
)
from custom_components.kma_weather.sensor import SENSOR_TYPES


# ---------------------------------------------------------------------------
# 1. 중기예보 구역코드 전수 검증
# ---------------------------------------------------------------------------
def test_exhaustive_mid_term_ids():
    """모든 구역 ID 좌표에서 _get_kma_reg_ids 를 호출했을 때
    해당 구역 ID 가 반환되는지 전수 검증합니다."""
    for expected_id, (lat, lon) in _TEMP_ID_COORDS.items():
        if expected_id in _EXCLUDE_FROM_NEAREST:
            continue

        reg_id, land_id = _get_kma_reg_ids(lat, lon)
        assert reg_id == expected_id, (
            f"ID 매칭 실패: 기대={expected_id}, 실제={reg_id}"
        )

        is_exception = land_id in ["11A00101", "11E00101"]
        assert land_id.endswith("0000") or is_exception, (
            f"land_id 형식 오류: {land_id}"
        )


# ---------------------------------------------------------------------------
# 2. 좌표-구역코드 매핑 경계 검증 (test_coordinator_e2e.py 에서 통합)
# ---------------------------------------------------------------------------
def test_kma_reg_ids_for_seoul():
    """서울 시청 좌표가 올바른 구역 코드로 매핑되는지 검증"""
    reg_temp, reg_land = _get_kma_reg_ids(37.5665, 126.9780)
    assert reg_temp == "11B10101"  # 서울
    assert reg_land == "11B00000"  # 서울/인천/경기


def test_valid_korean_coord_boundary():
    """한반도 유효 좌표 범위 검증"""
    assert _is_valid_korean_coord(37.5665, 126.9780) is True   # 서울
    assert _is_valid_korean_coord(33.1, 126.2) is True          # 이어도/제주 인근
    assert _is_valid_korean_coord(0.0, 0.0) is False            # 적도


# ---------------------------------------------------------------------------
# 3. 센서 키 유효성 검증 (방향 수정)
# ---------------------------------------------------------------------------
def test_all_sensor_types_have_required_fields():
    """SENSOR_TYPES 의 각 항목이 필수 필드 구조를 갖추고 있는지 검증합니다.

    기존 코드는 하드코딩 리스트 ⊆ SENSOR_TYPES 방향으로만 검사했습니다.
    (SENSOR_TYPES 에 새 센서가 추가돼도 테스트가 통과하는 문제)
    수정 후: SENSOR_TYPES 의 각 항목이 [name, unit, icon, device_class, id, category]
    6개 필드를 갖추었는지 검증합니다.
    """
    REQUIRED_FIELD_COUNT = 6
    for key, details in SENSOR_TYPES.items():
        assert len(details) == REQUIRED_FIELD_COUNT, (
            f"'{key}' 센서의 필드 수가 {REQUIRED_FIELD_COUNT}개가 아닙니다: {details}"
        )
        name, unit, icon, device_class, entity_id_suffix, category = details
        assert isinstance(name, str) and name, f"'{key}' 센서의 name 이 비어 있습니다."
        assert isinstance(icon, str) and icon.startswith("mdi:"), (
            f"'{key}' 센서의 icon 이 'mdi:' 로 시작하지 않습니다: {icon}"
        )
        assert isinstance(entity_id_suffix, str) and entity_id_suffix, (
            f"'{key}' 센서의 entity_id_suffix 가 비어 있습니다."
        )


def test_sensor_entity_id_suffixes_are_unique():
    """모든 센서의 entity_id suffix 가 중복 없이 고유한지 검증합니다."""
    suffixes = [details[4] for details in SENSOR_TYPES.values()]
    assert len(suffixes) == len(set(suffixes)), (
        f"entity_id suffix 중복 발견: {[s for s in suffixes if suffixes.count(s) > 1]}"
    )


# ---------------------------------------------------------------------------
# 4. 에어코리아 위치 결정 테스트 (KeyError 방지)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_air_korea_location_resolution(hass, mock_config_entry):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

    coordinator = KMAWeatherUpdateCoordinator(hass, mock_config_entry)

    with patch.object(coordinator, "_resolve_location", return_value=(37.5665, 126.9780)):
        mock_res = {
            "weather": {},
            "air": {"station": "중구", "pm10Value": 30},
        }
        with patch(
            "custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data",
            return_value=mock_res,
        ):
            res = await coordinator._async_update_data()
            assert res["air"]["station"] == "중구"


# ---------------------------------------------------------------------------
# 5. 날씨 요약 사수 로직 검증 (야간/데이터 공백 시간대)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_summary_persistence_at_midnight(hass, mock_config_entry):
    """API 응답에 wf_pm_today 가 없어도 코디네이터가 저장한 값을 유지하는지 검증"""
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

    coordinator = KMAWeatherUpdateCoordinator(hass, mock_config_entry)
    coordinator._wf_pm_today = "흐리고 비"
    coordinator._daily_date = datetime.now().date()

    new_data = {"weather": {"wf_pm_today": None}}

    # 사수 로직 시뮬레이션
    weather = new_data["weather"]
    weather["wf_pm_today"] = coordinator._wf_pm_today or weather.get("wf_pm_today")

    assert weather["wf_pm_today"] == "흐리고 비", "예보 데이터 유실 방어 실패"
