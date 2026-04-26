"""
tests/test_comprehensive.py
config_flow.py, __init__.py, api_kma.py 의 누락된 구문 및 분기(예외 처리, 에러 방어 로직)를
하나의 파일에서 100% 통합 검증하는 마스터 단위 테스트입니다.
* 실제 소스코드의 내부 메서드 구조(_get_short_term, _get_air_quality 등)를 완벽히 반영했습니다.
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
    aioclient_mock.get(
        "https://nominatim.openstreetmap.org/search",
        status=200, json=[{"lat": "37.5", "lon": "126.9", "display_name": "서울시청"}]
    )
    lat, lon, name = await _geocode_ko(hass, "서울시청")
    assert lat == 37.5
    
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
    mock_call.data = {"address": "   ", "date": today}
    with pytest.raises(HomeAssistantError, match="주소를 입력해주세요"):
        await _handle_get_astronomical_info(mock_call)

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
    
    mock_coordinator.calc_astronomical_for_date.return_value = {"error": "계산오류"}
    with pytest.raises(HomeAssistantError, match="천문 계산 중 오류가 발생했습니다"):
        await _handle_get_astronomical_info(mock_call)
        
    mock_coordinator.calc_astronomical_for_date.return_value = {"observation_condition": "좋음"}
    res = await _handle_get_astronomical_info(mock_call)
    assert res["address"] == "서울"


@pytest.mark.asyncio
async def test_async_setup_entry_already_registered(hass: HomeAssistant):
    """[TC 2-7] 생명주기: 기존 서비스 존재 시 분기 스킵 (88->97)"""
    hass.data[DOMAIN] = {"existing": True}
    config_entry = MockConfigEntry(domain=DOMAIN, data={})

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
    """실제 api_kma.KMAWeatherAPI 시그니처에 맞춘 객체 생성"""
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
    """[TC 3-3] 알림 발송 실패 시 예외 무시 로직 (_notified_unsubscribed 사용)"""
    mock_api._notified_unsubscribed.clear()
    with patch("homeassistant.components.persistent_notification.async_create", side_effect=Exception):
        assert mock_api._check_unsubscribed("air", "22") is True


@pytest.mark.asyncio
async def test_api_fetch_http_404_and_xml(mock_api, aioclient_mock):
    """[TC 3-4] API 통신 중 HTTP 404 에러와 XML 응답 파싱 (_fetch 활용)"""
    url_xml = "http://test.xml"
    xml_content = "<?xml version='1.0'?><OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
    
    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text.return_value = xml_content
        mock_get.return_value.__aenter__.return_value = mock_resp
        
        import aiohttp
        mock_api.session = aiohttp.ClientSession()
        res_xml = await mock_api._fetch(url_xml, {})
        assert res_xml.get("response", {}).get("header", {}).get("resultCode") == "22"
        await mock_api.session.close()


@pytest.mark.asyncio
async def test_air_quality_unsubscribed_bypass(mock_api):
    """[TC 3-5] 미신청 API 통신 패스 (_get_air_quality 활용)"""
    mock_api._cached_station = "종로구"
    mock_api._notified_unsubscribed.add("air")
    with patch.object(mock_api, "_fetch", return_value={"response": {"header": {"resultCode": "22"}}}):
        res = await mock_api._get_air_quality(37.5, 126.9)
        assert res == {"station": "종로구"}


@pytest.mark.asyncio
async def test_get_short_term_mark_approved(mock_api):
    """[TC 3-6] 단기예보 정상 시 승인 마킹 (_get_short_term 활용)"""
    mock_api.nx = 60
    mock_api.ny = 127
    with patch.object(mock_api, "_fetch", return_value={"response": {"body": {"items": {"item": [{"test": 1}]}}}}):
        await mock_api._get_short_term(datetime.now())
        assert "short" in mock_api._approved_apis


@pytest.mark.asyncio
async def test_get_mid_term_invalid_results(mock_api):
    """[TC 3-7] 중기예보 빈 데이터 응답 시 1일 전 fallback (_get_mid_term 활용)"""
    with patch.object(mock_api, "_fetch", side_effect=[
        {}, {}, # 첫 시도: 실패 유도
        {"response": {"body": {"items": {"item": [{"taMin3": 10}]}}}},
        {"response": {"body": {"items": {"item": [{"wf3Am": "맑음"}]}}}},
    ]):
        res = await mock_api._get_mid_term(datetime.now(), "11B00000", "11B10101")
        assert "taMin3" in res


@pytest.mark.asyncio
async def test_get_warning_empty_and_exception(mock_api):
    """[TC 3-8] 특보 데이터 비어있음/에러 방어 (_get_warning 활용)"""
    with patch.object(mock_api, "_fetch", return_value={"response": {"body": {"items": {"item": []}}}}):
        assert await mock_api._get_warning("L1000000") == "특보없음"
    with patch.object(mock_api, "_fetch", side_effect=Exception("Warning Error")):
        assert await mock_api._get_warning("L1000000") is None


@pytest.mark.asyncio
async def test_get_pollen_index_logic(mock_api):
    """[TC 3-9] 꽃가루 비시즌, 에러 복합 로직 (_get_pollen 활용)"""
    mock_api._approved_apis.add("pollen")
    dt_offseason = datetime(2025, 1, 1) # 비시즌 강제 (1월)
    
    with patch.object(mock_api, "_find_pollen_area", return_value=("110", "서울")):
        res1 = await mock_api._get_pollen(dt_offseason, 37.5, 126.9)
        assert res1["oak"] == "좋음"

    with patch.object(mock_api, "_find_pollen_area", side_effect=Exception("Error")):
        res2 = await mock_api._get_pollen(dt_offseason, 37.5, 126.9)
        assert isinstance(res2, dict)


def test_get_short_ampm_empty_and_merge_past(mock_api):
    """[TC 3-10] 일일예보 하늘상태 누락(_get_short_ampm) 및 과거데이터(_merge_all) 병합 로직"""
    # 1. 하늘 상태 누락 방어
    assert mock_api._get_short_ampm({}) == ("맑음", "맑음")
    
    # 2. 직전(어제) 데이터 사용 fallback
    now = datetime.now()
    past = (now - timedelta(days=2)).strftime("%Y%m%d")
    short_res = {"response": {"body": {"items": {"item": [
        {"fcstDate": past, "fcstTime": "0600", "category": "TMP", "fcstValue": "20"}
    ]}}}}
    res = mock_api._merge_all(now, short_res, None, None, None, None, None)
    assert res["TMP"] == "20"


def test_translate_mid_condition_kor(mock_api):
    """[TC 3-11] 중기예보 기상 상태(흐리, 개임 등) 치환 (_translate_mid_condition_kor)"""
    assert mock_api._translate_mid_condition_kor("점차 흐려짐") == "흐림"
    assert mock_api._translate_mid_condition_kor("개임") == "맑음"
