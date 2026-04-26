"""
tests/test_comprehensive.py
커버리지가 취약했던 config_flow.py와 __init__.py의 핵심 로직을 하나의 파일에서 통합 검증합니다.
* 적용 기법: BDD (Given-When-Then), 동치 클래스 분할(ECP), 경계값 분석(BVA), 상태 전이 테스팅
* 테스트 대상: API 키 검증, Config/Options Flow, 천문 서비스, Nominatim 지오코딩, 생명주기
"""
import pytest
from datetime import time, datetime, timedelta
from unittest.mock import patch, AsyncMock, MagicMock

from homeassistant import data_entry_flow
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import ServiceCall, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kma_weather.const import (
    DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY, CONF_PREFIX
)
from custom_components.kma_weather.config_flow import _validate_api_key
from custom_components.kma_weather.__init__ import (
    async_setup_entry,
    _parse_time_str,
    _geocode_ko,
    _handle_get_astronomical_info
)


# =====================================================================
# [Part 1] config_flow.py : API 키 검증 로직 (_validate_api_key)
# =====================================================================

@pytest.mark.asyncio
async def test_validate_api_key_success(hass: HomeAssistant, aioclient_mock):
    """
    [TC 1-1] 유효한 API 키 검증 (정상 동치 클래스)
    Given: 기상청 API 서버가 정상 코드("00")를 반환하도록 모킹
    When: _validate_api_key 함수 호출
    Then: 에러 없이 None을 반환해야 함
    """
    aioclient_mock.get(
        "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
        status=200,
        json={"response": {"header": {"resultCode": "00"}}}
    )
    result = await _validate_api_key(hass, "valid_test_key")
    assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result_code, expected_error",
    [
        ("30", "invalid_api_key"),      # 미등록 키
        ("31", "invalid_api_key"),      # 만료된 키
        ("22", "api_quota_exceeded"),   # 한도 초과
        ("20", "api_access_denied"),    # 권한 없음
        ("32", "api_access_denied"),    # 트래픽 초과
        ("99", "api_error"),            # 기타 오류
    ]
)
async def test_validate_api_key_error_codes(hass: HomeAssistant, aioclient_mock, result_code, expected_error):
    """
    [TC 1-2] API 에러 코드 반환 검증 (오류 동치 클래스 분할)
    Given: 기상청 서버가 특정 에러 코드를 반환
    When: 키 검증 수행
    Then: 각 코드에 매핑된 정확한 에러 식별자가 반환되어야 함
    """
    aioclient_mock.get(
        "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
        status=200,
        json={"response": {"header": {"resultCode": result_code}}}
    )
    result = await _validate_api_key(hass, "error_test_key")
    assert result == expected_error


@pytest.mark.asyncio
async def test_validate_api_key_network_error(hass: HomeAssistant, aioclient_mock):
    """
    [TC 1-3] 통신 오류 및 예외 처리 검증 (예외 도메인)
    """
    # 1. HTTP 500 에러 반환 시
    aioclient_mock.get(
        "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
        status=500
    )
    assert await _validate_api_key(hass, "timeout_key") == "cannot_connect"

    # 2. aiohttp 세션 자체에서 파이썬 Exception 발생 시
    with patch("custom_components.kma_weather.config_flow.aiohttp_client.async_get_clientsession", side_effect=Exception("Network Down")):
        assert await _validate_api_key(hass, "timeout_key") == "cannot_connect"


# =====================================================================
# [Part 2] config_flow.py : Config / Options Flow UI 흐름 검증
# =====================================================================

@pytest.mark.asyncio
async def test_config_flow_success_with_entity_name(hass: HomeAssistant):
    """
    [TC 2-1] 정상적인 UI 설정 흐름 및 동적 이름 생성 로직
    Given: HA 코어에 특정 zone 엔티티 등록 (이름: 스위트홈)
    When: 유효한 입력값 제출
    Then: Entry가 생성되며, 제목이 zone의 friendly_name을 반영해야 함
    """
    hass.states.async_set("zone.home", "zoning", {"friendly_name": "스위트홈"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    with patch("custom_components.kma_weather.config_flow._validate_api_key", return_value=None), \
         patch("custom_components.kma_weather.async_setup_entry", return_value=True):
        
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "valid_key", CONF_PREFIX: "my_weather", CONF_LOCATION_ENTITY: "zone.home"},
        )

    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result2["title"] == "기상청 날씨: 스위트홈"


@pytest.mark.asyncio
async def test_options_flow(hass: HomeAssistant):
    """
    [TC 2-2] 옵션 변경 폼(Options Flow) 제출 검증
    """
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: "key", CONF_PREFIX: "pre", CONF_LOCATION_ENTITY: "zone.home"},
        options={}
    )
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] == data_entry_flow.FlowResultType.FORM

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_LOCATION_ENTITY: "zone.work"}
    )
    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result2["data"][CONF_LOCATION_ENTITY] == "zone.work"


