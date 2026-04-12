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
    items = []
    for cat, val in [("TMP","20"),("SKY",sky),("PTY",pty),("REH","50"),("WSD","2"),("VEC","135"),("POP","10")]:
        items.append({"fcstDate": date_str, "fcstTime": time_str, "category": cat, "fcstValue": val})
    return {"response": {"body": {"items": {"item": items}}}}

def _build_coordinator_data(kor: str, eng: str) -> dict:
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
# 1. 매핑 테이블 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestKorToConditionMapping:
    HA_STANDARD_CONDITIONS = {
        "sunny", "partlycloudy", "cloudy", "rainy", "snowy",
        "lightning", "lightning-rainy", "fog", "windy", "hail",
        "exceptional", "clear-night", "pouring",
    }

    def test_all_values_are_ha_standard(self):
        # Given: KOR_TO_CONDITION 매핑 테이블이 정의되어 있을 때
        mapping = KOR_TO_CONDITION

        # When: 매핑된 영문 상태값들을 추출하여
        mapped_values = mapping.values()

        # Then: 모든 값은 Home Assistant 표준 상태에 포함되어야 함
        for eng in mapped_values:
            assert eng in self.HA_STANDARD_CONDITIONS

    def test_no_none_values(self):
        # Given: 매핑 테이블의 모든 키와 값에 대해
        # When: 데이터 무결성을 검사하면
        # Then: 어떠한 항목도 None이어서는 안 됨
        for kor, eng in KOR_TO_CONDITION.items():
            assert kor is not None
            assert eng is not None

    def test_required_korean_keys_present(self):
        # Given: 기상청에서 필수적으로 내려오는 한글 상태 키 목록
        required = {"맑음", "구름많음", "흐림", "비", "비/눈", "소나기", "눈"}

        # When: 현재 매핑 테이블의 키 세트를 가져오면
        current_keys = set(KOR_TO_CONDITION.keys())

        # Then: 필수 키 중 누락된 것이 없어야 함
        assert not (required - current_keys)

# ─────────────────────────────────────────────────────────────────────────────
# 2. 유틸리티 메서드 단위 테스트
# ─────────────────────────────────────────────────────────────────────────────

class TestKorToConditionMethod:
    @pytest.mark.parametrize("kor,expected_eng", ALL_CONDITIONS)
    def test_known_values(self, kor, expected_eng):
        # Given: 한글 상태명 'kor'이 주어졌을 때
        # When: 영문 상태명으로 변환을 시도하면
        actual_eng = KMAWeatherAPI.kor_to_condition(kor)

        # Then: 기대값 'expected_eng'와 일치해야 함
        assert actual_eng == expected_eng

    def test_edge_cases(self):
        # Given: 비정상적인 입력값(None 또는 알 수 없는 문자열)
        inputs = [None, "알수없음"]

        for val in inputs:
            # When: 변환 메서드를 호출하면
            result = KMAWeatherAPI.kor_to_condition(val)

            # Then: 결과는 항상 None을 반환해야 함
            assert result is None

# ─────────────────────────────────────────────────────────────────────────────
# 3. 데이터 병합 동기화
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
        # Given: 하늘상태(SKY)와 강수형태(PTY) 코드가 포함된 API 응답
        api = self._api()
        now = datetime(2025, 6, 1, 14, 0, tzinfo=TZ)
        short_res = _short_res(now.strftime("%Y%m%d"), "1500", sky, pty)

        # When: API 데이터를 병합하여 기상 정보를 생성하면
        weather_data = api._merge_all(now, short_res, (None, None), air_data={})["weather"]

        # Then: 한글명과 영문명이 동기화되어 저장되어야 함
        assert weather_data["current_condition_kor"] == expected_kor
        assert weather_data["current_condition"] == expected_eng
        assert KOR_TO_CONDITION[expected_kor] == expected_eng

    def test_no_obsolete_key(self):
        # Given: 정상적인 데이터 수신 상황에서
        api = self._api()
        now = datetime(2025, 6, 1, 14, 0, tzinfo=TZ)
        short_res = _short_res(now.strftime("%Y%m%d"), "1500", "1", "0")

        # When: 데이터 병합을 완료하면
        weather_data = api._merge_all(now, short_res, (None, **{}**), air_data={})["weather"]

        # Then: 구버전 키인 'current_condition_eng'가 존재하지 않아야 함
        assert "current_condition_eng" not in weather_data

# ─────────────────────────────────────────────────────────────────────────────
# 4. 코디네이터 업데이트 동기화
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
        # Given: HA 환경 세팅 및 특정 시간대 예보 데이터 준비
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

        # When: 코디네이터가 시간대별 데이터 갱신을 수행하면
        with patch("custom_components.kma_weather.coordinator.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 6, 1, hour, 0, tzinfo=TZ)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = await coord._async_update_data()

        # Then: 현재 시각에 맞는 한글/영문 상태가 정확히 동기화되어야 함
        actual_kor = result["weather"]["current_condition_kor"]
        actual_eng = result["weather"]["current_condition"]
        
        assert actual_kor == expected_kor
        assert actual_eng == KOR_TO_CONDITION[expected_kor]

# ─────────────────────────────────────────────────────────────────────────────
# 5. 통합 엔티티 상태 동기화
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("kor,eng", ALL_CONDITIONS, ids=[k for k, _ in ALL_CONDITIONS])
async def test_integration_sync(hass, kor, eng):
    # Given: 통합 구성요소가 설정되고 특정 상태('kor')의 데이터가 주입되었을 때
    entry = MockConfigEntry(domain=DOMAIN, data={"prefix": "sync"}, entry_id=f"sync_{kor}")
    
    with patch("custom_components.kma_weather.coordinator.KMAWeatherUpdateCoordinator._async_update_data",
               new_callable=AsyncMock, return_value=_build_coordinator_data(kor, eng)):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # When: 센서 엔티티와 날씨 엔티티의 상태를 각각 조회하면
    sensor_state = hass.states.get("sensor.sync_condition").state
    response = await hass.services.async_call("weather", "get_forecasts", {"type": "twice_daily"},
                                              target={"entity_id": "weather.sync_weather"},
                                              blocking=True, return_response=True)
    forecast_condition = response["weather.sync_weather"]["forecast"][0]["condition"]

    # Then: 센서의 한글값과 날씨 카드의 영문값이 매핑 테이블 기준으로 완벽히 일치해야 함
    assert sensor_state == kor
    assert forecast_condition == eng
    assert KOR_TO_CONDITION[sensor_state] == forecast_condition
