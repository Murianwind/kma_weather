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
        # Given: KOR_TO_CONDITION 매핑 테이블이 존재할 때
        # When: 매핑 데이터를 전수 검사하면
        # Then: 모든 영문값은 HA 표준을 준수해야 함
        required = {"맑음", "구름많음", "흐림", "비", "비/눈", "소나기", "눈"}
        
        for kor, eng in KOR_TO_CONDITION.items():
            assert eng in self.HA_STANDARD_CONDITIONS
            assert kor is not None and eng is not None
        
        assert not (required - set(KOR_TO_CONDITION.keys()))

# ─────────────────────────────────────────────────────────────────────────────
# 2. 유틸리티 메서드 테스트 (TestKorToConditionMethod)
# ─────────────────────────────────────────────────────────────────────────────

class TestKorToConditionMethod:
    @pytest.mark.parametrize("kor,expected_eng", ALL_CONDITIONS)
    def test_known_values(self, kor, expected_eng):
        # Given: 한글 상태명 'kor'이 주어졌을 때
        # When: kor_to_condition 메서드를 호출하면
        actual_eng = KMAWeatherAPI.kor_to_condition(kor)
        # Then: 결과는 '{expected_eng}'와 일치해야 함
        assert actual_eng == expected_eng

    def test_edge_cases(self):
        # Given: 유효하지 않은 입력값이 주어졌을 때
        # When: 변환을 시도하면
        # Then: 결과는 None을 반환해야 함
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
        ("1", "0", "맑음", "sunny"), ("4", "0", "흐림", "cloudy"),
    ])
    def test_condition_sync_during_merge(self, sky, pty, expected_kor, expected_eng):
        # Given: SKY={sky}, PTY={pty} 조건의 API 응답 데이터
        api = self._api()
        now = datetime(2025, 6, 1, 14, 0, tzinfo=TZ)
        short_res = _short_res(now.strftime("%Y%m%d"), "1500", sky, pty)

        # When: 데이터를 병합(merge)하면
        # midterm_res는 (ta, land, dt) 튜플 형태
        weather = api._merge_all(now, short_res, (None, None, now), air_data={})["weather"]

        # Then: 두 언어의 상태 키가 올바르게 동기화되어야 함
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
        # Given: 특정 시각({hour}시)과 오전/오후 예보 데이터
        from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
        hass.config.latitude, hass.config.longitude = 37.56, 126.98
        entry = MagicMock(data={"api_key": "test", "prefix": "sync"}, options={}, entry_id="coord_test")
        
        coord = KMAWeatherUpdateCoordinator(hass, entry)
        coord.api.tz = TZ
        
        # 핵심: fetch_data가 코디네이터가 예상하는 3-튜플(short, mid, air)을 반환하도록 설정
        # (None, None, air_data)를 주어 merge_all에서 동기화 로직이 동작하게 함
        fake_short = _short_res("20250601", "0800", "1", "0") # 기본값
        coord.api.fetch_data = AsyncMock(return_value=(fake_short, None, {}))
        
        # When: 특정 시각으로 패치하여 데이터를 업데이트하면
        fake_now = datetime(2025, 6, 1, hour, 0, tzinfo=TZ)
        with patch("custom_components.kma_weather.coordinator.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            
            # 테스트를 위해 코디네이터 내부 캐시 수동 설정
            coord._wf_am_today = wf_am
            coord._wf_pm_today = wf_pm
            
            result = await coord._async_update_data()

        # Then: 현재 시각에 따라 한글/영문 상태가 일관되게 갱신되어야 함
        assert result["weather"]["current_condition_kor"] == expected_kor
        assert result["weather"]["current_condition"] == KOR_TO_CONDITION[expected_kor]

# ─────────────────────────────────────────────────────────────────────────────
# 5. 통합 엔티티 상태 동기화 (test_integration_sync)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("kor,eng", [("맑음", "sunny"), ("비", "rainy")])
async def test_integration_sync(hass, kor, eng):
    # Given: 특정 기상 상태('{kor}')를 가진 데이터로 HA 환경이 준비되었을 때
    # CONF_PREFIX(prefix)가 정확해야 엔티티 ID 매칭이 가능함
    entry = MockConfigEntry(domain=DOMAIN, data={"prefix": "sync", "api_key": "test"}, entry_id=f"sync_{kor}")
    
    # 코디네이터가 반환할 데이터를 미리 패치
    with patch("custom_components.kma_weather.coordinator.KMAWeatherUpdateCoordinator._async_update_data",
               new_callable=AsyncMock, return_value=_build_coordinator_data(kor, eng)):
        
        # When: 통합 구성요소를 로드하면
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Then: 생성된 센서 엔티티를 찾을 수 있어야 함
        sensor_state = hass.states.get("sensor.sync_condition")
        assert sensor_state is not None, f"sensor.sync_condition 생성 실패 (kor={kor})"
        assert sensor_state.state == kor
        
        # Then: 날씨 엔티티의 예보 데이터와도 동기화되어야 함
        response = await hass.services.async_call("weather", "get_forecasts", {"type": "twice_daily"},
                                                  target={"entity_id": "weather.sync_weather"},
                                                  blocking=True, return_response=True)
        forecast = response["weather.sync_weather"]["forecast"]
        assert forecast[0]["condition"] == eng