# =====================================================================
# [Part 3] __init__.py : 유틸리티 및 지오코딩 서비스 검증
# =====================================================================

def test_parse_time_str():
    """
    [TC 3-1] 시각 문자열 파싱 및 예외 처리
    Given: 다양한 형태의 시각 문자열
    When: 파싱을 시도하면
    Then: 올바른 time 객체를 반환하거나 에러를 발생시킨다.
    """
    assert _parse_time_str("09:30") == time(9, 30)
    
    with pytest.raises(HomeAssistantError, match="시각을 입력해주세요"):
        _parse_time_str("")
        
    with pytest.raises(HomeAssistantError, match="시각 형식이 올바르지 않습니다"):
        _parse_time_str("25:00")


@pytest.mark.asyncio
async def test_geocode_ko_success(hass: HomeAssistant):
    """
    [TC 3-2] Nominatim 지오코딩 정상 통신
    """
    mock_resp = AsyncMock()
    mock_resp.json.return_value = [{"lat": "37.5665", "lon": "126.9780", "display_name": "서울시청"}]
    mock_session = AsyncMock()
    mock_session.get.return_value.__aenter__.return_value = mock_resp

    with patch("custom_components.kma_weather.__init__.async_get_clientsession", return_value=mock_session):
        lat, lon, name = await _geocode_ko(hass, "서울시청")
        
    assert lat == 37.5665
    assert lon == 126.9780
    assert name == "서울시청"


@pytest.mark.asyncio
async def test_geocode_ko_failure(hass: HomeAssistant):
    """
    [TC 3-3] 지오코딩 예외 (네트워크 에러) 처리
    """
    mock_session = AsyncMock()
    mock_session.get.return_value.__aenter__.side_effect = Exception("Connection Error")

    with patch("custom_components.kma_weather.__init__.async_get_clientsession", return_value=mock_session):
        lat, lon, name = await _geocode_ko(hass, "존재하지않는주소")
        
    assert (lat, lon, name) == (None, None, None)


# =====================================================================
# [Part 4] __init__.py : 천문 서비스 및 라이프사이클 예외 테스트
# =====================================================================

@pytest.fixture
def mock_call():
    call = MagicMock(spec=ServiceCall)
    call.hass = MagicMock(spec=HomeAssistant)
    call.hass.data = {DOMAIN: {}}
    return call


@pytest.mark.asyncio
async def test_astro_info_invalid_dates(mock_call):
    """
    [TC 4-1] 천문 서비스: 날짜 경계값(과거/4일초과) 예외 처리
    """
    today = datetime.now().date()
    
    # [경계값 1] 과거 날짜
    mock_call.data = {"address": "서울", "date": today - timedelta(days=1)}
    with pytest.raises(HomeAssistantError, match="과거 날짜는 조회할 수 없습니다"):
        await _handle_get_astronomical_info(mock_call)

    # [경계값 2] 4일 초과
    mock_call.data = {"address": "서울", "date": today + timedelta(days=5)}
    with pytest.raises(HomeAssistantError, match="4일 이후 날짜는 조회할 수 없습니다"):
        await _handle_get_astronomical_info(mock_call)


@pytest.mark.asyncio
@patch("custom_components.kma_weather.__init__._geocode_ko", return_value=(37.5, 126.9, "서울"))
async def test_astro_info_missing_coordinator(mock_geocode, mock_call):
    """
    [TC 4-2] 천문 서비스: 컴포넌트 미등록 예외
    """
    mock_call.data = {"address": "서울", "date": datetime.now().date()}
    mock_call.hass.data = {DOMAIN: {}} # 빈 코디네이터 공간
    
    with pytest.raises(HomeAssistantError, match="KMA Weather 통합 구성요소가 등록되지 않았습니다"):
        await _handle_get_astronomical_info(mock_call)


@pytest.mark.asyncio
async def test_async_setup_entry_existing_domain(hass: HomeAssistant):
    """
    [TC 4-3] 생명주기: DOMAIN 초기화 분기 커버
    Given: hass.data에 DOMAIN 키가 이미 존재할 때
    When: async_setup_entry를 호출하면
    Then: 딕셔너리를 덮어쓰지 않고 셋업을 완료해야 함
    """
    hass.data[DOMAIN] = {"existing_data": True}
    config_entry = MockConfigEntry(domain=DOMAIN, data={})

    with patch("custom_components.kma_weather.__init__.KMAWeatherUpdateCoordinator") as mock_coord:
        mock_coord.return_value.async_config_entry_first_refresh = AsyncMock()
        result = await async_setup_entry(hass, config_entry)

    assert result is True
    assert hass.data[DOMAIN]["existing_data"] is True
