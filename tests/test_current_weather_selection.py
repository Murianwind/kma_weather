import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from custom_components.kma_weather.api_kma import KMAWeatherAPI

class DummySession:
    """API 초기화를 위한 Mock 세션."""
    async def get(self, *args, **kwargs): pass
    async def close(self): pass

def create_api():
    """테스트용 KMAWeatherAPI 인스턴스 생성"""
    return KMAWeatherAPI(
        session=DummySession(),
        api_key="test_key",
        reg_id_temp="11B10101",
        reg_id_land="11B00000",
    )

def build_short_response(times):
    """테스트용 단기 예보 응답 생성 데이터 빌더"""
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
    """시나리오: 현재 시각보다 조금 더 미래의 예보 데이터를 선택함"""
    
    # Given: 14시 30분이고, 12시/15시/18시 데이터가 준비된 상황
    api = create_api()
    now = datetime(2025, 1, 1, 14, 30, tzinfo=ZoneInfo("Asia/Seoul"))
    short_forecast_data = build_short_response({"1200": 10, "1500": 15, "1800": 18})
    
    # When: 데이터를 병합하고 현재 날씨를 결정할 때 (매개변수 이름 수정)
    result = api._merge_all(
        now=now, 
        short_res=short_forecast_data, 
        mid_res=(None, None), 
        air_data={},        # 기존 mid_land_res 대신 air_data 사용
        address="Seoul"      # 기존 location_name 대신 address 사용
    )
    
    # Then: 14:30과 가장 가까운 미래인 15:00의 기온(15도)이 선택되어야 함
    assert str(result["weather"]["TMP"]) == "15"

@pytest.mark.asyncio
async def test_select_latest_past_time_when_no_future():
    """시나리오: 미래 예보가 없을 경우 가장 최근의 과거 데이터를 선택함"""
    
    # Given: 23시 30분이고, 18시/21시 데이터만 있는 상황 (미래 데이터 없음)
    api = create_api()
    now = datetime(2025, 1, 1, 23, 30, tzinfo=ZoneInfo("Asia/Seoul"))
    short_forecast_data = build_short_response({"1800": 18, "2100": 21})
    
    # When: 데이터를 병합할 때 (매개변수 이름 수정)
    result = api._merge_all(
        now=now, 
        short_res=short_forecast_data, 
        mid_res=(None, None), 
        air_data={}, 
        address="Seoul"
    )
    
    # Then: 가용한 데이터 중 가장 최신인 21:00의 기온(21도)이 선택되어야 함
    assert str(result["weather"]["TMP"]) == "21"
