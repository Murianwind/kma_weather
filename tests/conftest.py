import pytest
from unittest.mock import patch
from pytest_homeassistant_custom_component.common import MockConfigEntry

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """테스트 시 커스텀 컴포넌트를 HA가 인식하도록 허용"""
    yield

# ==========================================
# 4가지 지역별 가상 API 데이터 (Mock Data)
# ==========================================
MOCK_SCENARIOS = {
    # 1. 정상 (서울): 맑고 미세먼지 좋음
    "seoul_normal": {
        "weather": {
            "TMP": "22.5", "REH": "45", "WSD": "2.1", "VEC": "150", 
            "POP": "0", "PTY": "0", "SKY": "1",
            "TMX_today": "25.0", "TMN_today": "15.0", 
            "TMX_tomorrow": "26.0", "TMN_tomorrow": "16.0",
            "wf_am_today": "맑음", "wf_pm_today": "맑음", 
            "apparent_temp": "23.0", "rain_start_time": "강수없음",
            "address": "서울특별시 강남구", "현재 위치": "서울특별시 강남구",
            "forecast_twice_daily": []
        },
        "air": {"station": "강남구", "pm10": "30", "pm25": "15", "pm10Grade": "1", "pm25Grade": "1"}
    },
    
    # 2. 정상 (부산): 비가 오고 미세먼지 나쁨
    "busan_rain": {
        "weather": {
            "TMP": "15.0", "REH": "90", "WSD": "5.5", "VEC": "200", 
            "POP": "80", "PTY": "1", "SKY": "4",
            "TMX_today": "18.0", "TMN_today": "13.0", 
            "wf_am_today": "흐리고 비", "wf_pm_today": "흐림",
            "apparent_temp": "12.5", "rain_start_time": "15:00",
            "address": "부산광역시 해운대구", "현재 위치": "부산광역시 해운대구",
            "forecast_twice_daily": []
        },
        "air": {"station": "해운대구", "pm10": "150", "pm25": "80", "pm10Grade": "4", "pm25Grade": "4"}
    },

    # 3. 비정상 (제주): 기상청 온도 누락, 에어코리아 점검 중(응답 없음)
    "jeju_abnormal": {
        "weather": {
            "TMP": None, "REH": "60", "WSD": None, "VEC": "0", # 핵심 데이터 누락
            "POP": "10", "PTY": "0", "SKY": "1",
            "apparent_temp": None, "rain_start_time": "강수없음",
            "address": "제주특별자치도 서귀포시", "현재 위치": "제주특별자치도 서귀포시",
            "forecast_twice_daily": []
        },
        "air": {} # 에어코리아 데이터 아예 없음
    }
}

# ==========================================
# Fixtures (테스트 코드에서 가져다 쓸 수 있는 도구)
# ==========================================

@pytest.fixture
def mock_config_entry():
    """기본 설정 엔트리"""
    return MockConfigEntry(
        domain="kma_weather",
        data={"api_key": "test_mock_key", "location_mode": "zone", "prefix": "test"},
        entry_id="mock_id_001"
    )

@pytest.fixture
def kma_api_mock_factory():
    """
    테스트 케이스에서 원하는 시나리오("seoul_normal", "error" 등)를 
    입력하면 그에 맞춰 API 반환값을 조작해 주는 팩토리 함수
    """
    def _create_mock(scenario_name):
        mock_fetch = patch("custom_components.kma_weather.api_kma.KMAWeatherAPI.fetch_data").start()
        
        if scenario_name == "error":
            # 4. 완전 장애 (독도 등): 기상청 서버 500 에러 발생 상황
            mock_fetch.side_effect = Exception("API Server Timeout (HTTP 500)")
        else:
            mock_fetch.return_value = MOCK_SCENARIOS[scenario_name]
            
        return mock_fetch

    yield _create_mock
    patch.stopall() # 테스트가 끝나면 원래대로 복구

@pytest.fixture(autouse=True)
async def shutdown_executor(hass):
    """테스트 후 HA executor를 명시적으로 종료해 잔여 스레드 방지"""
    yield
    # executor 종료를 기다림
    if hass.loop and not hass.loop.is_closed():
        await hass.loop.shutdown_default_executor()
