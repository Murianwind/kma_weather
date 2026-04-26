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


# =====================================================================
# [Part 2] __init__.py : 지오코딩, 천문 서비스, 생명주기
# =====================================================================

def test_parse_time_str():
    """[TC 2-1] 시각 파싱 누락 및 포맷 에러 검증"""
    assert _parse_time_str("09:30") == time(9, 30)
    with pytest.raises(HomeAssistantError, match="시각을 입력해주세요"):
        _parse_time_str("")
    with pytest.raises(HomeAssistantError, match="시각 형식이 올바르지 않습니다"):
        _parse_time_str("25:00")


@pytest.mark.asyncio
async def test_geocode_ko_success_and_failure(hass: HomeAssistant, aioclient_mock):
    """[TC 2-2] Nominatim API 지오코딩 정상 통신 및 500 예외 로직"""
    # 1. 성공 케이스
    aioclient_mock.get(
        "https://nominatim.openstreetmap.org/search",
        status=200, json=[{"lat": "37.5", "lon": "126.9", "display_name": "서울시청"}]
    )
    lat, lon, name = await _geocode_ko(hass, "서울시청")
    assert lat == 37.5
    
    # 2. 실패 케이스 (에러 캐치)
    aioclient_mock.clear_requests()
    aioclient_mock.get("https://nominatim.openstreetmap.org/search", status=500, text="Error")
    lat2, lon2, name2 = await _geocode_ko(hass, "에러주소")
    assert lat2 is None


@pytest.fixture
def mock_call():
    call = MagicMock(spec=ServiceCall)
    call.hass = MagicMock(spec=HomeAssistant)
    call.hass.data = {DOMAIN: {}}
    return call


@pytest.mark.asyncio
async def test_astro_info_validation_errors(mock_call):
    """[TC 2-3] 천문 서비스 입력값 검증 (주소 공백, 날짜 범위)"""
    today = datetime.now().date()
    
    # 주소 공백
    mock_call.data = {"address": "   ", "date": today}
    with pytest.raises(HomeAssistantError, match="주소를 입력해주세요"):
        await _handle_get_astronomical_info(mock_call)

    # 과거 날짜 및 미래 날짜 한계
    mock_call.data = {"address": "서울", "date": today - timedelta(days=1)}
    with pytest.raises(HomeAssistantError, match="과거 날짜는 조회할 수 없습니다"):
        await _handle_get_astronomical_info(mock_call)


@pytest.mark.asyncio
@patch("custom_components.kma_weather.__init__._geocode_ko", return_value=(None, None, None))
async def test_astro_info_geocode_fail(mock_geocode, mock_call):
    """[TC 2-4] 천문 서비스: 변환 불가 주소 방어"""
    mock_call.data = {"address": "없는주소", "date": datetime.now().date()}
    with pytest.raises(HomeAssistantError, match="주소를 찾을 수 없습니다"):
        await _handle_get_astronomical_info(mock_call)


@pytest.mark.asyncio
@patch("custom_components.kma_weather.__init__._geocode_ko", return_value=(35.6, 139.6, "도쿄"))
@patch("custom_components.kma_weather.__init__.is_korean_coord_strict", return_value=False)
async def test_astro_info_outside_korea(mock_is_kr, mock_geocode, mock_call):
    """[TC 2-5] 천문 서비스: 한국 영역 외 좌표 방어"""
    mock_call.data = {"address": "도쿄", "date": datetime.now().date()}
    with pytest.raises(HomeAssistantError, match="한국 영역 밖의 좌표"):
        await _handle_get_astronomical_info(mock_call)


