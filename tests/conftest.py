import pytest
from unittest.mock import patch, MagicMock
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

@pytest.fixture(autouse=True)
def allow_pycares_thread(monkeypatch):
    """pycares DNS 라이브러리의 스레드 허용 패치"""
    import threading
    original_enumerate = threading.enumerate
    def patched_enumerate():
        return [t for t in original_enumerate() if "_run_safe_shutdown_loop" not in t.name]
    monkeypatch.setattr(threading, "enumerate", patched_enumerate)

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield

# ==========================================
# 10일치 데이터를 포함한 전체 MOCK 데이터
# ==========================================
def generate_forecast(start_date, days=10):
    """10일치 일별 및 오전/오후 예보 생성"""
    daily = []
    twice_daily = []
    for i in range(days):
        date = start_date + __import__("datetime").timedelta(days=i)
        date_str = date.strftime("%Y-%m-%d")
        
        # 일별 데이터
        daily.append({
            "datetime": f"{date_str}T00:00:00Z",
            "temperature": 25 - i,
            "templow": 15 - i,
            "condition": "sunny" if i % 2 == 0 else "partlycloudy"
        })
        
        # 오전/오후 데이터 (총 20개)
        twice_daily.append({
            "datetime": f"{date_str}T00:00:00Z",
            "is_daytime": True,
            "condition": "sunny",
            "temperature": 25 - i,
            "precipitation_probability": 10 + i
        })
        twice_daily.append({
            "datetime": f"{date_str}T12:00:00Z",
            "is_daytime": False,
            "condition": "clear-night",
            "temperature": 18 - i,
            "precipitation_probability": 20 + i
        })
    return daily, twice_daily

now = dt_util.now()
daily_data, twice_daily_data = generate_forecast(now)

MOCK_SCENARIOS = {
    "full_test": {
        "weather": {
            "TMP": "22.5", "REH": "45", "WSD": "2.1", "VEC": "150", 
            "POP": "10", "PTY": "0", "SKY": "1",
            "TMX_today": "25", "TMN_today": "15",
            "wf_am_today": "맑음", "wf_pm_today": "구름많음",
            "TMX_tomorrow": "26", "TMN_tomorrow": "16",
            "wf_am_tomorrow": "흐림", "wf_pm_tomorrow": "맑음",
            "apparent_temp": "23.4", "rain_start_time": "강수없음",
            "address": "경기도 화성시", "현재 위치": "경기도 화성시",
            "forecast_daily": daily_data,
            "forecast_twice_daily": twice_daily_data,
        },
        "air": {
            "station": "화성", "pm10": "35", "pm25": "15", 
            "pm10Grade": "1", "pm25Grade": "1"
        }
    },
    "jeju_missing": {
        "weather": {"TMP": None, "address": "제주시", "현재 위치": "제주시", "forecast_twice_daily": []},
        "air": {}
    }
}

@pytest.fixture
def kma_api_mock_factory():
    def _create_mock(scenario_name):
        mock_fetch = patch("custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data").start()
        if scenario_name == "error":
            mock_fetch.side_effect = Exception("API Server Error")
        else:
            mock_fetch.return_value = MOCK_SCENARIOS[scenario_name]
        return mock_fetch
    yield _create_mock
    patch.stopall()

@pytest.fixture
def mock_config_entry():
    return MockConfigEntry(
        domain="kma_weather",
        data={"api_key": "test_key", "location_mode": "zone", "prefix": "test"},
        entry_id="test_id"
    )
