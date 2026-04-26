"""
tests/test_comprehensive.py
config_flow.py의 _validate_api_key, ConfigFlow, OptionsFlow에 대한 단위 테스트입니다.
* BDD (Given-When-Then) 패턴을 적용했습니다.
* aioclient_mock을 사용하여 실제 _validate_api_key 내부의 HTTP 통신 라인을 실행합니다.
"""
import pytest
from unittest.mock import patch
from homeassistant import data_entry_flow
from homeassistant.config_entries import SOURCE_USER
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kma_weather.const import (
    DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY, CONF_PREFIX,
    CONF_APPLY_DATE, CONF_EXPIRE_DATE
)
from custom_components.kma_weather.config_flow import _validate_api_key

# =====================================================================
# 1. API 키 검증 로직 (_validate_api_key) 
# 적용 기법: 동치 클래스 분할 (Equivalence Partitioning) 및 경계값 분석
# =====================================================================

@pytest.mark.asyncio
async def test_validate_api_key_success(hass, aioclient_mock):
    """
    [TC 1-1] 유효한 API 키 검증 (정상 동치 클래스)
    """
    # Given: 기상청 API 서버가 정상 코드("00")를 반환하도록 모킹
    aioclient_mock.get(
        "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
        status=200,
        json={"response": {"header": {"resultCode": "00"}}}
    )

    # When: _validate_api_key 함수 호출
    result = await _validate_api_key(hass, "valid_test_key")

    # Then: 에러 없이 None을 반환해야 함
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
        ("99", "api_error"),            # 기타/알 수 없는 오류
    ]
)
async def test_validate_api_key_error_codes(hass, aioclient_mock, result_code, expected_error):
    """
    [TC 1-2] API 에러 코드 반환 검증 (오류 동치 클래스 분할)
    """
    # Given: 기상청 서버가 다양한 에러 코드를 반환
    aioclient_mock.get(
        "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
        status=200,
        json={"response": {"header": {"resultCode": result_code}}}
    )

    # When: 키 검증 수행
    result = await _validate_api_key(hass, "error_test_key")

    # Then: 각 코드에 매핑된 정확한 에러 식별자가 반환되어야 함
    assert result == expected_error


@pytest.mark.asyncio
async def test_validate_api_key_network_error(hass, aioclient_mock):
    """
    [TC 1-3] 통신 오류 및 예외 처리 검증 (예외 도메인)
    """
    # Given 1: API 서버가 HTTP 500 에러 반환
    aioclient_mock.get(
        "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
        status=500
    )
    # When 1 & Then 1: HTTP 에러 시 cannot_connect 반환
    result_http = await _validate_api_key(hass, "timeout_key")
    assert result_http == "cannot_connect"

    # Given 2: aiohttp session 자체에서 파이썬 Exception 발생
    with patch("custom_components.kma_weather.config_flow.aiohttp_client.async_get_clientsession", side_effect=Exception("Network Down")):
        # When 2 & Then 2: 예외 발생 시 Exception 블록을 타고 cannot_connect 반환
        result_exc = await _validate_api_key(hass, "timeout_key")
        assert result_exc == "cannot_connect"


# =====================================================================
# 2. Config Flow 흐름 및 동적 이름 생성 로직 검증
# 적용 기법: 상태 전이 (State Transition)
# =====================================================================

@pytest.mark.asyncio
async def test_config_flow_success_with_entity_name(hass):
    """
    [TC 2-1] 정상적인 UI 설정 흐름 완료 및 엔티티 이름 추출 로직 검증
    """
    # Given: HA 코어에 특정 zone 엔티티 등록 (이름: 스위트홈)
    hass.states.async_set("zone.home", "zoning", {"friendly_name": "스위트홈"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    # When: 유효한 입력값 제출 (API 검증 통과 모킹)
    with patch("custom_components.kma_weather.config_flow._validate_api_key", return_value=None), \
         patch("custom_components.kma_weather.async_setup_entry", return_value=True):
        
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_API_KEY: "valid_key",
                CONF_PREFIX: "my_weather",
                CONF_LOCATION_ENTITY: "zone.home"
            },
        )

    # Then: Entry가 생성되어야 하며, 제목이 zone의 friendly_name을 따와야 함
    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result2["title"] == "기상청 날씨: 스위트홈"
    assert result2["data"][CONF_PREFIX] == "my_weather"


@pytest.mark.asyncio
async def test_config_flow_fallback_name(hass):
    """
    [TC 2-2] 엔티티가 없을 경우 폴백(Fallback) 이름 검증
    """
    # Given: 초기 설정 화면 진입
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    # When: 엔티티 없이 폼 제출
    with patch("custom_components.kma_weather.config_flow._validate_api_key", return_value=None), \
         patch("custom_components.kma_weather.async_setup_entry", return_value=True):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_API_KEY: "valid_key",
                CONF_PREFIX: "my_weather",
                # CONF_LOCATION_ENTITY 없음
            },
        )

    # Then: 기본 이름인 "우리집"이 제목에 사용되어야 함
    assert result2["title"] == "기상청 날씨: 우리집"


@pytest.mark.asyncio
async def test_config_flow_api_error_returns_form(hass):
    """
    [TC 2-3] API 검증 실패 시 폼 재출력 검증
    """
    # Given: 초기 설정 화면 진입
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    # When: API 키 검증이 에러 문자열을 반환하는 상황
    with patch("custom_components.kma_weather.config_flow._validate_api_key", return_value="invalid_api_key"):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "bad_key", CONF_PREFIX: "pre"}
        )

    # Then: 설정이 완료되지 않고, 해당 에러와 함께 폼(FORM)이 다시 나타나야 함
    assert result2["type"] == data_entry_flow.FlowResultType.FORM
    assert result2["errors"][CONF_API_KEY] == "invalid_api_key"


# =====================================================================
# 3. Options Flow 검증 (설정 변경 시나리오)
# =====================================================================

@pytest.mark.asyncio
async def test_options_flow(hass):
    """
    [TC 3-1] 옵션 변경 폼 제출 검증
    """
    # Given: 기존에 설정된 Entry 존재
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: "key", CONF_PREFIX: "pre", CONF_LOCATION_ENTITY: "zone.home"},
        options={}
    )
    config_entry.add_to_hass(hass)

    # When 1: 옵션 플로우 진입
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    
    # Then 1: 옵션 폼 표시
    assert result["type"] == data_entry_flow.FlowResultType.FORM

    # When 2: 새로운 값으로 옵션 폼 제출
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_LOCATION_ENTITY: "zone.work"}
    )

    # Then 2: 옵션이 성공적으로 갱신됨
    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result2["data"][CONF_LOCATION_ENTITY] == "zone.work"
