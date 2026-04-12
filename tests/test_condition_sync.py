import pytest
from datetime import datetime, timedelta
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

def _short_res(date_str: str, time_str: str, sky: str, pty: str) -> dict:
    """단기예보 API 응답 Mock 데이터 생성"""
    items = []
    for cat, val in [("TMP","20"),("SKY",sky),("PTY",pty),("REH","50"),("WSD","2"),("VEC","135"),("POP","10")]:
        items.append({"fcstDate": date_str, "fcstTime": time_str, "category": cat, "fcstValue": val})
    return {"response": {"body": {"items": {"item": items}}}}

def _build_coordinator_data(kor: str, eng: str) -> dict:
    """통합 테스트용 coordinator.data Mock 데이터 생성"""
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
            "current_condition_kor": kor,
            "current_condition": eng,
            "forecast_twice_daily": twice_daily,
            "last_updated": dt_util.utcnow(),
        },
        "air": {},
    }

# ─────────────────────────────────────────────────────────────────────────────
# 1. 매핑 테이블 검증 (TestKorToConditionMapping)
# ─────────────────────────────────────────────────────────────────────────────

class TestKorToConditionMapping:
    HA_STANDARD_CONDITIONS = {
        "sunny", "partlycloudy", "cloudy", "rainy", "snowy",
        "lightning", "lightning-rainy", "fog", "windy", "hail",
        "exceptional", "clear-night", "pouring",
    }

    def test_all_values_are_ha_standard(self):
        # Given: KOR_TO_CONDITION 매핑 테이블이 존재할 때
        # When: 매핑된 영문 상태값들을 검사하면
        # Then: 모든 값은 HA 표준 condition에 포함되어야 함
        for kor, eng in KOR_TO_CONDITION.items():
            assert eng in self.HA_STANDARD_CONDITIONS

    def test_no_none_values(self):
        # Given: 매핑 테이블의 모든 데이터를 순회하며
        # When: 무결성을 검사할 때
        # Then: 한글 키나 영문 값에 None이 있어서는 안 됨
        for kor, eng in KOR_TO_CONDITION.items():
            assert kor is not None
            assert eng is not None

    def test_required_korean_keys_present(self):
        # Given: 기상청 API에서 필수로 사용되는 한글 상태 목록
        required = {"맑음", "구름많음", "흐림", "비", "비/눈", "소나기", "눈"}
        
        # When: 현재 매핑 테이블의 키 세트를 확인하면
        # Then: 필수 키가 하나라도 누락되어서는 안 됨
        missing = required - set(KOR_TO_CONDITION.keys())
        assert not missing

# ─────────────────────────────────────────────────────────────────────────────
# 2. 유틸리티 메서드 테스트 (TestKorToConditionMethod)
# ─────────────────────────────────────────────────────────────────────────────

class TestKorToConditionMethod:
    @pytest.mark.parametrize("kor,expected_eng", ALL_CONDITIONS)
    def test_known_values(self, kor, expected_eng):
        # Given: 한글 기상 상태명 'kor'이 주어졌을 때
        # When: kor_to_condition 메서드를 통해 변환하면
        actual_eng = KMAWeatherAPI.kor_to_condition(kor)
        
        # Then: 기대하는 영문명 'expected_eng'와 정확히 일치해야 함
        assert actual_eng == expected_eng

    def test_edge_cases(self):
        # Given: None 또는 정의되지 않은 문자열 입력값이 주어졌을 때
        # When: 변환을 시도하면
        # Then: 결과는 항상 None이어야 함
        assert KMAWeatherAPI.kor_to_condition(None) is None
        assert KMAWeatherAPI.kor_to_condition("알수없음") is None

# ─────────────────────────────────────────────────────────────────────────────
# 3. 데이터 병합 동기화 검증 (TestMergeAllSync)
# ─────────────────────────────────────────────────────────────────────────────

