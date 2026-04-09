import pytest
from datetime import datetime, date
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
    # 인스턴스 직접 조작
    coord = KMAWeatherUpdateCoordinator.__new__(KMAWeatherUpdateCoordinator)
    coord.hass = hass
    coord.api = MagicMock()
    coord.api.tz = ZoneInfo("Asia/Seoul")
    coord._daily_date = None
    coord._daily_max_temp = None
    coord._daily_min_temp = None
    return coord

def test_daily_accumulation(coordinator):
    """기온 누적 및 갱신 로직 검증"""
    today = datetime.now(coordinator.api.tz).strftime("%Y%m%d")
    
    # 1. 10~20도 데이터 수신
    coordinator._update_daily_temperatures({today: {"0900": {"TMP": "10"}, "1200": {"TMP": "20"}}})
    assert coordinator._daily_max_temp == 20
    assert coordinator._daily_min_temp == 10

    # 2. 15~25도 데이터 수신 (최고치만 25로 갱신)
    coordinator._update_daily_temperatures({today: {"1200": {"TMP": "15"}, "1500": {"TMP": "25"}}})
    assert coordinator._daily_max_temp == 25
    assert coordinator._daily_min_temp == 10

def test_daily_reset(coordinator):
    """날짜 변경 시 최고/최저 기온이 초기화되는지 확인"""
    # 1. 과거 날짜로 설정
    past_date = date(2020, 1, 1)
    coordinator._daily_date = past_date
    coordinator._daily_max_temp = 30.0
    coordinator._daily_min_temp = 5.0

    # 2. 현재 날짜 예보 데이터 주입
    today_str = datetime.now(coordinator.api.tz).strftime("%Y%m%d")
    forecast = {today_str: {"1200": {"TMP": "15"}}}
    
    coordinator._update_daily_temperatures(forecast)

    # 3. 검증: 30.0이 15.0으로 리셋되어야 함
    assert coordinator._daily_max_temp == 15.0
    assert coordinator._daily_date == datetime.now(coordinator.api.tz).date()
