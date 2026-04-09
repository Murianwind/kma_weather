import pytest
from unittest.mock import patch
from datetime import timedelta
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

# 1. 사용자가 해결한 DNS 스레드 패치 (최상단 유지)
@pytest.fixture(autouse=True)
def allow_pycares_thread(monkeypatch):
    """pycares DNS 라이브러리의 _run_safe_shutdown_loop 스레드 허용"""
    import threading
    original_enumerate = threading.enumerate
    def patched_enumerate():
        return [t for t in original_enumerate() if "_run_safe_shutdown_loop" not in t.name]
    monkeypatch.setattr(threading, "enumerate", patched_enumerate)

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """테스트 시 커스텀 컴포넌트를 HA가 인식하도록 허용"""
    yield

# 2. 10일치 예보 데이터를 자동으로 만들어주는 도우미 함수
def generate_forecast_data(start_date):
    """시나리오 2, 3, 4를 위해 10일치(20개) 예보 데이터를 생성"""
    daily = []
    twice_daily = []
    for i in range(10):
        day = start_date + timedelta(days=i)
        d_str = day.strftime("%Y-%m-%dT00:00:00Z")
        n_str = day.strftime("%Y-%m-%dT12:00:00Z")
        
        # 일별 예보
        daily.append({"datetime": d_str, "temperature": 25-i, "templow": 15-i, "condition": "sunny"})
        # 오전/오후 예보 (총 20개)
        twice_daily.append({"datetime": d_str, "is_daytime": True, "condition": "sunny", "temp": 25-i, "pop": 10})
        twice_daily.append({"datetime": n_str, "is_daytime": False, "condition": "clear-night", "temp": 18-i, "pop": 20})
    return daily, twice_daily

# 3. 모든 시나리오를 포함한 통합 Mock 데이터
now = dt_util.now()
daily_list, twice_daily_list = generate_forecast_data(now)

MOCK_SCENARIOS = {
    # [시나리오 1~6용] 모든 데이터가 완벽한 상태
    "full_test": {
        "weather": {
            "TMP": 22.5, "REH": 45, "WSD": 2.1, 
            "VEC": 150, 
            "VEC_KOR": "남동",              # [추가] 풍향 센서가 참조하는 키
            "POP": 10, "PTY": 0, "SKY": 1,
            "current_condition_kor": "맑음", # [추가] 현재날씨 센서가 참조하는 키
            "current_condition": "sunny",   # [추가] weather 엔티티가 참조하는 키
            "TMX_today": 25, "TMN_today": 15,
            "wf_am_today": "맑음", "wf_pm_today": "구름많음",
            "TMX_tomorrow": 26, "TMN_tomorrow": 16,
            "wf_am_tomorrow": "흐림", "wf_pm_tomorrow": "맑음",
            "apparent_temp": 23.4, 
            "rain_start_time": "강수없음",
            "address": "경기도 화성시", "현재 위치": "경기도 화성시",
            "forecast_daily": daily_list,
            "forecast_twice_daily": twice_daily_list,
        },
        "air": {
            "station": "화성", 
            "pm10Value": 35, "pm10Grade": "좋음", # [수정] pm10 -> pm10Value, pm10Grade
            "pm25Value": 15, "pm25Grade": "좋음"  # [수정] pm25 -> pm25Value, pm25Grade
        }
    },
    # [시나리오 2] 부산 (비 상황)
    "busan_rain": {
        "weather": {
            "TMP": "15.0", "POP": "80", "PTY": "1", "address": "부산광역시", "현재 위치": "부산광역시",
            "forecast_twice_daily": twice_daily_list
        },
        "air": {"station": "해운대", "pm10": "150", "pm10Grade": "4"}
    },
    # [시나리오 7] 제주 (데이터 일부 누락)
    "jeju_missing": {
        "weather": {"TMP": None, "address": "제주시", "현재 위치": "제주시", "forecast_twice_daily": []},
        "air": {}
    }
}

# 4. 공용 Fixtures
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
            mock_fetch.side_effect = Exception("API Server Error")
        else:
            mock_fetch.return_value = MOCK_SCENARIOS.get(scenario_name)
        return mock_fetch
    yield _create_mock
    patch.stopall()
