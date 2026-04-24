"""
통합 시나리오 테스트.
센서 출력값은 API 원본 자릿수 그대로 검증한다.
  - 기상청 정수 원본 → int 출력
  - 기상청 소수점 원본 → float 출력 (변환 없음)
"""
import pytest
from unittest.mock import patch, AsyncMock
from custom_components.kma_weather.const import DOMAIN

# conftest 로드 (경로 호환성 유지)
try:
    from tests.conftest import MOCK_SCENARIOS
except ImportError:
    from conftest import MOCK_SCENARIOS

# ─────────────────────────────────────────────────────────────────────────────
# [Given] 등급 계산 헬퍼 함수 (원본 로직 100% 유지)
# ─────────────────────────────────────────────────────────────────────────────
def calculate_pm10_grade(value) -> str:
    try:
        val = int(float(value))
        if val <= 50: return "좋음"
        elif val <= 100: return "보통"
        elif val <= 150: return "나쁨"
        return "매우나쁨"
    except (ValueError, TypeError):
        return "정보없음"

def calculate_pm25_grade(value) -> str:
    try:
        val = int(float(value))
        if val <= 15: return "좋음"
        elif val <= 35: return "보통"
        elif val <= 75: return "나쁨"
        return "매우나쁨"
    except (ValueError, TypeError):
        return "정보없음"

