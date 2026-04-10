# tests/test_condition_sync.py
"""
sensor.{prefix}_condition (한글) 과 weather.{prefix}_weather.condition (영문) 이
항상 동일한 기상 상태를 나타내는지 검증합니다.

수정 내용:
  1. api_kma.py  — current_condition 을 current_condition_kor 로부터 파생
                   (KOR_TO_CONDITION 매핑, kor_to_condition 메서드)
  2. coordinator.py — 시간대 갱신 시 current_condition 도 함께 동기화
  3. weather.py  — condition 속성의 잘못된 키('current_condition_eng')를
                   올바른 키('current_condition')로 수정
"""

import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kma_weather.api_kma import KMAWeatherAPI, KOR_TO_CONDITION
from custom_components.kma_weather.const import DOMAIN


# ---------------------------------------------------------------------------
# 공통 상수
# ---------------------------------------------------------------------------
ALL_CONDITIONS = list(KOR_TO_CONDITION.items())   # [(kor, eng), ...]
TZ = ZoneInfo("Asia/Seoul")


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------
def _short_res(date_str: str, time_str: str, sky: str, pty: str) -> dict:
    """단기예보 API 응답 형식의 mock 데이터를 생성합니다."""
    items = []
    for cat, val in [("TMP","20"),("SKY",sky),("PTY",pty),("REH","50"),("WSD","2"),("VEC","135"),("POP","10")]:
        items.append({"fcstDate": date_str, "fcstTime": time_str, "category": cat, "fcstValue": val})
    return {"response": {"body": {"items": {"item": items}}}}


def _build_coordinator_data(kor: str, eng: str) -> dict:
    """통합 테스트용 coordinator.data 를 생성합니다."""
    now = dt_util.now()
    twice_daily = [
        {
            "datetime": (now + timedelta(days=i)).replace(
                hour=9 if is_am else 21, minute=0, second=0, microsecond=0
            ).isoformat(),
            "is_daytime": is_am,
            "condition": eng,
            "native_temperature": 25 - i,
            "native_templow": 15 - i,
            "native_precipitation_probability": 10,
        }
        for i in range(4)
        for is_am in (True, False)
    ]
    return {
        "weather": {
            "TMP": 22.0, "REH": 50, "WSD": 2.0, "VEC": "135", "VEC_KOR": "남동",
            "POP": 10, "PTY": "0", "SKY": "1",
            "current_condition_kor": kor,
            "current_condition": eng,
            "apparent_temp": 22.0,
            "rain_start_time": "강수없음",
            "address": "경기도 화성시",
            "TMX_today": 25, "TMN_today": 15,
            "TMX_tomorrow": 26, "TMN_tomorrow": 16,
            "wf_am_today": kor, "wf_pm_today": kor,
            "wf_am_tomorrow": kor, "wf_pm_tomorrow": kor,
            "forecast_twice_daily": twice_daily,
            "last_updated": dt_util.utcnow(),
        },
        "air": {
            "pm10Value": 30, "pm10Grade": "좋음",
            "pm25Value": 12, "pm25Grade": "좋음", "station": "화성",
        },
    }


# ---------------------------------------------------------------------------
# 1. KOR_TO_CONDITION 매핑 테이블 완전성 검증
# ---------------------------------------------------------------------------
class TestKorToConditionMapping:
    """모듈 상단의 KOR_TO_CONDITION 매핑 테이블 자체를 검증합니다."""

    HA_STANDARD_CONDITIONS = {
        "sunny", "partlycloudy", "cloudy", "rainy", "snowy",
        "lightning", "lightning-rainy", "fog", "windy", "hail",
        "exceptional", "clear-night", "pouring",
    }

    def test_all_values_are_ha_standard(self):
        """매핑된 모든 영문값이 HA 표준 condition 인지 확인합니다."""
        for kor, eng in KOR_TO_CONDITION.items():
            assert eng in self.HA_STANDARD_CONDITIONS, (
                f"'{kor}' → '{eng}' 는 HA 표준 condition 이 아닙니다."
            )

    def test_no_none_values(self):
        """매핑 테이블에 None 값이 없는지 확인합니다."""
        for kor, eng in KOR_TO_CONDITION.items():
            assert kor is not None and eng is not None

    def test_required_korean_keys_present(self):
        """기상청 API 에서 실제로 발생하는 한글 상태값이 모두 포함되었는지 확인합니다."""
        required = {"맑음", "구름많음", "흐림", "비", "비/눈", "소나기", "눈"}
        missing = required - set(KOR_TO_CONDITION.keys())
        assert not missing, f"매핑 테이블에 누락된 한글 상태값: {missing}"


