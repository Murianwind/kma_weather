import pytest
import logging
from unittest.mock import AsyncMock
from urllib.parse import quote
from custom_components.kma_weather.api_kma import KMAWeatherAPI

# --- 1. API 키 디코딩 검증 테스트 ---
def test_api_key_decoding():
    """URL 인코딩된 API 키가 KMAWeatherAPI 초기화 시 정상적으로 디코딩되는지 확인"""
    encoded_key = quote("test_secret_key!@#")
    api = KMAWeatherAPI(None, encoded_key, "TEMP", "LAND")
    
    assert api.api_key == "test_secret_key!@#"


# --- 2. HTTP 에러 및 Bare Exception 처리 검증 테스트 ---
@pytest.mark.asyncio
async def test_fetch_http_error(caplog):
    """API 호출 중 HTTP 에러(500 등) 발생 시 크래시 없이 None을 반환하고 로그를 남기는지 확인"""
    # aiohttp ClientSession Mocking
    session_mock = AsyncMock()
    response_mock = AsyncMock()
    
    # raise_for_status() 호출 시 HTTP 예외가 발생하도록 조작
    response_mock.raise_for_status.side_effect = Exception("HTTP 500 Internal Server Error")
    session_mock.get.return_value.__aenter__.return_value = response_mock

    api = KMAWeatherAPI(session_mock, "TEST_KEY", "TEMP", "LAND")
    
    # 로그를 캡처하여 에러 메시지가 기록되는지 확인 준비
    with caplog.at_level(logging.ERROR):
        result = await api._fetch("http://example.com", {})
    
    # 검증 1: 예외로 인해 프로그램이 죽지 않고 None을 반환해야 함
    assert result is None
    # 검증 2: _LOGGER.error를 통해 "API 호출 실패" 문자열이 기록되어야 함
    assert "API 호출 실패" in caplog.text


# --- 3. Nominatim User-Agent 포함 여부 검증 테스트 ---
@pytest.mark.asyncio
async def test_nominatim_user_agent():
    """OpenStreetMap 주소 변환 API 호출 시 헤더에 User-Agent가 정상적으로 포함되는지 확인"""
    session_mock = AsyncMock()
    response_mock = AsyncMock()
    
    # 주소 API의 정상적인 응답 Mocking
    response_mock.json.return_value = {
        "address": {"city": "서울특별시", "borough": "강남구"}
    }
    session_mock.get.return_value.__aenter__.return_value = response_mock

    api = KMAWeatherAPI(session_mock, "TEST_KEY", "TEMP", "LAND")
    
    # 주소 변환 실행 (서울 좌표)
    address = await api._get_address(37.56, 126.98)
    
    # 검증 1: 주소 파싱이 정상적으로 이루어졌는지 확인
    assert address == "서울특별시 강남구"
    
    # 검증 2: session.get이 호출될 때 headers가 전달되었고, 그 안에 User-Agent가 있는지 확인
    call_args = session_mock.get.call_args
    assert call_args is not None, "session.get 이 호출되지 않았습니다."
    
    _, kwargs = call_args
    assert "headers" in kwargs, "API 호출 시 headers가 누락되었습니다."
    assert "User-Agent" in kwargs["headers"], "headers에 User-Agent가 없습니다."
    assert "kma_weather" in kwargs["headers"]["User-Agent"], "User-Agent에 컴포넌트 정보가 누락되었습니다."
