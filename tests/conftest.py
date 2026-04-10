import pytest
from unittest.mock import patch
from datetime import timedelta
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

# 1. 사용자가 해결한 DNS 스레드 패치
@pytest.fixture(autouse=True)
def allow_pycares_thread(monkeypatch):
    import threading
    original_enumerate = threading.enumerate
    def patched_enumerate():
        return [t for t in original_enumerate() if "_run_safe_shutdown_loop" not in t.name]
    monkeypatch.setattr(threading, "enumerate", patched_enumerate)

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield

# 2. 10일치 예보 데이터를 자동으로 만들어주는 도우미
def generate_forecast_data(start_date):
    twice_daily = []
    for i in range(10):
        day = start_date + timedelta(days=i)
        d_str = day.strftime("%Y-%m-%dT00:00:00Z")
        n_str = day.strftime("%Y-%m-%dT12:00:00Z")
        
        # Home Assistant 표준 Forecast 키 (native_... 사용)
        twice_daily.append({
            "datetime": d_str, 
            "is_daytime": True, 
            "condition": "sunny", 
            "native_temperature": 25 - i, 
            "precipitation_probability": 10
        })
        twice_daily.append({
            "datetime": n_str, 
            "is_daytime": False, 
            "condition": "clear-night", 
            "native_temperature": 18 - i, 
            "precipitation_probability": 20
        })
    return twice_daily

now = dt_util.now()
forecast_list = generate_forecast_data(now)

MOCK_SCENARIOS = {
    "full_test": {
        "weather": {
            "TMP": 22.5, "REH": 45, "WSD": 2.1, "VEC_KOR": "남동",
            "POP": 10, "PTY": 0, "SKY": 1,
            "current_condition_kor": "맑음", "current_condition": "sunny",
            "TMX_today": 25, "TMN_today": 15,
            "TMX_tomorrow": 26, "TMN_tomorrow": 16,
            "wf_am_today": "맑음", "wf_pm_today": "구름많음",
            "wf_am_tomorrow": "흐림", "wf_pm_tomorrow": "맑음",
            "apparent_temp": 23.4, "rain_start_time": "강수없음",
            "address": "경기도 화성시", "현재 위치": "경기도 화성시",
            "forecast_twice_daily": forecast_list,
        },
        "air": {
            "pm10Value": 35, "pm10Grade": "좋음", 
            "pm25Value": 15, "pm25Grade": "좋음", "station": "화성"
        }
    },
    "jeju_missing": {
        "weather": {"TMP": None, "address": "제주시", "현재 위치": "제주시", "forecast_twice_daily": []},
        "air": {}
    }
}

@pytest.fixture
def mock_config_entry():
    return MockConfigEntry(
        domain="kma_weather",
        data={"api_key": "test_key", "location_mode": "zone", "prefix": "test"},
        entry_id="mock_id_123"
    )

# tests/conftest.py 의 해당 부분 수정 예시
@pytest.fixture
def kma_api_mock_factory():
    from unittest.mock import AsyncMock # 상단에 추가하거나 여기서 임포트
    def _create_mock(scenario_name):
        # new_callable=AsyncMock 추가가 핵심입니다.
        mock_fetch = patch("custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data", new_callable=AsyncMock).start()
        if scenario_name == "error":
            mock_fetch.side_effect = Exception("API Error")
        else:
            mock_fetch.return_value = MOCK_SCENARIOS.get(scenario_name)
        return mock_fetch
    yield _create_mock
    patch.stopall()
