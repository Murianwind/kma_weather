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
    }

# ─────────────────────────────────────────────────────────────────────────────
# 1. 매핑 테이블/단위 테스트 (생략 없이 로직 보존)
# ─────────────────────────────────────────────────────────────────────────────

class TestKorToConditionMapping:
    def test_mapping_integrity(self):
        # Given: 매핑 테이블이 존재할 때
        # Then: HA 표준 준수 및 필수 키 존재 확인
        required = {"맑음", "구름많음", "흐림", "비", "비/눈", "소나기", "눈"}
        for kor, eng in KOR_TO_CONDITION.items():
            assert eng in {"sunny", "partlycloudy", "cloudy", "rainy", "snowy", "lightning", "lightning-rainy", "fog", "windy", "hail", "exceptional", "clear-night", "pouring"}
        assert required.issubset(set(KOR_TO_CONDITION.keys()))

class TestKorToConditionMethod:
    @pytest.mark.parametrize("kor,expected_eng", ALL_CONDITIONS)
    def test_known_values(self, kor, expected_eng):
        # Given/When/Then: 한글 상태 입력 시 정확한 영문 반환
        assert KMAWeatherAPI.kor_to_condition(kor) == expected_eng

# ─────────────────────────────────────────────────────────────────────────────
# 2. 코디네이터 업데이트 동기화 (Regression Fixed)
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
        # Given: 특정 시각과 예보 데이터 준비
        from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
        hass.config.latitude, hass.config.longitude = 37.56, 126.98
        entry = MagicMock(data={"api_key": "test", "prefix": "sync"}, options={}, entry_id="coord_test")
        
        coord = KMAWeatherUpdateCoordinator(hass, entry)
        coord.api.tz = TZ
        
        # [Fix] fetch_data는 튜플이 아닌 _merge_all 결과인 '딕셔너리'를 반환해야 함
        coord.api.fetch_data = AsyncMock(return_value={
            "weather": {
                "wf_am_today": wf_am, "wf_pm_today": wf_pm, 
                "current_condition_kor": wf_am, "forecast_twice_daily": [], "forecast_daily": []
            },
            "air": {}, "raw_forecast": {"20250601": {"0800": {"TMP": "20"}}}
        })

        # When: 시각을 패치하여 업데이트 수행
        fake_now = datetime(2025, 6, 1, hour, 0, tzinfo=TZ)
        with patch("custom_components.kma_weather.coordinator.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = await coord._async_update_data()

        # Then: 결과가 None이 아니어야 하며 상태가 동기화되어야 함
        assert result is not None
        assert result["weather"]["current_condition_kor"] == expected_kor
        assert result["weather"]["current_condition"] == KOR_TO_CONDITION[expected_kor]

# ─────────────────────────────────────────────────────────────────────────────
# 3. 통합 엔티티 동기화 (Regression Fixed)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("kor,eng", [("맑음", "sunny"), ("비", "rainy")])
async def test_integration_sync(hass, kor, eng):
    # Given: 특정 상태 데이터 주입
    entry = MockConfigEntry(domain=DOMAIN, data={"prefix": "sync", "api_key": "test"}, entry_id=f"sync_{kor}")
    
    # [Fix] 패치 범위를 넓혀 셋업 시 발생하는 모든 업데이트 요청 대응
    with patch("custom_components.kma_weather.coordinator.KMAWeatherUpdateCoordinator._async_update_data",
               new_callable=AsyncMock, return_value=_build_coordinator_data(kor, eng)):
        
        # When: 엔티티 생성 및 셋업
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Then: 센서 상태 확인
        sensor_state = hass.states.get("sensor.sync_condition")
        assert sensor_state is not None, "센서 엔티티가 생성되지 않았습니다."
        assert sensor_state.state == kor
        
        # Then: 예보 데이터 동기화 확인
        response = await hass.services.async_call("weather", "get_forecasts", {"type": "twice_daily"},
                                                  target={"entity_id": "weather.sync_weather"},
                                                  blocking=True, return_response=True)
        assert response["weather.sync_weather"]["forecast"][0]["condition"] == eng
