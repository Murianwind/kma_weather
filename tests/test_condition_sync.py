import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kma_weather.api_kma import KMAWeatherAPI, KOR_TO_CONDITION
from custom_components.kma_weather.const import DOMAIN

# ─────────────────────────────────────────────────────────────────────────────
# [Given] 공통 상수 및 헬퍼 함수
# ─────────────────────────────────────────────────────────────────────────────
ALL_CONDITIONS = list(KOR_TO_CONDITION.items())
TZ = ZoneInfo("Asia/Seoul")

def _build_coordinator_data(kor: str, eng: str) -> dict:
    """통합 테스트용 coordinator.data 구조 생성"""
    now = dt_util.now()
    return {
        "weather": {
            "TMP": 22.0, "REH": 50, "WSD": 2.0, "VEC_KOR": "남동",
            "current_condition_kor": kor,
            "current_condition": eng,
            "forecast_twice_daily": [
                {"datetime": now.isoformat(), "is_daytime": True, "condition": eng, "native_temperature": 25, "_day_index": 0}
            ],
            "forecast_daily": [
                {"datetime": now.isoformat(), "condition": eng, "native_temperature": 25, "_day_index": 0}
            ],
            "last_updated": dt_util.utcnow(),
        },
        "air": {},
        # ── 꽃가루 키 추가 ─────────────────────────────────────────────────
        # pollen 센서는 API 승인 여부와 무관하게 항상 등록된다(SENSOR_API_GROUPS[None]).
        # coordinator.data에 "pollen" 키가 없어도 sensor.py가 fallback("좋음")을 반환하므로
        # 테스트 자체가 실패하지는 않지만, extra_state_attributes 호출 시
        # dict.get()을 사용하므로 안전하다. 명시적으로 포함시켜 실제 동작과 일치시킨다.
        "pollen": {
            "oak": "좋음",
            "pine": "좋음",
            "grass": "좋음",
            "worst": "좋음",
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. 매핑 테이블/단위 테스트 (오류 수정: snowy-rainy 허용)
# ─────────────────────────────────────────────────────────────────────────────

class TestKorToConditionMapping:
    def test_mapping_integrity(self):
        # [Given] 기상청 한글-영문 매핑 테이블이 정의되어 있을 때

        # [Fix] snowy-rainy(진눈깨비)는 파트너님이 정의한 유효한 커스텀 상태이므로 허용 목록에 추가
        valid_ha_conditions = {
            "sunny", "partlycloudy", "cloudy", "rainy", "snowy",
            "lightning", "lightning-rainy", "fog", "windy", "hail",
            "exceptional", "clear-night", "pouring", "snowy-rainy"
        }

        # [Then] 모든 매핑 결과가 허용된 상태값 안에 포함되어야 함
        for kor, eng in KOR_TO_CONDITION.items():
            assert eng in valid_ha_conditions, f"'{eng}'는 유효한 HA 상태값이 아닙니다 (한글 키: {kor})"

        # [Then] 필수 기상 키들이 누락 없이 존재해야 함
        required = {"맑음", "구름많음", "흐림", "비", "비/눈", "소나기", "눈"}
        assert required.issubset(set(KOR_TO_CONDITION.keys()))

class TestKorToConditionMethod:
    @pytest.mark.parametrize("kor,expected_eng", ALL_CONDITIONS)
    def test_known_values(self, kor, expected_eng):
        # [Given/When] 클래스 메서드를 통해 한글 상태를 영문으로 변환하면
        # [Then] 매핑 테이블과 일치하는 결과가 반환되어야 함
        assert KMAWeatherAPI.kor_to_condition(kor) == expected_eng

# ─────────────────────────────────────────────────────────────────────────────
# 2. 코디네이터 업데이트 동기화 (구조 정렬)
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordinatorConditionSync:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("hour,wf_am,wf_pm,expected_kor", [
        (9,  "맑음", "구름많음", "맑음"),
        (15, "맑음", "구름많음", "구름많음"),
        (20, "구름많음", "흐림", "흐림"),
        (3,  "비", "맑음", "비"),
    ], ids=["오전9시", "오후15시", "저녁20시", "새벽3시"])
    async def test_coordinator_sync_logic(self, hass, hour, wf_am, wf_pm, expected_kor):
        # [Given] 코디네이터를 생성하고 시뮬레이션할 예보 데이터를 설정
        from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
        hass.config.latitude, hass.config.longitude = 37.56, 126.98
        entry = MagicMock(data={"api_key": "test", "prefix": "sync"}, options={}, entry_id="coord_test")

        coord = KMAWeatherUpdateCoordinator(hass, entry)
        coord.api.tz = TZ

        # API 응답 결과 Mocking (실제 _merge_all 결과 딕셔너리와 구조 일치)
        coord.api.fetch_data = AsyncMock(return_value={
            "weather": {
                "wf_am_today": wf_am, "wf_pm_today": wf_pm,
                "current_condition_kor": wf_am, # 초기값
                "forecast_twice_daily": [], "forecast_daily": []
            },
            "air": {}, "raw_forecast": {"20250601": {"0800": {"TMP": "20"}}}
        })

        # [When] 특정 시각(hour)을 패치하여 코디네이터 데이터를 업데이트하면
        fake_now = datetime(2025, 6, 1, hour, 0, tzinfo=TZ)
        with patch("custom_components.kma_weather.coordinator.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = await coord._async_update_data()

        # [Then] 시각에 맞는 오전/오후 날씨가 현재 날씨(current_condition_kor)로 선택되어야 함
        assert result is not None
        assert result["weather"]["current_condition_kor"] == expected_kor
        assert result["weather"]["current_condition"] == KOR_TO_CONDITION[expected_kor]

# ─────────────────────────────────────────────────────────────────────────────
# 3. 통합 엔티티 동기화 (동작 사수)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("kor,eng", [("맑음", "sunny"), ("비", "rainy")])
async def test_integration_sync(hass, kor, eng):
    # [Given] 센서와 날씨 엔티티가 동기화될 상태 데이터 주입
    entry = MockConfigEntry(domain=DOMAIN, data={"prefix": "sync", "api_key": "test"}, entry_id=f"sync_{kor}")

    # coordinator.data를 반환할 mock 데이터
    mock_data = _build_coordinator_data(kor, eng)

    # ── 핵심 수정: _async_update_data 패치와 함께 _approved_apis도 주입 ──
    # _async_update_data를 mock으로 대체하면 실제 API 호출이 없으므로
    # api._mark_approved()가 호출되지 않는다.
    # 그 결과 sensor.py의 _eligible_sensor_types()가 short API 미승인으로 판단하여
    # current_condition_kor 등 short 의존 센서를 등록하지 않아 테스트가 실패한다.
    # KMAWeatherUpdateCoordinator.__init__ 를 감싸서 초기화 직후 _approved_apis를 주입한다.
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

    _real_init = KMAWeatherUpdateCoordinator.__init__

    def _patched_init(self_coord, hass_arg, entry_arg):
        _real_init(self_coord, hass_arg, entry_arg)
        self_coord.api._approved_apis = {"short", "mid", "air", "warning", "pollen"}

    with patch.object(KMAWeatherUpdateCoordinator, "__init__", _patched_init), \
         patch(
             "custom_components.kma_weather.coordinator.KMAWeatherUpdateCoordinator._async_update_data",
             new_callable=AsyncMock,
             return_value=mock_data,
         ):
        # [When] 엔티티를 생성하고 홈어시스턴트에 등록하면
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # [Then] 센서 상태(한글)가 코디네이터 데이터와 일치해야 함
        sensor_state = hass.states.get("sensor.sync_condition")
        assert sensor_state is not None, "센서 엔티티가 생성되지 않았습니다."
        assert sensor_state.state == kor

        # [Then] 날씨 엔티티의 예보(영문)가 코디네이터 데이터와 일치해야 함
        response = await hass.services.async_call(
            "weather", "get_forecasts", {"type": "twice_daily"},
            target={"entity_id": "weather.sync_weather"},
            blocking=True, return_response=True
        )
        assert response["weather.sync_weather"]["forecast"][0]["condition"] == eng


# ══════════════════════════════════════════════════════════════════════════════
# 1. SKY × PTY 조합 테스트 (Combinatorial Testing)
# ══════════════════════════════════════════════════════════════════════════════

class TestSkyPtyCombinations:
    """
    기상청 단기예보 SKY(하늘 상태)와 PTY(강수 형태) 조합에 대한 완전한 매핑 검증.

    SKY 유효값: "1"=맑음, "3"=구름많음, "4"=흐림
    PTY 유효값: "0"=없음, "1"=비, "2"=비/눈, "3"=눈, "4"=소나기,
                "5"=빗방울, "6"=빗방울/눈날림, "7"=눈날림
    """

    @pytest.mark.parametrize("sky,pty,expected", [
        # ── PTY=0(강수없음): SKY 값에 따라 분기 ──────────────────────────────
        ("1", "0", "맑음"),
        ("3", "0", "구름많음"),
        ("4", "0", "흐림"),
        # SKY가 맑음(1)이어도 PTY가 있으면 PTY 우선
        ("1", "1", "비"),
        ("1", "2", "비/눈"),
        ("1", "3", "눈"),
        ("1", "4", "소나기"),
        ("1", "5", "빗방울"),
        ("1", "6", "빗방울/눈날림"),
        ("1", "7", "눈날림"),
        # PTY가 있을 때 SKY 값은 결과에 영향 없음
        ("4", "1", "비"),
        ("3", "3", "눈"),
        ("4", "4", "소나기"),
    ])
    def test_valid_combinations(self, sky, pty, expected):
        """
        [Given] 유효한 SKY, PTY 값 조합
        [When] _get_sky_kor 호출
        [Then] 명세에 정의된 한국어 날씨 상태를 반환해야 함
        """
        api = KMAWeatherAPI(None, "key")
        assert api._get_sky_kor(sky, pty) == expected, \
            f"SKY={sky}, PTY={pty} → '{api._get_sky_kor(sky, pty)}' (기대: '{expected}')"

    @pytest.mark.parametrize("sky,pty", [
        ("1", "9"),    # 명세에 없는 PTY 값
        ("1", "99"),   # 완전히 벗어난 PTY 값
        ("2", "0"),    # 명세에 없는 SKY 값 (2는 없음)
        ("9", "0"),    # 명세에 없는 SKY 값
        ("0", "0"),    # SKY 최솟값 이하
        (None, None),  # None 입력
        ("", ""),      # 빈 문자열 입력
        ("abc", "xyz"),# 비숫자 입력
    ])
    def test_out_of_spec_values_do_not_crash(self, sky, pty):
        """
        [Given] 명세에 없는 SKY 또는 PTY 값 (OUT 값)
        [When] _get_sky_kor 호출
        [Then] 크래시 없이 기본값(맑음 또는 흐림) 중 하나를 반환해야 함
        """
        api = KMAWeatherAPI(None, "key")
        result = api._get_sky_kor(sky, pty)
        assert isinstance(result, str), \
            f"SKY={sky!r}, PTY={pty!r} → 문자열 반환 기대, 실제: {result!r}"
        assert result in ["맑음", "구름많음", "흐림", "비", "비/눈", "눈",
                          "소나기", "빗방울", "빗방울/눈날림", "눈날림"], \
            f"알 수 없는 반환값: {result!r}"

    def test_pty_takes_priority_over_sky(self):
        """
        [Given] SKY='1'(맑음), PTY='1'(비) — 논리적으로 모순된 조합
        [When] _get_sky_kor 호출
        [Then] PTY가 SKY보다 우선하여 '비'를 반환해야 함
        """
        api = KMAWeatherAPI(None, "key")
        assert api._get_sky_kor("1", "1") == "비"

    @pytest.mark.parametrize("kor,expected_eng", [
        ("맑음",         "sunny"),
        ("구름많음",      "partlycloudy"),
        ("흐림",         "cloudy"),
        ("비",           "rainy"),
        ("비/눈",        "snowy-rainy"),
        ("눈",           "snowy"),
        ("소나기",        "pouring"),
        ("빗방울",        "rainy"),
        ("빗방울/눈날림", "snowy-rainy"),
        ("눈날림",        "snowy"),
    ])
    def test_kor_to_condition_full_mapping(self, kor, expected_eng):
        """
        [Given] 한국어 날씨 상태 전체 목록
        [When] kor_to_condition 호출
        [Then] HA 표준 영문 condition으로 정확히 매핑되어야 함
        """
        result = KMAWeatherAPI.kor_to_condition(kor)
        assert result == expected_eng, \
            f"'{kor}' → '{result}' (기대: '{expected_eng}')"

    def test_kor_to_condition_unknown_returns_none(self):
        """
        [Given] 매핑 테이블에 없는 한국어 날씨 상태
        [When] kor_to_condition 호출
        [Then] None을 반환해야 함 (크래시 없음)
        """
        assert KMAWeatherAPI.kor_to_condition("모래바람") is None
        assert KMAWeatherAPI.kor_to_condition("") is None
        assert KMAWeatherAPI.kor_to_condition(None) is None
