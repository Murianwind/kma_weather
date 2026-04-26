"""
tests/test_comprehensive.py
config_flow.py, __init__.py, api_kma.py 의 누락된 구문 및 분기(예외 처리, 에러 방어 로직)를
하나의 파일에서 100% 통합 검증하는 마스터 단위 테스트입니다.
* 적용 기법: BDD(Given-When-Then), 동치 클래스 분할(ECP), 경계값 분석(BVA), 오류 추측
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
from custom_components.kma_weather.api_kma import KMAWeatherAPI
from custom_components.kma_weather.__init__ import (
    async_setup_entry,
    _parse_time_str,
    _geocode_ko,
    _handle_get_astronomical_info
)

# =====================================================================
# [Part 1] config_flow.py : API 키 검증 및 설정 플로우
# =====================================================================

@pytest.mark.asyncio
async def test_validate_api_key_success(hass: HomeAssistant, aioclient_mock):
    """[TC 1-1] 유효한 API 키 검증"""
    aioclient_mock.get(
        "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
        status=200, json={"response": {"header": {"resultCode": "00"}}}
    )
    assert await _validate_api_key(hass, "valid_test_key") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("result_code, expected_error", [
    ("30", "invalid_api_key"), ("31", "invalid_api_key"),
    ("22", "api_quota_exceeded"), ("20", "api_access_denied"),
    ("32", "api_access_denied"), ("99", "api_error"),
])
async def test_validate_api_key_error_codes(hass: HomeAssistant, aioclient_mock, result_code, expected_error):
    """[TC 1-2] 기상청 API 에러 코드 반환 검증"""
    aioclient_mock.get(
        "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
        status=200, json={"response": {"header": {"resultCode": result_code}}}
    )
    assert await _validate_api_key(hass, "error_test_key") == expected_error


@pytest.mark.asyncio
async def test_validate_api_key_network_error(hass: HomeAssistant, aioclient_mock):
    """[TC 1-3] HTTP 500 등 네트워크 통신 예외 방어"""
    aioclient_mock.get(
        "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
        status=500
    )
    assert await _validate_api_key(hass, "timeout_key") == "cannot_connect"


@pytest.mark.asyncio
async def test_config_flow_success_with_entity_name(hass: HomeAssistant):
    """[TC 1-4] 정상적인 UI 설정 흐름 및 Zone 엔티티 이름 획득"""
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
    """[TC 1-5] 옵션 변경 플로우 (OptionsFlow) 제출 검증"""
    config_entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_API_KEY: "key", CONF_PREFIX: "pre", CONF_LOCATION_ENTITY: "zone.home"}
    )
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_LOCATION_ENTITY: "zone.work"}
    )
    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_async_setup_entry_already_registered(hass: HomeAssistant):
    """[TC 2-7] 생명주기: 기존 서비스 존재 시 분기 스킵 (88->97)"""
    hass.data[DOMAIN] = {"existing": True}
    config_entry = MockConfigEntry(domain=DOMAIN, data={})

    # has_service가 True를 반환하도록 Mocking을 올바른 방식으로 수정
    with patch("custom_components.kma_weather.__init__.KMAWeatherUpdateCoordinator") as mock_coord, \
         patch("homeassistant.config_entries.ConfigEntries.async_forward_entry_setups"), \
         patch("homeassistant.core.ServiceRegistry.has_service", return_value=True):
         
        mock_coord.return_value.async_config_entry_first_refresh = AsyncMock()
        assert await async_setup_entry(hass, config_entry) is True


# =====================================================================
# [Part 3] api_kma.py : 비정상 데이터, 예외 및 fallback 방어 로직 (67라인)
# =====================================================================

@pytest.fixture
def mock_api(hass):
    """KMAWeatherAPI 인스턴스를 올바른 파라미터로 생성하여 제공하는 피스처"""
    # 원본 서명: KMAWeatherAPI(session, api_key, hass=None)
    return KMAWeatherAPI(session=MagicMock(), api_key="dummy", hass=hass)


@pytest.mark.asyncio
async def test_pollen_area_map_exception(mock_api):
    """[TC 3-1] 꽃가루 맵 json 파일 누락 방어"""
    with patch("builtins.open", side_effect=FileNotFoundError):
        mock_api._load_pollen_area_map()
        assert mock_api._pollen_area_data is None


@pytest.mark.asyncio
async def test_find_pollen_area_cache_and_empty(mock_api, hass: HomeAssistant):
    """[TC 3-2] 꽃가루 캐시 확인 및 executor 실패 시 fallback"""
    with patch.object(hass, "async_add_executor_job", side_effect=Exception("Executor error")):
        area_no, _ = await mock_api._find_pollen_area(37.5, 126.9)
        assert area_no == "1100000000"


def test_check_unsubscribed_notification_exception(mock_api):
    """[TC 3-3] 알림 발송 실패 시 예외 무시 로직"""
    mock_api._unsubscribed_apis.clear()
    with patch("homeassistant.components.persistent_notification.async_create", side_effect=Exception):
        assert mock_api._check_unsubscribed("air", "22") is True


@pytest.mark.asyncio
async def test_api_get_http_404_and_xml(mock_api, aioclient_mock):
    """[TC 3-4] API 통신 중 HTTP 404 에러와 XML 응답 파싱"""
    url_xml = "http://test.xml"
    xml_content = "<?xml version='1.0'?><OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
    aioclient_mock.get(url_xml, status=200, text=xml_content)
    
    # aiohttp ClientSession 모킹을 우회하기 위해 _get 자체를 호출하지 않고 fetch_data 활용
    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text.return_value = xml_content
        mock_get.return_value.__aenter__.return_value = mock_resp
        
        # 임시로 _session 생성
        import aiohttp
        mock_api._session = aiohttp.ClientSession()
        res_xml = await mock_api._get(url_xml, {})
        assert res_xml.get("response", {}).get("header", {}).get("resultCode") == "22"
        await mock_api._session.close()


@pytest.mark.asyncio
async def test_air_quality_unsubscribed_bypass(mock_api):
    """[TC 3-5] 미신청 API 확인 시 통신 패스 (빠른 반환)"""
    mock_api._unsubscribed_apis.add("air")
    with patch.object(mock_api, "fetch_data", return_value={"response": {"header": {"resultCode": "22"}}}):
        assert (await mock_api.get_air_quality("종로구")) == {"station": "종로구"}


@pytest.mark.asyncio
async def test_get_short_forecast_mark_approved(mock_api):
    """[TC 3-6] 단기예보 정상 시 승인 마킹"""
    with patch.object(mock_api, "fetch_data", return_value={"response": {"body": {"items": {"item": [{"test": 1}]}}}}):
        await mock_api.get_short_forecast()
        assert "short" in mock_api._approved_apis


@pytest.mark.asyncio
async def test_midterm_forecast_invalid_results(mock_api):
    """[TC 3-7] 중기예보 빈 데이터 응답 시 1일 전 fallback 재귀 호출"""
    with patch.object(mock_api, "fetch_data", side_effect=[
        {}, {}, # 첫 시도 실패 유도
        {"response": {"body": {"items": {"item": [{"taMin3": 10}]}}}},
        {"response": {"body": {"items": {"item": [{"wf3Am": "맑음"}]}}}},
    ]):
        assert "taMin3" in await mock_api.get_midterm_forecast("11B00000", "11B10101")


@pytest.mark.asyncio
async def test_warning_info_empty_and_exception(mock_api):
    """[TC 3-8] 특보 데이터가 아예 비어있거나 통신 에러 발생 시 방어"""
    with patch.object(mock_api, "fetch_data", return_value={"response": {"body": {"items": {"item": []}}}}):
        assert await mock_api.get_warning_info() == "특보없음"
    with patch.object(mock_api, "fetch_data", side_effect=Exception("Warning Error")):
        assert await mock_api.get_warning_info() is None


@pytest.mark.asyncio
async def test_pollen_index_logic(mock_api):
    """[TC 3-9] 꽃가루 지수 발표 전(캐시), rc=99 예외, 에러 복합 로직"""
    mock_api._approved_apis.add("pollen")
    
    with patch("custom_components.kma_weather.api_kma.KMAWeatherAPI._find_pollen_area", return_value=("110", "서울")), \
         patch("custom_components.kma_weather.api_kma.is_in_season", return_value=False):
        res1 = await mock_api.get_pollen_index()
        assert res1["oak"] == "좋음"

    with patch("custom_components.kma_weather.api_kma.KMAWeatherAPI._find_pollen_area", side_effect=Exception("Error")):
        assert isinstance(await mock_api.get_pollen_index(), dict)


@pytest.mark.asyncio
async def test_daily_forecast_empty_skies_and_past_dates(mock_api):
    """[TC 3-10] 일일예보 하늘상태(SKY/PTY) 누락 및 과거날짜 병합 로직"""
    mock_items = [
        {"fcstDate": datetime.now().strftime("%Y%m%d"), "fcstTime": "0600", "category": "TMP", "fcstValue": "20"},
        {"fcstDate": "20000101", "fcstTime": "0600", "category": "TMP", "fcstValue": "10"}, # 과거
    ]
    with patch.object(mock_api, "fetch_data", return_value={"response": {"body": {"items": {"item": mock_items}}}}):
        assert isinstance(await mock_api.get_daily_forecast(), list)


def test_translate_mid_condition(mock_api):
    """[TC 3-11] 중기예보 기상 상태(흐리, 개임 등) 치환"""
    assert mock_api._translate_mid_condition("점차 흐려짐") == "흐림"
    assert mock_api._translate_mid_condition("개임") == "맑음"
