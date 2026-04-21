import pytest
from unittest.mock import patch
from datetime import timedelta
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

# 1. DNS 스레드 패치
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

# 2. 10일치 예보 데이터 생성 헬퍼
def generate_forecast_data(start_date):
    twice_daily = []
    for i in range(10):
        day = start_date + timedelta(days=i)
        d_str = day.strftime("%Y-%m-%dT00:00:00Z")
        n_str = day.strftime("%Y-%m-%dT12:00:00Z")
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

# ── Mock 시나리오 — 모든 수치는 API가 실제로 보내는 원본 형식 그대로 ──
# 기상청 단기예보 TMP, REH 등은 정수 문자열("22", "45")로 오고
# WSD는 소수점 1자리("2.1"), apparent_temp는 소수점 1자리("23.4")
# pm10/pm25 농도는 정수("35", "15")
MOCK_SCENARIOS = {
    "full_test": {
        "weather": {
            # 기상청 원본: 온도/습도/강수확률은 정수
            "TMP": 22,
            "REH": 45,
            # 기상청 원본: 풍속은 소수점 1자리
            "WSD": 2.1,
            "VEC_KOR": "남동",
            "POP": 10,
            "PTY": 0,
            "SKY": 1,
            "current_condition_kor": "맑음",
            "current_condition": "sunny",
            "TMX_today": 25,
            "TMN_today": 15,
            "TMX_tomorrow": 26,
            "TMN_tomorrow": 16,
            "wf_am_today": "맑음",
            "wf_pm_today": "구름많음",
            "wf_am_tomorrow": "흐림",
            "wf_pm_tomorrow": "맑음",
            # 체감온도: 소수점 1자리
            "apparent_temp": 23.4,
            "rain_start_time": "강수없음",
            "address": "경기도 화성시",
            "현재 위치": "경기도 화성시",
            "forecast_twice_daily": forecast_list,
        },
        "air": {
            # 에어코리아 원본: 농도는 정수
            "pm10Value": 35,
            "pm10Grade": "좋음",
            "pm25Value": 15,
            "pm25Grade": "좋음",
            "station": "화성"
        }
    },
    "jeju_missing": {
        "weather": {"address": "제주시", "현재 위치": "제주시", "forecast_twice_daily": []},
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

@pytest.fixture
def kma_api_mock_factory():
    def _create_mock(scenario_name):
        mock_fetch = patch("custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data").start()
        if scenario_name == "error":
            mock_fetch.side_effect = Exception("API Error")
        else:
            mock_fetch.return_value = MOCK_SCENARIOS.get(scenario_name)
        return mock_fetch
    yield _create_mock
    patch.stopall()
