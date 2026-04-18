import pytest
from datetime import datetime, date
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock
from freezegun import freeze_time
from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

# ─────────────────────────────────────────────────────────────────────────────
# [Given] 공통 헬퍼 클래스 및 Fixture
# ─────────────────────────────────────────────────────────────────────────────
class MockHass:
    def __init__(self):
        self.config = MagicMock()
        self.config.time_zone = ZoneInfo("Asia/Seoul")


@pytest.fixture
def coordinator():
    hass = MockHass()
    coord = KMAWeatherUpdateCoordinator.__new__(KMAWeatherUpdateCoordinator)
    coord.hass = hass
    coord.api = MagicMock()
    coord.api.tz = ZoneInfo("Asia/Seoul")
    coord._daily_date = None
    coord._daily_max_temp = None
    coord._daily_min_temp = None
    return coord

# ─────────────────────────────────────────────────────────────────────────────
# 일일 기온 누적 로직 테스트 (BDD 시나리오)
# ─────────────────────────────────────────────────────────────────────────────

@freeze_time("2026-04-08 15:00:00")
def test_initial_accumulation(coordinator):
    """시나리오: 첫 데이터 수신 시 최고/최저가 같은 값으로 초기화됨"""
    
    # [Given] 오늘 날짜 문자열 준비
    today = datetime.now(coordinator.api.tz).strftime("%Y%m%d")

    # [When] 첫 기온 데이터(20도)가 수신되어 누적 업데이트를 수행하면
    coordinator._update_daily_temperatures({today: {"0900": {"TMP": "20"}}})

    # [Then] 최고와 최저 기온 모두 20.0으로 초기화되어야 함
    assert coordinator._daily_max_temp == 20.0
    assert coordinator._daily_min_temp == 20.0


@freeze_time("2026-04-08 15:00:00")
def test_max_temp_updates_when_higher(coordinator):
    """시나리오: 더 높은 기온이 들어오면 최고온도만 갱신됨"""
    
    # [Given] 초기 기온 12도가 기록된 상태에서
    today = datetime.now(coordinator.api.tz).strftime("%Y%m%d")
    coordinator._update_daily_temperatures({today: {"0900": {"TMP": "12"}}})

    # [When] 더 높은 기온(13도) 데이터가 수신되면
    coordinator._update_daily_temperatures({today: {"1000": {"TMP": "13"}}})

    # [Then] 최저 기온은 유지되고 최고 기온만 13.0으로 갱신되어야 함
    assert coordinator._daily_min_temp == 12.0  # 유지
    assert coordinator._daily_max_temp == 13.0  # 갱신


@freeze_time("2026-04-08 15:00:00")
def test_min_temp_updates_when_lower(coordinator):
    """시나리오: 더 낮은 기온이 들어오면 최저온도만 갱신됨"""
    
    # [Given] 초기 기온 12도가 기록된 상태에서
    today = datetime.now(coordinator.api.tz).strftime("%Y%m%d")
    coordinator._update_daily_temperatures({today: {"0900": {"TMP": "12"}}})

    # [When] 더 낮은 기온(11도) 데이터가 수신되면
    coordinator._update_daily_temperatures({today: {"1000": {"TMP": "11"}}})

    # [Then] 최저 기온은 11.0으로 갱신되고 최고 기온은 유지되어야 함
    assert coordinator._daily_min_temp == 11.0  # 갱신
    assert coordinator._daily_max_temp == 12.0  # 유지


@freeze_time("2026-04-08 15:00:00")
def test_accumulation_across_multiple_slots(coordinator):
    """시나리오: 여러 시간 슬롯의 기온 범위가 올바르게 누적됨"""
    
    # [Given] 오늘 날짜 문자열 준비
    today = datetime.now(coordinator.api.tz).strftime("%Y%m%d")

    # [When] 10~20도 범위의 데이터가 수신되면
    coordinator._update_daily_temperatures({today: {"0900": {"TMP": "10"}, "1200": {"TMP": "20"}}})
    
    # [Then] 최고 20, 최저 10으로 설정됨
    assert coordinator._daily_max_temp == 20.0
    assert coordinator._daily_min_temp == 10.0

    # [When] 15~25도 범위의 데이터가 추가로 수신되면 (최고 기온만 갱신 조건)
    coordinator._update_daily_temperatures({today: {"1200": {"TMP": "15"}, "1500": {"TMP": "25"}}})
    
    # [Then] 최고 기온만 25.0으로 갱신되고 최저는 10.0으로 유지되어야 함
    assert coordinator._daily_max_temp == 25.0
    assert coordinator._daily_min_temp == 10.0  # 유지


@freeze_time("2026-04-08 15:00:00")
def test_daily_reset_on_date_change(coordinator):
    """시나리오: 날짜가 바뀌면 최고/최저가 초기화됨"""
    
    # [Given] 과거 날짜(2020년 1월 1일)의 기온 기록이 존재하는 상태에서
    coordinator._daily_date = date(2020, 1, 1)
    coordinator._daily_max_temp = 30.0
    coordinator._daily_min_temp = 5.0

    # [Given] 오늘 날짜 문자열 준비
    today_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    
    # [When] 오늘 날짜의 예보 데이터 주입 시
    coordinator._update_daily_temperatures({today_str: {"1200": {"TMP": "15"}}})

    # [Then] 이전 값은 버려지고 오늘 값으로 새로 시작되어야 함
    assert coordinator._daily_max_temp == 15.0
    assert coordinator._daily_min_temp == 15.0
    assert coordinator._daily_date == datetime.now(ZoneInfo("Asia/Seoul")).date()


@freeze_time("2026-04-08 15:00:00")
def test_location_move_preserves_recorded_min(coordinator):
    """시나리오: 위치 이동으로 예보 범위가 달라져도 이미 기록된 최저 기온은 유지됨"""
    
    # [Given] 오늘 날짜 문자열 준비
    today = datetime.now(coordinator.api.tz).strftime("%Y%m%d")

    # [Given] A 지점에서 12도를 기록한 상태
    coordinator._update_daily_temperatures({today: {"0900": {"TMP": "12"}}})
    assert coordinator._daily_min_temp == 12.0
    assert coordinator._daily_max_temp == 12.0

    # [When] B 지점으로 이동하여 12도보다 높은 값들(13~15도)만 포함된 예보를 수신하면
    coordinator._update_daily_temperatures({
        today: {
            "1200": {"TMP": "13"},
            "1500": {"TMP": "14"},
            "1800": {"TMP": "15"},
        }
    })

    # [Then] 최저 기온은 이전의 12.0을 유지하고, 최고 기온만 15.0으로 갱신되어야 함
    assert coordinator._daily_min_temp == 12.0
    assert coordinator._daily_max_temp == 15.0