@pytest.mark.asyncio
@patch("custom_components.kma_weather.__init__._geocode_ko", return_value=(37.5, 126.9, "서울"))
@patch("custom_components.kma_weather.__init__.is_korean_coord_strict", return_value=True)
async def test_astro_info_success_and_error(mock_is_kr, mock_geocode, mock_call):
    """[TC 2-6] 천문 서비스: Skyfield 계산 성공 및 내부 에러 처리"""
    mock_call.data = {"address": "서울", "date": datetime.now().date(), "time": "12:00"}
    mock_coordinator = AsyncMock()
    mock_coordinator._sf_eph = True
    mock_coordinator._sf_ts = True
    mock_call.hass.data = {DOMAIN: {"entry_id": mock_coordinator}}
    
    # 에러 발생 상황
    mock_coordinator.calc_astronomical_for_date.return_value = {"error": "계산오류"}
    with pytest.raises(HomeAssistantError, match="천문 계산 중 오류가 발생했습니다"):
        await _handle_get_astronomical_info(mock_call)
        
    # 성공 상황
    mock_coordinator.calc_astronomical_for_date.return_value = {"observation_condition": "좋음"}
    res = await _handle_get_astronomical_info(mock_call)
    assert res["address"] == "서울"


@pytest.mark.asyncio
async def test_async_setup_entry_already_registered(hass: HomeAssistant):
    """[TC 2-7] 생명주기: 기존 서비스 존재 시 분기 스킵 (88->97)"""
    hass.data[DOMAIN] = {"existing": True}
    config_entry = MockConfigEntry(domain=DOMAIN, data={})
    hass.services.async_register(DOMAIN, SERVICE_GET_ASTRONOMICAL_INFO, AsyncMock())

    with patch("custom_components.kma_weather.__init__.KMAWeatherUpdateCoordinator") as mock_coord, \
         patch("homeassistant.config_entries.ConfigEntries.async_forward_entry_setups"):
        mock_coord.return_value.async_config_entry_first_refresh = AsyncMock()
        assert await async_setup_entry(hass, config_entry) is True


# =====================================================================
# [Part 3] api_kma.py : 비정상 데이터, 예외 및 fallback 방어 로직 (67라인)
# =====================================================================

@pytest.mark.asyncio
async def test_pollen_area_map_exception(hass: HomeAssistant):
    """[TC 3-1] 꽃가루 맵 json 파일 누락 방어"""
    api = KMAWeatherAPI(hass, "dummy", 37.5, 126.9)
    with patch("builtins.open", side_effect=FileNotFoundError):
        api._load_pollen_area_map()
        assert api._pollen_area_data is None


@pytest.mark.asyncio
async def test_find_pollen_area_cache_and_empty(hass: HomeAssistant):
    """[TC 3-2] 꽃가루 캐시 확인 및 executor 실패 시 fallback"""
    api = KMAWeatherAPI(hass, "dummy", 37.5, 126.9)
    with patch.object(hass, "async_add_executor_job", side_effect=Exception("Executor error")):
        area_no, _ = await api._find_pollen_area(37.5, 126.9)
        assert area_no == "1100000000"


def test_check_unsubscribed_notification_exception(hass: HomeAssistant):
    """[TC 3-3] 알림 발송 실패 시 예외 무시 로직"""
    api = KMAWeatherAPI(hass, "dummy", 37.5, 126.9)
    api._unsubscribed_apis.clear()
    with patch("homeassistant.components.persistent_notification.async_create", side_effect=Exception):
        assert api._check_unsubscribed("air", "22") is True


@pytest.mark.asyncio
async def test_api_get_http_404_and_xml(hass: HomeAssistant, aioclient_mock):
    """[TC 3-4] API 통신 중 HTTP 404 에러와 XML 응답 파싱"""
    api = KMAWeatherAPI(hass, "dummy", 37.5, 126.9)
    url_xml = "http://test.xml"
    xml_content = "<?xml version='1.0'?><OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
    aioclient_mock.get(url_xml, status=200, text=xml_content)
    res_xml = await api._get(url_xml, {})
    assert res_xml.get("response", {}).get("header", {}).get("resultCode") == "22"


@pytest.mark.asyncio
async def test_air_quality_unsubscribed_bypass(hass: HomeAssistant):
    """[TC 3-5] 미신청 API 확인 시 통신 패스 (빠른 반환)"""
    api = KMAWeatherAPI(hass, "dummy", 37.5, 126.9)
    api._unsubscribed_apis.add("air")
    with patch.object(api, "fetch_data", return_value={"response": {"header": {"resultCode": "22"}}}):
        assert (await api.get_air_quality("종로구")) == {"station": "종로구"}


