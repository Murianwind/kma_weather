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
def coord():
    hass = MockHass()
    entry = MagicMock()
    # 인스턴스 생성을 최소화하여 로직만 테스트
    coord = KMAWeatherUpdateCoordinator.__new__(KMAWeatherUpdateCoordinator)
    coord.hass = hass
    coord.api = MagicMock()
    coord.api.tz = ZoneInfo("Asia/Seoul")
    coord._daily_date = None
    coord._daily_max_temp = None
    coord._daily_min_temp = None
    return coord

def test_daily_accumulation(coord):
    """기온 누적 및 갱신 로직 검증"""
    today = datetime.now(coord.api.tz).strftime("%Y%m%d")
    
    # 1. 10~20도 데이터 수신
    coord._update_daily_temperatures({today: {"0900": {"TMP": "10"}, "1200": {"TMP": "20"}}})
    assert coord._daily_max_temp == 20
    assert coord._daily_min_temp == 10

    # 2. 15~25도 데이터 수신 (최고치만 25로 갱신되어야 함)
    coord._update_daily_temperatures({today: {"1200": {"TMP": "15"}, "1500": {"TMP": "25"}}})
    assert coord._daily_max_temp == 25
    assert coord._daily_min_temp == 10

def test_daily_reset(coord):
    """날짜 변경 시 초기화 검증"""
    yesterday = datetime.now(coord.api.tz).date()
    coord._daily_date = yesterday
    coord._daily_max_temp = 30.0
    
    # 가짜 '오늘' 데이터 수신 시 날짜가 다르므로 리셋되어야 함
    coord._update_daily_temperatures({"20990101": {"1200": {"TMP": "15"}}})
    assert coord._daily_max_temp == 15
