import pytest
from datetime import datetime, date
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock
from freezegun import freeze_time
from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator


class MockHass:
    def __init__(self):
        self.config = MagicMock()
        self.config.time_zone = ZoneInfo("Asia/Seoul")


@pytest.fixture
def coordinator():
    hass = MockHass()
    entry = MagicMock()
    coord = KMAWeatherUpdateCoordinator.__new__(KMAWeatherUpdateCoordinator)
    coord.hass = hass
    coord.api = MagicMock()
    coord.api.tz = ZoneInfo("Asia/Seoul")
    coord._daily_date = None
    coord._daily_max_temp = None
    coord._daily_min_temp = None
    return coord


@freeze_time("2026-04-08 15:00:00")
def test_initial_accumulation(coordinator):
    """첫 데이터 수신 시 최고/최저가 같은 값으로 초기화되는지 확인"""
    today = datetime.now(coordinator.api.tz).strftime("%Y%m%d")

    coordinator._update_daily_temperatures({today: {"0900": {"TMP": "20"}}})

    assert coordinator._daily_max_temp == 20.0
    assert coordinator._daily_min_temp == 20.0


@freeze_time("2026-04-08 15:00:00")
def test_max_temp_updates_when_higher(coordinator):
    """더 높은 기온이 들어오면 최고온도만 갱신되는지 확인"""
    today = datetime.now(coordinator.api.tz).strftime("%Y%m%d")

    coordinator._update_daily_temperatures({today: {"0900": {"TMP": "12"}}})
    coordinator._update_daily_temperatures({today: {"1000": {"TMP": "13"}}})

    assert coordinator._daily_min_temp == 12.0  # 유지
    assert coordinator._daily_max_temp == 13.0  # 갱신


@freeze_time("2026-04-08 15:00:00")
def test_min_temp_updates_when_lower(coordinator):
    """더 낮은 기온이 들어오면 최저온도만 갱신되는지 확인"""
    today = datetime.now(coordinator.api.tz).strftime("%Y%m%d")

    coordinator._update_daily_temperatures({today: {"0900": {"TMP": "12"}}})
    coordinator._update_daily_temperatures({today: {"1000": {"TMP": "11"}}})

    assert coordinator._daily_min_temp == 11.0  # 갱신
    assert coordinator._daily_max_temp == 12.0  # 유지


@freeze_time("2026-04-08 15:00:00")
def test_accumulation_across_multiple_slots(coordinator):
    """여러 시간 슬롯의 기온 범위가 올바르게 누적되는지 확인"""
    today = datetime.now(coordinator.api.tz).strftime("%Y%m%d")

    # 10~20도 수신
    coordinator._update_daily_temperatures({today: {"0900": {"TMP": "10"}, "1200": {"TMP": "20"}}})
    assert coordinator._daily_max_temp == 20.0
    assert coordinator._daily_min_temp == 10.0

    # 15~25도 수신 → 최고만 25로 갱신
    coordinator._update_daily_temperatures({today: {"1200": {"TMP": "15"}, "1500": {"TMP": "25"}}})
    assert coordinator._daily_max_temp == 25.0
    assert coordinator._daily_min_temp == 10.0  # 유지


@freeze_time("2026-04-08 15:00:00")
def test_daily_reset_on_date_change(coordinator):
    """날짜가 바뀌면 최고/최저가 초기화되는지 확인"""
    # 과거 날짜로 기록 설정
    coordinator._daily_date = date(2020, 1, 1)
    coordinator._daily_max_temp = 30.0
    coordinator._daily_min_temp = 5.0

    # 오늘 날짜의 예보 데이터 주입
    today_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    coordinator._update_daily_temperatures({today_str: {"1200": {"TMP": "15"}}})

    # 이전 값은 버려지고 오늘 값으로 새로 시작
    assert coordinator._daily_max_temp == 15.0
    assert coordinator._daily_min_temp == 15.0
    assert coordinator._daily_date == datetime.now(ZoneInfo("Asia/Seoul")).date()


@freeze_time("2026-04-08 15:00:00")
def test_location_move_preserves_recorded_min(coordinator):
    """
    [핵심] A 지점에서 12도를 기록한 뒤 B 지점(최저 13도)으로 이동해도
    기존에 기록된 최저 12도가 유지되어야 합니다.
    """
    today = datetime.now(coordinator.api.tz).strftime("%Y%m%d")

    # A 지점: 12도 기록
    coordinator._update_daily_temperatures({today: {"0900": {"TMP": "12"}}})
    assert coordinator._daily_min_temp == 12.0
    assert coordinator._daily_max_temp == 12.0

    # B 지점으로 이동: 예보 뭉치에 12도보다 낮은 값이 없음
    coordinator._update_daily_temperatures({
        today: {
            "1200": {"TMP": "13"},
            "1500": {"TMP": "14"},
            "1800": {"TMP": "15"},
        }
    })

    # 최저는 A 지점의 12도를 유지, 최고는 B 지점의 15도로 갱신
    assert coordinator._daily_min_temp == 12.0
    assert coordinator._daily_max_temp == 15.0
