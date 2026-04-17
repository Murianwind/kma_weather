import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from custom_components.kma_weather.api_kma import KMAWeatherAPI

class DummySession:
    async def get(self, *args, **kwargs): pass
    async def close(self): pass

def create_api():
    """테스트용 KMAWeatherAPI 인스턴스 생성 — reg_id 파라미터 제거"""
    return KMAWeatherAPI(
        session=DummySession(),
        api_key="test_key",
    )

def build_short_response(times):
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
    api = create_api()
    now = datetime(2025, 1, 1, 14, 30, tzinfo=ZoneInfo("Asia/Seoul"))
    short_forecast_data = build_short_response({"1200": 10, "1500": 15, "1800": 18})
    result = api._merge_all(
        now=now,
        short_res=short_forecast_data,
        mid_res=(None, None),
        air_data={},
        address="Seoul"
    )
    assert str(result["weather"]["TMP"]) == "15"

@pytest.mark.asyncio
async def test_select_latest_past_time_when_no_future():
    api = create_api()
    now = datetime(2025, 1, 1, 23, 30, tzinfo=ZoneInfo("Asia/Seoul"))
    short_forecast_data = build_short_response({"1800": 18, "2100": 21})
    result = api._merge_all(
        now=now,
        short_res=short_forecast_data,
        mid_res=(None, None),
        air_data={},
        address="Seoul"
    )
    assert str(result["weather"]["TMP"]) == "21"