# ---------------------------------------------------------------------------
# 2. kor_to_condition 단위 테스트
# ---------------------------------------------------------------------------
class TestKorToConditionMethod:
    """KMAWeatherAPI.kor_to_condition() 의 변환 정확성을 검증합니다."""

    @pytest.mark.parametrize("kor,expected_eng", ALL_CONDITIONS)
    def test_known_values(self, kor, expected_eng):
        assert KMAWeatherAPI.kor_to_condition(kor) == expected_eng

    def test_none_input_returns_none(self):
        assert KMAWeatherAPI.kor_to_condition(None) is None

    def test_unknown_input_returns_none(self):
        assert KMAWeatherAPI.kor_to_condition("알수없음") is None


# ---------------------------------------------------------------------------
# 3. _merge_all: current_condition_kor 와 current_condition 동기화 검증
# ---------------------------------------------------------------------------
class TestMergeAllSync:
    """api_kma._merge_all() 이 두 condition 키를 항상 동기화해서 생성하는지 검증합니다."""

    def _api(self) -> KMAWeatherAPI:
        api = KMAWeatherAPI(MagicMock(), "key", "11B10101", "11B00000")
        api.lat, api.lon, api.nx, api.ny = 37.56, 126.98, 60, 127
        return api

    @pytest.mark.parametrize(
        "sky,pty,expected_kor,expected_eng",
        [
            ("1", "0", "맑음",    "sunny"),
            ("3", "0", "구름많음", "partlycloudy"),
            ("4", "0", "흐림",    "cloudy"),
            ("1", "1", "비",      "rainy"),
            ("1", "2", "비/눈",   "rainy"),
            ("1", "3", "눈",      "snowy"),
            ("1", "4", "소나기",  "rainy"),
        ],
        ids=["맑음","구름많음","흐림","비","비눈","눈","소나기"],
    )
    def test_both_keys_populated_and_consistent(self, sky, pty, expected_kor, expected_eng):
        """두 키가 동시에 채워지고 서로 일치하는지 확인합니다."""
        api = self._api()
        now = datetime(2025, 6, 1, 14, 0, tzinfo=TZ)
        short_res = _short_res(now.strftime("%Y%m%d"), "1500", sky, pty)

        w = api._merge_all(now, short_res, (None, None), {})["weather"]

        # 두 키 모두 None 이 아님
        assert w.get("current_condition_kor") is not None
        assert w.get("current_condition") is not None

        # 각각 기댓값과 일치
        assert w["current_condition_kor"] == expected_kor
        assert w["current_condition"] == expected_eng

        # 핵심: 한글값을 매핑하면 영문값과 동일
        assert KOR_TO_CONDITION[w["current_condition_kor"]] == w["current_condition"], (
            f"두 condition 값이 다른 상태를 가리킵니다: "
            f"kor={w['current_condition_kor']}, eng={w['current_condition']}"
        )

    def test_no_obsolete_current_condition_eng_key(self):
        """삭제된 'current_condition_eng' 키가 더 이상 생성되지 않는지 확인합니다."""
        api = self._api()
        now = datetime(2025, 6, 1, 14, 0, tzinfo=TZ)
        short_res = _short_res(now.strftime("%Y%m%d"), "1500", "1", "0")

        w = api._merge_all(now, short_res, (None, None), {})["weather"]
        assert "current_condition_eng" not in w, (
            "'current_condition_eng' 키가 아직 남아 있습니다. "
            "api_kma.py 에서 해당 키 생성 코드를 제거하세요."
        )