# ─────────────────────────────────────────────────────────────────────────────
# 통합 시나리오 테스트 메인
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_kma_full_scenarios(hass, mock_config_entry, kma_api_mock_factory, freezer):
    """시나리오: 다양한 기상 상황(이동, 누락, 가비지 데이터)에서도 센서가 무결성을 유지하며 동작함"""

    # 1. [Given] 초기 환경 설정 및 통합 구성요소 로드
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98

    kma_api_mock_factory("full_test")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    p = "test"
    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]

    # 2. [Scenario 1] 개별 센서 값 검증 (API 원본 자릿수 사수)
    # [When] 현재 날씨 데이터를 수신했을 때
    # [Then] TMP=22 (정수) → "22" 출력 확인
    state = hass.states.get(f"sensor.{p}_temperature")
    assert state is not None, "온도 센서가 없음"
    assert state.state == "22", f"온도 기대='22', 실제='{state.state}'"
    assert state.attributes.get("unit_of_measurement") == "°C"
    assert state.attributes.get("device_class") == "temperature"

    # [Then] REH=45 (정수) → "45" 출력 확인
    state_reh = hass.states.get(f"sensor.{p}_humidity")
    assert state_reh is not None, "습도 센서가 없음"
    assert state_reh.state == "45", f"습도 기대='45', 실제='{state_reh.state}'"

    # [Then] WSD=2.1 (소수점 1자리) → "2.1" 출력 확인
    state_wsd = hass.states.get(f"sensor.{p}_wind_speed")
    assert state_wsd is not None, "풍속 센서가 없음"
    assert state_wsd.state == "2.1", f"풍속 기대='2.1', 실제='{state_wsd.state}'"

    # [Then] POP=10 (정수) → "10" 출력 확인
    assert hass.states.get(f"sensor.{p}_precipitation_prob").state == "10"

    # [Then] apparent_temp=23.4 (소수점 1자리) → "23.4" 출력 확인
    state_apparent = hass.states.get(f"sensor.{p}_apparent_temperature")
    assert state_apparent is not None, "체감온도 센서가 없음"
    assert state_apparent.state == "23.4", f"체감온도 기대='23.4', 실제='{state_apparent.state}'"

    # 3. [Scenario 2] 예보 10일치 검증
    response = await hass.services.async_call(
        "weather", "get_forecasts",
        {"type": "twice_daily"},
        target={"entity_id": f"weather.{p}_weather"},
        blocking=True, return_response=True,
    )
    forecast = response[f"weather.{p}_weather"]["forecast"]
    assert len(forecast) >= 10
    f0 = forecast[0]
    assert "temperature" in f0
    assert f0["temperature"] is not None
    if "templow" in f0:
        assert f0["templow"] is not None
    assert f0["condition"] is not None

    # 4. [Scenario 3] 미세먼지 센서 및 등급 검증
    pm10 = hass.states.get(f"sensor.{p}_pm10")
    assert pm10 is not None, "PM10 센서가 없음"
    assert pm10.state == "35"
    assert pm10.attributes.get("unit_of_measurement") == "µg/m³"
    assert pm10.attributes.get("icon") == "mdi:blur"

    pm25 = hass.states.get(f"sensor.{p}_pm25")
    assert pm25 is not None
    assert pm25.state == "15"

    assert hass.states.get(f"sensor.{p}_pm10_grade").state == calculate_pm10_grade(pm10.state)
    assert hass.states.get(f"sensor.{p}_pm25_grade").state == calculate_pm25_grade(pm25.state)

    # 5. [Scenario 4] 위치 진단 센서 속성 검증
    loc_state = hass.states.get(f"sensor.{p}_location")
    assert loc_state is not None
    expected_station = MOCK_SCENARIOS["full_test"].get("air", {}).get(
        "stationName", loc_state.attributes.get("air_korea_station")
    )
    assert loc_state.attributes.get("air_korea_station") == expected_station

    # 6. [Scenario 5 & 6] 현재 위치 출력 및 이동 시 갱신 검증
    assert hass.states.get(f"sensor.{p}_location").state == "경기도 화성시"

    with patch(
        "custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data",
        new_callable=AsyncMock,
    ) as mock_fetch:
        busan_data = MOCK_SCENARIOS["full_test"].copy()
        busan_data["weather"] = busan_data["weather"].copy()
        busan_data["weather"]["address"] = "부산광역시"
        busan_data["weather"]["현재 위치"] = "부산광역시"
        mock_fetch.return_value = busan_data

        hass.states.async_set(
            "device_tracker.my_phone", "home",
            {"latitude": 35.1, "longitude": 129.0},
        )
        await hass.async_block_till_done()
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        assert hass.states.get(f"sensor.{p}_location").state == "부산광역시"

    # 7. [Scenario 7 & 8] 데이터 누락 및 복원 검증
    kma_api_mock_factory("jeju_missing")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(f"sensor.{p}_temperature").state == "unknown"

    kma_api_mock_factory("full_test")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(f"sensor.{p}_temperature").state == "22"

    # 8. [Scenario 10] 오염된 데이터("-") 주입 시 안정성 검사
    from custom_components.kma_weather.sensor import SENSOR_TYPES
    polluted_data = {
        "weather": {key: "-" for key in SENSOR_TYPES},
        "air": {key: "-" for key in SENSOR_TYPES},
    }

    with patch(
        "custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data",
        new_callable=AsyncMock,
    ) as mock_polluted:
        mock_polluted.return_value = polluted_data
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        # [Then] 센서가 에러(unavailable) 없이 안전하게 처리되는지 전수 검사
        # 아래 센서는 unknown 검사 제외:
        #  - _REALTIME_KEYS: "-" 주입 시 이전 캐시로 복원
        #  - 천문/달 센서: API와 무관하게 좌표 계산으로 항상 값 존재
        #  - pollen: "-" 주입 시에도 비시즌 fallback("좋음")으로 동작 (설계 의도)
        _SKIP_UNKNOWN_CHECK = {
            "TMP", "REH", "WSD", "VEC_KOR", "POP", "apparent_temp",  # _REALTIME_KEYS
            "dawn", "sunrise", "sunset", "dusk",                       # 태양 시각
            "astro_dawn", "astro_dusk",                                # 천문 박명
            "moon_phase", "moon_illumination", "moonrise", "moonset",  # 달
            "observation_condition",                                    # 관측 조건
            "pollen",          # 비시즌/미신청 시 "좋음" fallback → unknown이 아님(의도된 동작)
            "api_calls_today", # 초기값 0 → unknown이 아님(coordinator 생성 즉시 카운터 존재)
        }
        for sensor_type, details in SENSOR_TYPES.items():
            if sensor_type in ["last_updated", "api_expire"]:
                continue
            entity_id = f"sensor.{p}_{details[4]}"
            state = hass.states.get(entity_id)
            if state:
                assert state.state != "unavailable", f"센서 {entity_id}가 unavailable 상태"
                if sensor_type not in _SKIP_UNKNOWN_CHECK:
                    assert state.state == "unknown", f"센서 {entity_id}가 {state.state} (unknown 기대)"

        # [Then] pollen 센서는 "-" 주입 시에도 "좋음"을 유지해야 함 (fallback 검증)
        pollen_state = hass.states.get(f"sensor.{p}_pollen")
        assert pollen_state is not None, "pollen 센서가 없음"
        assert pollen_state.state == "좋음", \
            f"'-' 주입 시 pollen 좋음 fallback 기대, 실제='{pollen_state.state}'"

    # 9. [Scenario 11] 가비지 데이터 주입 시 강건성 검증
    garbage_data = {
        "weather": {key: "BAD_DATA" for key in SENSOR_TYPES},
        "air": {key: "BAD_DATA" for key in SENSOR_TYPES},
    }

    with patch(
        "custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data",
        new_callable=AsyncMock,
    ) as mock_garbage:
        mock_garbage.return_value = garbage_data
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        _ASTRO_KEYS = {
            "dawn", "sunrise", "sunset", "dusk",
            "astro_dawn", "astro_dusk",
            "moon_phase", "moon_illumination", "moonrise", "moonset",
            "observation_condition",
        }
        for sensor_type, details in SENSOR_TYPES.items():
            if sensor_type in ["last_updated", "api_expire"]:
                continue
            if sensor_type in _ASTRO_KEYS:
                continue
            if sensor_type == "pollen":
                # 가비지 데이터에도 pollen은 좋음 fallback
                pollen_s = hass.states.get(f"sensor.{p}_{details[4]}")
                if pollen_s:
                    assert pollen_s.state == "좋음"
                continue
            if sensor_type == "api_calls_today":
                # coordinator 내부 카운터를 직접 읽으므로 가비지 데이터와 무관하게 항상 숫자 반환
                api_s = hass.states.get(f"sensor.{p}_{details[4]}")
                if api_s:
                    assert api_s.state.isdigit() or int(api_s.state) >= 0
                continue
            entity_id = f"sensor.{p}_{details[4]}"
            state = hass.states.get(entity_id)
            if state:
                if details[1] is not None:
                    assert state.state == "unknown"
                assert state.state != "unavailable"

    # 10. [Teardown] 통합 구성요소 언로드
    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()


