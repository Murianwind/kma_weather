# tests/test_current_weather_selection.py

import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from custom_components.kma_weather.api_kma import KMAWeatherAPI

class DummySession:
    """Mock session for API initialization."""
    async def get(self, *args, **kwargs): pass
    async def close(self): pass

def create_api():
    # 실제 api_kma.py 구조에 맞게 초기화
    return KMAWeatherAPI(
        session=DummySession(),
        api_key="test_key",
        reg_id_temp="11B10101",
        reg_id_land="11B00000",
    )

def build_short_response(times):
    """테스트용 단기 예보 응답 생성기"""
    items = []
    for t, tmp in times.items():
        for cat, val in [("TMP", str(tmp)), ("SKY", "1"), ("PTY", "0"), ("REH", "50"), ("WSD", "2")]:
            items.append({
                "fcstDate": "20250101",
                "fcstTime": t,
                "category": cat,
                "fcstValue": val,
            })
    return {"response": {"body": {"items": {"item": items}}}}

@pytest.mark.asyncio
async def test_select_nearest_future_time():
    """14:30일 때 15:00 데이터를 선택하는지 검증"""
    api = create_api()
    now = datetime(2025, 1, 1, 14, 30, tzinfo=ZoneInfo("Asia/Seoul"))
    
    # 12시, 15시 데이터가 있을 때 14:30에서는 15:00를 선택해야 함
    short_res = build_short_response({"1200": 10, "1500": 15, "1800": 18})
    
    result = api._merge_all(now, short_res, (None, None), {}, "Seoul")
    assert str(result["weather"]["TMP"]) == "15"

@pytest.mark.asyncio
async def test_select_latest_past_time_when_no_future():
    """밤 23:30에 미래 데이터가 없으면 마지막 데이터(21시)를 선택하는지 검증"""
    api = create_api()
    now = datetime(2025, 1, 1, 23, 30, tzinfo=ZoneInfo("Asia/Seoul"))
    
    short_res = build_short_response({"1800": 18, "2100": 21})
    
    result = api._merge_all(now, short_res, (None, None), {}, "Seoul")
    assert str(result["weather"]["TMP"]) == "21"
