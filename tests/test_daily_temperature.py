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
    # 인스턴스 직접 조작 (테스트용)
    coord = KMAWeatherUpdateCoordinator.__new__(KMAWeatherUpdateCoordinator)
    coord.hass = hass
    coord.api = MagicMock()
    coord.api.tz = ZoneInfo("Asia/Seoul")
    coord._daily_date = None
    coord._daily_max_temp = None
    coord._daily_min_temp = None
    return coord

def test_specific_temperature_scenarios(coordinator):
    """사용자 요청 시나리오: 특정 기온 변화에 따른 누적 로직 검증"""
    today = datetime.now(coordinator.api.tz).strftime("%Y%m%d")
    
    # [시나리오 1] A 지점에서 12도였으나 B 지점에서 13도가 된 경우
    # 1. 초기화 (12도 데이터 인입)
    coordinator._update_daily_temperatures({today: {"0900": {"TMP": "12"}}})
    assert coordinator._daily_min_temp == 12.0
    assert coordinator._daily_max_temp == 12.0

    # 2. 13도 데이터 인입 (최고온도는 13도로 갱신, 최저온도는 12도 유지)
    coordinator._update_daily_temperatures({today: {"1000": {"TMP": "13"}}})
    assert coordinator._daily_min_temp == 12.0  # 갱신되지 않음
    assert coordinator._daily_max_temp == 13.0  # 갱신됨

    # --- 코디네이터 상태 초기화 (다음 시나리오를 위해) ---
    coordinator._daily_max_temp = None
    coordinator._daily_min_temp = None
    coordinator._daily_date = None

    # [시나리오 2] A 지점에서 12도였으나 B 지점에서 11도가 된 경우
    # 1. 초기화 (12도 데이터 인입)
    coordinator._update_daily_temperatures({today: {"0900": {"TMP": "12"}}})
    assert coordinator._daily_min_temp == 12.0
    assert coordinator._daily_max_temp == 12.0

    # 2. 11도 데이터 인입 (최저온도는 11도로 갱신, 최고온도는 12도 유지)
    coordinator._update_daily_temperatures({today: {"1000": {"TMP": "11"}}})
    assert coordinator._daily_min_temp == 11.0  # 갱신됨
    assert coordinator._daily_max_temp == 12.0  # 갱신되지 않음

def test_daily_accumulation(coordinator):
    """기존: 기온 누적 및 범위 갱신 로직 검증"""
    today = datetime.now(coordinator.api.tz).strftime("%Y%m%d")
    
    # 10~20도 데이터 수신
    coordinator._update_daily_temperatures({today: {"0900": {"TMP": "10"}, "1200": {"TMP": "20"}}})
    assert coordinator._daily_max_temp == 20
    assert coordinator._daily_min_temp == 10

    # 15~25도 데이터 수신 (최고치만 25로 갱신)
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

    # 3. 검증: 이전 값들은 버려지고 오늘 값(15.0)으로 새로 시작
    assert coordinator._daily_max_temp == 15.0
    assert coordinator._daily_min_temp == 15.0
    assert coordinator._daily_date == datetime.now(coordinator.api.tz).date()