# ══════════════════════════════════════════════════════════════════════════════
# API 호출 카운터 센서 테스트
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_api_calls_today_sensor_exists_and_zero(hass, mock_config_entry, kma_api_mock_factory):
    """
    [Given] full_test mock으로 통합 구성요소 설치
    [When] sensor.test_api_calls_today 상태를 조회
    [Then] 센서가 존재하고 초기값이 0이어야 함 (테스트 환경에서 실제 API 미호출)
    """
    kma_api_mock_factory("full_test")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.test_api_calls_today")
    assert state is not None, "api_calls_today 센서가 없음"
    assert state.state == "0", f"초기 호출 수 0 기대, 실제={state.state}"
    assert state.attributes.get("단기예보") == 0
    assert state.attributes.get("중기예보") == 0
    assert state.attributes.get("에어코리아_측정소") == 0
    assert state.attributes.get("에어코리아_대기") == 0
    assert state.attributes.get("기상특보") == 0
    assert state.attributes.get("꽃가루") == 0


@pytest.mark.asyncio
async def test_api_calls_today_attributes_present(hass, mock_config_entry, kma_api_mock_factory):
    """
    [Given] full_test mock으로 통합 구성요소 설치
    [When] api_calls_today 센서의 속성을 조회
    [Then] 모든 API 키 속성과 집계일 속성이 존재해야 함
    """
    kma_api_mock_factory("full_test")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.test_api_calls_today")
    assert state is not None
    attrs = state.attributes
    for key in ["단기예보", "중기예보", "에어코리아_측정소", "에어코리아_대기", "기상특보", "꽃가루"]:
        assert key in attrs, f"속성 '{key}' 없음"
    # 집계일: 초기값 "-" (아직 카운터 증가 없음)
    assert "집계일" in attrs, "집계일 속성 없음"