class TestMergeAllSync:
    def _api(self) -> KMAWeatherAPI:
        api = KMAWeatherAPI(MagicMock(), "key", "11B10101", "11B00000")
        api.lat, api.lon, api.nx, api.ny = 37.56, 126.98, 60, 127
        return api

    @pytest.mark.parametrize("sky,pty,expected_kor,expected_eng", [
        ("1", "0", "맑음", "sunny"), ("3", "0", "구름많음", "partlycloudy"),
        ("4", "0", "흐림", "cloudy"), ("1", "1", "비", "rainy"),
        ("1", "2", "비/눈", "rainy"), ("1", "3", "눈", "snowy"),
        ("1", "4", "소나기", "rainy"),
    ], ids=["맑음","구름많음","흐림","비","비눈","눈","소나기"])
    def test_condition_sync_during_merge(self, sky, pty, expected_kor, expected_eng):
        # Given: 특정 SKY, PTY 코드를 포함한 API 응답 데이터
        api = self._api()
        now = datetime(2025, 6, 1, 14, 0, tzinfo=TZ)
        short_res = _short_res(now.strftime("%Y%m%d"), "1500", sky, pty)

        # When: API 데이터를 병합하여 weather 데이터를 생성하면
        weather = api._merge_all(now, short_res, (None, None, now), air_data={})["weather"]

        # Then: 한글 키와 영문 키가 모두 올바른 값으로 생성되어야 함
        assert weather["current_condition_kor"] == expected_kor
        assert weather["current_condition"] == expected_eng

    def test_no_obsolete_key(self):
        # Given: 정상적인 데이터 수신 상황
        api = self._api()
        now = datetime(2025, 6, 1, 14, 0, tzinfo=TZ)
        short_res = _short_res(now.strftime("%Y%m%d"), "1500", "1", "0")

        # When: 데이터 병합이 완료된 후 결과에서
        weather = api._merge_all(now, short_res, (None, None, now), air_data={})["weather"]

        # Then: 더 이상 사용하지 않는 'current_condition_eng' 키는 존재하지 않아야 함
        assert "current_condition_eng" not in weather

# ─────────────────────────────────────────────────────────────────────────────
# 4. 코디네이터 동기화 검증 (TestCoordinatorConditionSync)
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordinatorConditionSync:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("hour,wf_am,wf_pm,expected_kor", [
        (9,  "맑음", "구름많음", "맑음"),
        (15, "맑음", "구름많음", "구름많음"),
        (20, "구름많음", "흐림", "흐림"),
        (3,  "비", "맑음", "비"),
    ], ids=["오전9시","오후15시","저녁20시","새벽3시"])
    async def test_coordinator_sync_logic(self, hass, hour, wf_am, wf_pm, expected_kor):
        # Given: HA 환경과 특정 시간대의 예보 데이터가 준비되었을 때
        from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
        hass.config.latitude, hass.config.longitude = 37.56, 126.98
        entry = MagicMock(data={"api_key": "test"}, options={}, entry_id="coord_test")
        
        coord = KMAWeatherUpdateCoordinator(hass, entry)
        coord._wf_am_today, coord._wf_pm_today = wf_am, wf_pm
        coord.api.tz = TZ
        coord.api.fetch_data = AsyncMock(return_value={
            "weather": {"current_condition_kor": wf_am, "current_condition": KOR_TO_CONDITION[wf_am], "forecast_twice_daily": []},
            "air": {}, "raw_forecast": {},
        })

        # When: 코디네이터가 시간대별 데이터 업데이트를 수행하면
        with patch("custom_components.kma_weather.coordinator.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 6, 1, hour, 0, tzinfo=TZ)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = await coord._async_update_data()

        # Then: 한글 상태와 영문 상태가 현재 시각에 맞춰 동기화되어야 함
        assert result["weather"]["current_condition_kor"] == expected_kor
        assert result["weather"]["current_condition"] == KOR_TO_CONDITION[expected_kor]

# ─────────────────────────────────────────────────────────────────────────────
# 5. 통합 엔티티 동기화 (test_integration_sync)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("kor,eng", ALL_CONDITIONS, ids=[k for k, _ in ALL_CONDITIONS])
async def test_integration_sync(hass, kor, eng):
    # Given: 특정 기상 상태(kor)를 가진 데이터로 HA 통합 구성요소가 설정되었을 때
    entry = MockConfigEntry(domain=DOMAIN, data={"prefix": "sync"}, entry_id=f"sync_{kor}")
    
    with patch("custom_components.kma_weather.coordinator.KMAWeatherUpdateCoordinator._async_update_data",
               new_callable=AsyncMock, return_value=_build_coordinator_data(kor, eng)):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # When: 센서와 날씨 엔티티의 상태를 각각 조회하면
    sensor_state = hass.states.get("sensor.sync_condition").state
    response = await hass.services.async_call("weather", "get_forecasts", {"type": "twice_daily"},
                                              target={"entity_id": "weather.sync_weather"},
                                              blocking=True, return_response=True)
    forecast_condition = response["weather.sync_weather"]["forecast"][0]["condition"]

    # Then: 센서(한글)와 날씨 엔티티(영문)의 상태가 매핑 테이블 기준으로 완벽히 일치해야 함
    assert sensor_state == kor
    assert forecast_condition == eng
    assert KOR_TO_CONDITION[sensor_state] == forecast_condition
