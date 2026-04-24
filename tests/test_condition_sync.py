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