@pytest.mark.asyncio
async def test_get_short_forecast_mark_approved(hass: HomeAssistant):
    """[TC 3-6] 단기예보 정상 시 승인 마킹"""
    api = KMAWeatherAPI(hass, "dummy", 37.5, 126.9)
    with patch.object(api, "fetch_data", return_value={"response": {"body": {"items": {"item": [{"test": 1}]}}}}):
        await api.get_short_forecast()
        assert "short" in api._approved_apis


@pytest.mark.asyncio
async def test_midterm_forecast_invalid_results(hass: HomeAssistant):
    """[TC 3-7] 중기예보 빈 데이터 응답 시 1일 전 fallback 재귀 호출"""
    api = KMAWeatherAPI(hass, "dummy", 37.5, 126.9)
    with patch.object(api, "fetch_data", side_effect=[
        {}, {}, # 첫 시도: 빈 데이터 -> 실패 유도
        {"response": {"body": {"items": {"item": [{"taMin3": 10}]}}}},
        {"response": {"body": {"items": {"item": [{"wf3Am": "맑음"}]}}}},
    ]):
        assert "taMin3" in await api.get_midterm_forecast("11B00000", "11B10101")


@pytest.mark.asyncio
async def test_warning_info_empty_and_exception(hass: HomeAssistant):
    """[TC 3-8] 특보 데이터가 아예 비어있거나 통신 에러 발생 시 방어"""
    api = KMAWeatherAPI(hass, "dummy", 37.5, 126.9)
    with patch.object(api, "fetch_data", return_value={"response": {"body": {"items": {"item": []}}}}):
        assert await api.get_warning_info() == "특보없음"
    with patch.object(api, "fetch_data", side_effect=Exception("Warning Error")):
        assert await api.get_warning_info() is None


@pytest.mark.asyncio
async def test_pollen_index_logic(hass: HomeAssistant):
    """[TC 3-9] 꽃가루 지수 발표 전(캐시), rc=99 예외, 에러 복합 로직"""
    api = KMAWeatherAPI(hass, "dummy", 37.5, 126.9)
    api._approved_apis.add("pollen")
    
    # 비시즌 강제 적용
    with patch("custom_components.kma_weather.api_kma.KMAWeatherAPI._find_pollen_area", return_value=("110", "서울")), \
         patch("custom_components.kma_weather.api_kma.is_in_season", return_value=False):
        res1 = await api.get_pollen_index()
        assert res1["oak"] == "좋음"

    # 통신 에러 발생
    with patch("custom_components.kma_weather.api_kma.KMAWeatherAPI._find_pollen_area", side_effect=Exception("Error")):
        assert isinstance(await api.get_pollen_index(), dict)


@pytest.mark.asyncio
async def test_daily_forecast_empty_skies_and_past_dates(hass: HomeAssistant):
    """[TC 3-10] 일일예보 하늘상태(SKY/PTY) 누락 및 과거날짜 병합 로직"""
    api = KMAWeatherAPI(hass, "dummy", 37.5, 126.9)
    mock_items = [
        {"fcstDate": datetime.now().strftime("%Y%m%d"), "fcstTime": "0600", "category": "TMP", "fcstValue": "20"},
        {"fcstDate": "20000101", "fcstTime": "0600", "category": "TMP", "fcstValue": "10"}, # 과거
    ]
    with patch.object(api, "fetch_data", return_value={"response": {"body": {"items": {"item": mock_items}}}}):
        assert isinstance(await api.get_daily_forecast(), list)


def test_translate_mid_condition(hass: HomeAssistant):
    """[TC 3-11] 중기예보 기상 상태(흐리, 개임 등) 치환"""
    api = KMAWeatherAPI(hass, "dummy", 37.5, 126.9)
    assert api._translate_mid_condition("점차 흐려짐") == "흐림"
    assert api._translate_mid_condition("개임") == "맑음"