# ---------------------------------------------------------------------------
# 4. coordinator: 시간대 갱신 후 두 condition 키 동기화 검증
# ---------------------------------------------------------------------------
class TestCoordinatorConditionSync:
    """coordinator 가 시간대에 따라 condition 을 갱신할 때
    current_condition 도 함께 동기화되는지 검증합니다."""

    def _make_coordinator(self, hass, wf_am: str, wf_pm: str):
        from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
        entry = MagicMock()
        entry.data = {"api_key": "test", "location_entity": ""}
        entry.options = {}
        entry.entry_id = "coord_sync_test"

        coord = KMAWeatherUpdateCoordinator(hass, entry)
        coord._store_loaded = True
        coord._wf_am_today = wf_am
        coord._wf_pm_today = wf_pm
        coord.api.tz = TZ
        return coord

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "hour,wf_am,wf_pm,expected_kor",
        [
            (9,  "맑음",    "구름많음", "맑음"),
            (15, "맑음",    "구름많음", "구름많음"),
            (20, "구름많음", "흐림",    "흐림"),
            (3,  "비",      "맑음",    "비"),
        ],
        ids=["오전9시","오후15시","저녁20시","새벽3시"],
    )
    async def test_condition_keys_synced_after_time_update(
        self, hass, hour, wf_am, wf_pm, expected_kor
    ):
        """시간대별 갱신 후 두 키가 동일한 상태를 가리키는지 확인합니다."""
        coord = self._make_coordinator(hass, wf_am, wf_pm)
        fake_now = datetime(2025, 6, 1, hour, 0, tzinfo=TZ)

        coord.api.fetch_data = AsyncMock(return_value={
            "weather": {
                "TMP": 20, "wf_am_today": wf_am, "wf_pm_today": wf_pm,
                "current_condition_kor": wf_am, "current_condition": KOR_TO_CONDITION[wf_am],
                "forecast_twice_daily": [],
            },
            "air": {},
            "raw_forecast": {},
        })

        with patch(
            "custom_components.kma_weather.coordinator.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = await coord._async_update_data()

        w = result["weather"]
        kor = w.get("current_condition_kor")
        eng = w.get("current_condition")

        # 기댓값 확인
        assert kor == expected_kor, f"kor 기대={expected_kor}, 실제={kor}"
        assert eng == KOR_TO_CONDITION[expected_kor], (
            f"eng 기대={KOR_TO_CONDITION[expected_kor]}, 실제={eng}"
        )

        # 두 값이 동일한 상태를 가리키는지 교차 검증
        assert KOR_TO_CONDITION.get(kor) == eng, (
            f"coordinator 갱신 후 두 condition 값 불일치: kor={kor}, eng={eng}"
        )


# ---------------------------------------------------------------------------
# 5. 통합 테스트: HA 인스턴스에서 두 엔티티 상태 교차 검증
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("kor,eng", ALL_CONDITIONS, ids=[k for k, _ in ALL_CONDITIONS])
async def test_sensor_and_weather_entity_condition_in_sync(hass, kor, eng):
    """
    실제 HA 인스턴스에서
      - sensor.{p}_condition.state (한글)
      - weather.{p}_weather 예보의 condition (영문)
    이 동일한 기상 상태를 나타내는지 통합 검증합니다.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"api_key": "test_key", "prefix": "sync"},
        entry_id=f"sync_{kor}",
        title="기상청 날씨: 화성시",
    )

    with patch(
        "custom_components.kma_weather.coordinator.KMAWeatherUpdateCoordinator._async_update_data",
        new_callable=AsyncMock,
        return_value=_build_coordinator_data(kor, eng),
    ):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # ── 현재날씨 센서 상태 확인 ───────────────────────────────────────────
    sensor_state = hass.states.get("sensor.sync_condition")
    assert sensor_state is not None, "sensor.sync_condition 을 찾을 수 없습니다."
    assert sensor_state.state == kor, (
        f"[센서] 기대='{kor}', 실제='{sensor_state.state}'"
    )

    # ── weather 엔티티 예보에서 condition 확인 ────────────────────────────
    response = await hass.services.async_call(
        "weather", "get_forecasts",
        {"type": "twice_daily"},
        target={"entity_id": "weather.sync_weather"},
        blocking=True, return_response=True,
    )
    forecast = response.get("weather.sync_weather", {}).get("forecast", [])
    assert len(forecast) > 0, "예보 데이터가 비어 있습니다."

    forecast_condition = forecast[0].get("condition")
    assert forecast_condition == eng, (
        f"[예보 condition] 기대='{eng}', 실제='{forecast_condition}'"
    )

    # ── 핵심 교차 검증 ────────────────────────────────────────────────────
    assert KOR_TO_CONDITION.get(sensor_state.state) == forecast_condition, (
        f"센서 한글값 '{sensor_state.state}' 의 영문 매핑 '{KOR_TO_CONDITION.get(sensor_state.state)}' 이 "
        f"예보 condition '{forecast_condition}' 과 다릅니다."
    )

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
