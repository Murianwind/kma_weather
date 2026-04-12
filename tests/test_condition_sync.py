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
    for cat, val in [("TMP","20"),("SKY",sky),("PTY",pty),("REH","50"),("WSD","2")]:
        items.append({"fcstDate": date_str, "fcstTime": time_str, "category": cat, "fcstValue": val})
    return {"response": {"body": {"items": {"item": items}}}}

def _build_coordinator_data(kor: str, eng: str) -> dict:
    """통합 테스트용 coordinator.data Mock 데이터 생성"""
    now = dt_util.now()
    return {
        "weather": {
            "TMP": 22.0, "REH": 50, "WSD": 2.0, "VEC_KOR": "남동",
            "current_condition_kor": kor,
            "current_condition": eng,
            "forecast_twice_daily": [
                {"datetime": now.isoformat(), "is_daytime": True, "condition": eng, "native_temperature": 25}
            ],
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

    def test_mapping_integrity(self):
        # Given: KOR_TO_CONDITION 매핑 테이블이 정의되어 있을 때
        mapping = KOR_TO_CONDITION

        # When: 모든 한글/영문 매핑 쌍을 검사하면
        # Then: 영문값은 HA 표준을 따라야 하며 필수 한글 키가 존재해야 함
        required_keys = {"맑음", "구름많음", "흐림", "비", "비/눈", "소나기", "눈"}
        
        for kor, eng in mapping.items():
            assert eng in self.HA_STANDARD_CONDITIONS
            assert kor is not None and eng is not None
        
        assert required_keys.issubset(mapping.keys())

# ─────────────────────────────────────────────────────────────────────────────
# 2. 단위 테스트 (TestKorToConditionMethod)
# ─────────────────────────────────────────────────────────────────────────────

class TestKorToConditionMethod:
    @pytest.mark.parametrize("kor,expected_eng", ALL_CONDITIONS)
    def test_known_values(self, kor, expected_eng):
        # Given: 알려진 한글 상태명 '{kor}'이 주어졌을 때
        # When: 변환 메서드를 호출하면
        actual_eng = KMAWeatherAPI.kor_to_condition(kor)
        
        # Then: 기대되는 영문명 '{expected_eng}'와 일치해야 함
        assert actual_eng == expected_eng

    def test_edge_cases(self):
        # Given: None 또는 정의되지 않은 입력값이 주어졌을 때
        # When: 변환을 시도하면
        # Then: 결과는 항상 None이어야 함
        assert KMAWeatherAPI.kor_to_condition(None) is None
        assert KMAWeatherAPI.kor_to_condition("알수없음") is None

# ─────────────────────────────────────────────────────────────────────────────
# 3. 데이터 병합 동기화 (TestMergeAllSync)
# ─────────────────────────────────────────────────────────────────────────────

class TestMergeAllSync:
    def _api(self) -> KMAWeatherAPI:
        api = KMAWeatherAPI(MagicMock(), "key", "11B10101", "11B00000")
        api.lat, api.lon, api.nx, api.ny = 37.56, 126.98, 60, 127
        return api

    @pytest.mark.parametrize("sky,pty,expected_kor,expected_eng", [
        ("1", "0", "맑음", "sunny"), ("4", "0", "흐림", "cloudy"),
    ], ids=["맑음", "흐림"])
    def test_condition_sync_during_merge(self, sky, pty, expected_kor, expected_eng):
        # Given: 특정 SKY/PTY 코드를 포함한 API 응답 데이터가 주어졌을 때
        api = self._api()
        now = datetime(2025, 6, 1, 14, 0, tzinfo=TZ)
        short_res = _short_res(now.strftime("%Y%m%d"), "1500", sky, pty)

        # When: 데이터를 병합(merge)하여 결과 weather 딕셔너리를 추출하면
        result = api._merge_all(now, short_res, (None, None, now), air_data={})
        weather = result["weather"]

        # Then: 한글 키와 영문 키가 모두 올바른 값으로 생성되어야 함
        assert weather["current_condition_kor"] == expected_kor
        assert weather["current_condition"] == expected_eng

# ─────────────────────────────────────────────────────────────────────────────
# 4. 코디네이터 업데이트 동기화 (TestCoordinatorConditionSync)
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
        # Given: 특정 시각({hour}시)과 코디네이터가 사용할 오전/오후 예보 데이터 준비
        from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
        hass.config.latitude, hass.config.longitude = 37.56, 126.98
        entry = MagicMock(data={"api_key": "test", "prefix": "sync"}, options={}, entry_id="coord_test")
        
        coord = KMAWeatherUpdateCoordinator(hass, entry)
        coord.api.tz = TZ
        
        # 핵심: fetch_data가 (short, mid, air) 3-튜플을 반환하도록 하여 Unpacking 에러 방지
        coord.api.fetch_data = AsyncMock(return_value=({"weather": {"wf_am_today": wf_am, "wf_pm_today": wf_pm}}, None, {}))

        # When: 특정 시각으로 패치하여 코디네이터 데이터 갱신을 수행하면
        fake_now = datetime(2025, 6, 1, hour, 0, tzinfo=TZ)
        with patch("custom_components.kma_weather.coordinator.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = await coord._async_update_data()

        # Then: 업데이트 결과값이 존재해야 하며, 한글/영문 상태가 시각에 맞춰 동기화되어야 함
        assert result is not None, "코디네이터 업데이트가 None을 반환함 (로직 에러 확인 필요)"
        assert result["weather"]["current_condition_kor"] == expected_kor
        assert result["weather"]["current_condition"] == KOR_TO_CONDITION[expected_kor]

# ─────────────────────────────────────────────────────────────────────────────
# 5. 통합 엔티티 상태 동기화 (test_integration_sync)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("kor,eng", [("맑음", "sunny"), ("비", "rainy")])
async def test_integration_sync(hass, kor, eng):
    # Given: 특정 기상 상태('{kor}')를 가진 데이터와 HA 설정이 주어졌을 때
    entry = MockConfigEntry(domain=DOMAIN, data={"prefix": "sync", "api_key": "test"}, entry_id=f"sync_{kor}")
    
    # When: 코디네이터의 데이터 수집 로직을 패치하고 통합 구성요소를 로드하면
    with patch("custom_components.kma_weather.coordinator.KMAWeatherUpdateCoordinator._async_update_data",
               new_callable=AsyncMock, return_value=_build_coordinator_data(kor, eng)):
        entry.add_to_hass(hass)
        setup_success = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Then: 셋업이 성공해야 하며 센서 엔티티의 상태가 '{kor}'이어야 함
        assert setup_success is True
        sensor_state = hass.states.get("sensor.sync_condition")
        assert sensor_state is not None, "센서 엔티티가 생성되지 않음"
        assert sensor_state.state == kor
        
        # Then: 날씨 엔티티의 예보 데이터(영문)도 '{eng}'로 동기화되어야 함
        response = await hass.services.async_call("weather", "get_forecasts", {"type": "twice_daily"},
                                                  target={"entity_id": "weather.sync_weather"},
                                                  blocking=True, return_response=True)
        forecast = response["weather.sync_weather"]["forecast"]
        assert forecast[0]["condition"] == eng
