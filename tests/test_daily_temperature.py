import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock
from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

class MockHass:
    def __init__(self):
        self.config = MagicMock()
        self.config.time_zone = ZoneInfo("Asia/Seoul")

@pytest.fixture
def coordinator():
    hass = MockHass()
    entry = MagicMock()
    # __init__ 호출 시 발생하는 복잡한 로직을 피하기 위해 인스턴스 직접 조작
    coord = KMAWeatherUpdateCoordinator.__new__(KMAWeatherUpdateCoordinator)
    coord.hass = hass
    coord.api = MagicMock()
    coord.api.tz = ZoneInfo("Asia/Seoul")
    coord._daily_date = None
    coord._daily_max_temp = None
    coord._daily_min_temp = None
    return coord

def test_daily_temperature_initialization(coordinator):
    """최초 데이터 수신 시 최고/최저 기온 설정 확인"""
    today = datetime.now(coordinator.api.tz).strftime("%Y%m%d")
    forecast_map = {
        today: {
            "0900": {"TMP": "10.5"},
            "1200": {"TMP": "15.0"},
            "1500": {"TMP": "13.0"}
        }
    }

    coordinator._update_daily_temperatures(forecast_map)

    assert coordinator._daily_max_temp == 15.0
    assert coordinator._daily_min_temp == 10.5
    assert coordinator._daily_date == datetime.now(coordinator.api.tz).date()

def test_temperature_accumulation(coordinator):
    """새로운 예보가 들어왔을 때 기존 값보다 극단적인 경우에만 업데이트되는지 확인"""
    today = datetime.now(coordinator.api.tz).strftime("%Y%m%d")
    
    # 1차 데이터: 10 ~ 15도
    coordinator._update_daily_temperatures({today: {"0900": {"TMP": "10"}, "1200": {"TMP": "15"}}})
    assert coordinator._daily_max_temp == 15
    assert coordinator._daily_min_temp == 10

    # 2차 데이터: 8 ~ 14도 (최저는 갱신, 최고는 유지)
    coordinator._update_daily_temperatures({today: {"0600": {"TMP": "8"}, "1200": {"TMP": "14"}}})
    assert coordinator._daily_max_temp == 15
    assert coordinator._daily_min_temp == 8

    # 3차 데이터: 12 ~ 18도 (최저는 유지, 최고는 갱신)
    coordinator._update_daily_temperatures({today: {"1500": {"TMP": "18"}, "1800": {"TMP": "12"}}})
    assert coordinator._daily_max_temp == 18
    assert coordinator._daily_min_temp == 8

def test_midnight_fallback(coordinator):
    """오늘 날짜 예보가 아직 없을 때(00시경) 가장 가까운 미래 데이터 활용 여부"""
    # 오늘이 20250410인데, 예보는 20250411부터만 있는 상황 시뮬레이션
    forecast_map = {
        "20250411": {"0000": {"TMP": "5.5"}}
    }
    
    coordinator._update_daily_temperatures(forecast_map)
    
    assert coordinator._daily_max_temp == 5.5
    assert coordinator._daily_min_temp == 5.5

def test_daily_reset(coordinator):
    """날짜가 바뀌면 최고/최저 기온이 초기화되는지 확인"""
    # 1. 어제 날짜로 데이터 설정
    yesterday = datetime.now(coordinator.api.tz).date() - timedelta(days=1)
    coordinator._daily_date = yesterday
    coordinator._daily_max_temp = 30.0
    coordinator._daily_min_temp = -10.0

    # 2. 오늘 날짜 데이터로 업데이트 호출
    today_str = datetime.now(coordinator.api.tz).strftime("%Y%m%d")
    forecast_map = {today_str: {"1200": {"TMP": "15.0"}}}
    
    coordinator._update_daily_temperatures(forecast_map)

    # 3. 어제의 데이터는 버려지고 오늘의 데이터로만 새로 시작해야 함
    assert coordinator._daily_date == datetime.now(coordinator.api.tz).date()
    assert coordinator._daily_max_temp == 15.0
    assert coordinator._daily_min_temp == 15.0
