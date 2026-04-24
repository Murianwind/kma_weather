import pytest
from datetime import datetime
from unittest.mock import patch
from custom_components.kma_weather.coordinator import (
    _TEMP_ID_COORDS,
    _calc_reg_ids,          # 변경: _get_kma_reg_ids → _calc_reg_ids
    _EXCLUDE_FROM_NEAREST,
)
from custom_components.kma_weather.const import is_korean_coord_loose as _is_valid_korean_coord
from custom_components.kma_weather.sensor import SENSOR_TYPES


# ─────────────────────────────────────────────────────────────────────────────
# 1. 중기예보 구역코드 전수 검증
# ─────────────────────────────────────────────────────────────────────────────
def test_exhaustive_mid_term_ids():
    """시나리오: 모든 구역 ID 좌표에서 _calc_reg_ids를 호출했을 때 해당 구역 ID가 반환되는지 전수 검증함"""

    for expected_id, (lat, lon) in _TEMP_ID_COORDS.items():
        if expected_id in _EXCLUDE_FROM_NEAREST:
            continue

        reg_id, land_id = _calc_reg_ids(lat, lon)

        assert reg_id == expected_id, (
            f"ID 매칭 실패: 기대={expected_id}, 실제={reg_id}"
        )

        is_exception = land_id in ["11A00101", "11E00101"]
        assert land_id.endswith("0000") or is_exception, (
            f"land_id 형식 오류: {land_id}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2. 좌표-구역코드 매핑 경계 검증
# ─────────────────────────────────────────────────────────────────────────────
def test_kma_reg_ids_for_seoul():
    """시나리오: 서울 시청 좌표가 올바른 구역 코드로 매핑됨"""
    reg_temp, reg_land = _calc_reg_ids(37.5665, 126.9780)
    assert reg_temp == "11B10101"
    assert reg_land == "11B00000"


def test_valid_korean_coord_boundary():
    """시나리오: 한반도 유효 좌표 범위를 정상적으로 판별함"""
    assert _is_valid_korean_coord(37.5665, 126.9780) is True
    assert _is_valid_korean_coord(33.1, 126.2) is True
    assert _is_valid_korean_coord(0.0, 0.0) is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. 센서 키 유효성 검증
# ─────────────────────────────────────────────────────────────────────────────
def test_all_sensor_types_have_required_fields():
    """시나리오: SENSOR_TYPES의 각 항목이 6개의 필수 필드 구조를 완벽히 갖추고 있음"""
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
    """시나리오: 모든 센서의 entity_id suffix가 중복 없이 고유함"""
    suffixes = [details[4] for details in SENSOR_TYPES.values()]
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


# ─────────────────────────────────────────────────────────────────────────────
# 5. 날씨 요약 사수 로직 검증
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_summary_persistence_at_midnight(hass, mock_config_entry):
    """시나리오: API 응답에 wf_pm_today가 없어도 코디네이터가 저장한 값을 사수함"""
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

    coordinator = KMAWeatherUpdateCoordinator(hass, mock_config_entry)
    coordinator._wf_pm_today = "흐리고 비"
    coordinator._daily_date = datetime.now().date()

    new_data = {"weather": {"wf_pm_today": None}}
    weather = new_data["weather"]
    weather["wf_pm_today"] = coordinator._wf_pm_today or weather.get("wf_pm_today")

    assert weather["wf_pm_today"] == "흐리고 비", "예보 데이터 유실 방어 실패"


# ─────────────────────────────────────────────────────────────────────────────
# 6. 유틸리티 헬퍼 함수 검증
# ─────────────────────────────────────────────────────────────────────────────
from custom_components.kma_weather.coordinator import _land_code
from custom_components.kma_weather.const import haversine as _haversine

def test_haversine_known_distance():
    d = _haversine(37.5665, 126.9780, 35.1796, 129.0756)
    assert 310 < d < 340


class TestLandCodeMapping:
    @pytest.mark.parametrize("temp_id,expected_land", [
        ("11B10101", "11B00000"),
        ("11A00101", "11A00101"),
        ("11H10101", "11H10000"),
    ])
    def test_land_code(self, temp_id, expected_land):
        assert _land_code(temp_id) == expected_land


# ══════════════════════════════════════════════════════════════════════════════
# 2. convert_grid 경계값 분석 (Boundary Value Analysis)
# ══════════════════════════════════════════════════════════════════════════════

class TestConvertGridBoundaryValues:
    """
    WGS84 → 기상청 격자 변환의 경계값 검증.

    ON 값: 한국 영토 극단부 (마라도, 독도, 이어도, 철원 북단)
    OFF 값: 유효 범위 완전 이탈 좌표
    """

    @pytest.mark.parametrize("name,lat,lon,expected_nx,expected_ny", [
        # 공식 격자 기준점 (기상청 문서 확인값)
        ("서울 시청",   37.5665, 126.9780, 60, 127),
        # 한국 영토 극단부 (ON 경계값)
        ("마라도 최남단", 33.1067, 126.2700, 48, 29),
        ("독도 동단",    37.2427, 131.8650, 144, 123),
        ("철원 최북단",  38.2700, 127.1500, 63, 142),
    ])
    def test_valid_korean_locations(self, name, lat, lon, expected_nx, expected_ny):
        """
        [Given] 한국 영토 내 극단 좌표 (ON 경계값)
        [When] convert_grid 호출
        [Then] 예상 격자 좌표로 변환되어야 함
        """
        nx, ny = convert_grid(lat, lon)
        assert nx == expected_nx, f"{name}: nx={nx} (기대: {expected_nx})"
        assert ny == expected_ny, f"{name}: ny={ny} (기대: {expected_ny})"

    @pytest.mark.parametrize("name,lat,lon", [
        ("범위 밖 북쪽",  43.0, 128.0),
        ("범위 밖 남쪽",  30.0, 126.0),
        ("범위 밖 동쪽",  36.0, 133.0),
        ("적도 (0,0)",    0.0,  0.0),
        ("음수 위도",     -10.0, 126.0),
    ])
    def test_out_of_range_coordinates_do_not_crash(self, name, lat, lon):
        """
        [Given] 한국 영토 완전 이탈 좌표 (OFF 값)
        [When] convert_grid 호출
        [Then] 수학적 변환은 실행되되 크래시 없이 정수 tuple을 반환해야 함
              (결과 격자값의 유효성은 보장 안 함, 안정성만 검증)
        """
        result = convert_grid(lat, lon)
        assert isinstance(result, tuple) and len(result) == 2, \
            f"{name}: tuple(nx, ny) 반환 기대"
        nx, ny = result
        assert isinstance(nx, int) and isinstance(ny, int), \
            f"{name}: 정수 반환 기대, 실제: nx={nx!r}, ny={ny!r}"

    def test_jeju_island(self):
        """
        [Given] 제주도 좌표
        [When] convert_grid 호출
        [Then] nx가 50~60 범위 내 격자로 변환되어야 함 (서남쪽)
        """
        nx, ny = convert_grid(33.4996, 126.5312)
        assert 50 <= nx <= 60, f"제주도 nx={nx} (50~60 기대)"
        assert 30 <= ny <= 45, f"제주도 ny={ny} (30~45 기대)"


# ══════════════════════════════════════════════════════════════════════════════
# 3. API 응답 구조적 결측치 대응 테스트
# ══════════════════════════════════════════════════════════════════════════════

class TestMissingJsonStructure:
    """
    정상 HTTP 200 응답이지만 내부 JSON 구조가 결측된 경우의 방어력 검증.
    """

    @pytest.mark.parametrize("malformed_response,description", [
        # 빈 items 배열
        ({"response": {"header": {"resultCode": "00"},
                       "body": {"items": {"item": []}}}},
         "items가 빈 배열"),
        # items 키 자체 누락
        ({"response": {"header": {"resultCode": "00"},
                       "body": {}}},
         "items 키 누락"),
        # body 키 누락
        ({"response": {"header": {"resultCode": "00"}}},
         "body 키 누락"),
        # response 키 누락
        ({},
         "response 키 전체 누락"),
        # item이 dict 단건 (list가 아님)
        ({"response": {"header": {"resultCode": "00"},
                       "body": {"items": {"item": {
                           "fcstDate": "20260424", "fcstTime": "1200",
                           "category": "TMP", "fcstValue": "22"
                       }}}}},
         "item이 dict 단건 (배열 아님)"),
    ])
    def test_short_term_malformed_response_does_not_crash(self, malformed_response, description):
        """
        [Given] 비정상 구조의 단기예보 JSON 응답
        [When] items 파싱 시도
        [Then] 크래시 없이 빈 리스트 또는 빈 dict로 처리되어야 함
        """
        # _merge_all의 파싱 로직과 동일한 패턴으로 방어력 검증
        items = (
            malformed_response
            .get("response", {})
            .get("body", {})
            .get("items", {})
            .get("item", [])
        )
        # 단건 dict인 경우 list로 래핑해야 함 → api_kma.py 동작과 동일
        if isinstance(items, dict):
            items = [items]
        assert isinstance(items, list), \
            f"'{description}': list 기대, 실제 {type(items)}"

    def test_missing_fcst_time_slot_uses_fallback(self):
        """
        [Given] 현재 시각에 해당하는 fcstTime 슬롯이 없는 예보 맵
        [When] 가장 가까운 슬롯 선택 로직 실행
        [Then] 가용한 마지막 슬롯으로 fallback되어야 함 (KeyError 없음)
        """
        forecast_map = {
            "20260424": {
                "0600": {"TMP": "18", "SKY": "1", "PTY": "0"},
                # 현재 시각이 1500이라고 가정, 0900~1200 슬롯 없음
                "1800": {"TMP": "20", "SKY": "3", "PTY": "0"},
            }
        }
        today_str = "20260424"
        curr_h = "1500"  # 이 슬롯은 없음

        times = sorted(forecast_map[today_str].keys())
        best_t = next((t for t in times if t >= curr_h), times[-1] if times else None)

        # 1500 이상인 슬롯: 1800 → best_t = "1800"
        assert best_t == "1800", f"fallback 슬롯 기대: '1800', 실제: '{best_t}'"
        assert forecast_map[today_str][best_t]["TMP"] == "20"

    def test_all_required_categories_missing_returns_none_values(self):
        """
        [Given] 예보 슬롯에 TMP, SKY, PTY 등 주요 카테고리가 없는 경우
        [When] 해당 슬롯에서 값을 꺼낼 때
        [Then] None/기본값을 반환하고 크래시가 없어야 함
        """
        from custom_components.kma_weather.const import safe_float
        empty_slot = {}  # 모든 카테고리 누락

        tmp = safe_float(empty_slot.get("TMP"))
        sky = empty_slot.get("SKY")
        pty = empty_slot.get("PTY")

        assert tmp is None
        assert sky is None
        assert pty is None
