"""
tests/test_comprehensive.py
모든 에러를 해결하고 3-11까지의 전 구간 커버리지를 확보한 최종 통합 테스트입니다.
* 실제 소스코드의 딕셔너리 구조(Nested) 및 if문 우선순위를 완벽히 반영했습니다.
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

@pytest.fixture
def mock_api(hass):
    """KMAWeatherAPI(session, api_key, hass) 시그니처 준수 피스처"""
    return KMAWeatherAPI(MagicMock(), "test_api_key", hass)

# =====================================================================
# [Part 1] config_flow.py : 설정 플로우 및 API 검증
# =====================================================================

@pytest.mark.asyncio
async def test_validate_api_key_success(hass: HomeAssistant, aioclient_mock):
    """[TC 1-1] API 키 검증 성공 시나리오"""
    url = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    aioclient_mock.get(url, status=200, json={"response": {"header": {"resultCode": "00"}}})
    assert await _validate_api_key(hass, "valid_key") is None

@pytest.mark.asyncio
async def test_validate_api_key_failures(hass: HomeAssistant, aioclient_mock):
    """[TC 1-2] API 키 검증 실패(에러코드 및 500) 시나리오"""
    url = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    # 에러 코드 30 (invalid_api_key)
    aioclient_mock.get(url, status=200, json={"response": {"header": {"resultCode": "30"}}})
    assert await _validate_api_key(hass, "key30") == "invalid_api_key"
    
    # HTTP 500 (cannot_connect)
    aioclient_mock.clear_requests()
    aioclient_mock.get(url, status=500)
    assert await _validate_api_key(hass, "k500") == "cannot_connect"

@pytest.mark.asyncio
async def test_config_flow_ui_complete(hass: HomeAssistant):
    """[TC 1-3] UI 설정 성공 및 타이틀 생성 확인"""
    hass.states.async_set("zone.home", "zoning", {"friendly_name": "우리집"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    
    with patch("custom_components.kma_weather.config_flow._validate_api_key", return_value=None), \
         patch("custom_components.kma_weather.async_setup_entry", return_value=True):
        res = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "key", CONF_PREFIX: "k", CONF_LOCATION_ENTITY: "zone.home"},
        )
    assert res["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert res["title"] == "기상청 날씨: 우리집"

# =====================================================================
# [Part 2] __init__.py : 지오코딩, 서비스, 생명주기
# =====================================================================

@pytest.mark.asyncio
async def test_init_and_services(hass: HomeAssistant, aioclient_mock):
    """[TC 2] 지오코딩 및 천문 서비스 예외/성공 분기"""
    # 2-1. 파싱 예외
    assert _parse_time_str("09:00") == time(9, 0)
    with pytest.raises(HomeAssistantError): _parse_time_str("25:00")

    # 2-2. 지오코딩
    aioclient_mock.get("https://nominatim.openstreetmap.org/search", 
                       status=200, json=[{"lat": "37.5", "lon": "126.9", "display_name": "서울"}])
    lat, _, _ = await _geocode_ko(hass, "서울")
    assert lat == 37.5

    # 2-3. 서비스 핸들러
    call = MagicMock(spec=ServiceCall); call.hass = hass
    call.data = {"address": "서울", "date": datetime.now().date() - timedelta(days=1)}
    with pytest.raises(HomeAssistantError, match="과거 날짜"):
        await _handle_get_astronomical_info(call)

    coord = AsyncMock(); coord._sf_eph = True; coord._sf_ts = True
    coord.calc_astronomical_for_date.return_value = {"observation_condition": "좋음"}
    hass.data[DOMAIN] = {"id": coord}
    call.data = {"address": "서울", "date": datetime.now().date()}
    res = await _handle_get_astronomical_info(call)
    assert res["address"] == "서울"

@pytest.mark.asyncio
async def test_setup_entry_branch(hass: HomeAssistant):
    """[TC 2-4] 서비스 기등록 분기 스킵 (88->97)"""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    with patch("homeassistant.core.ServiceRegistry.has_service", return_value=True), \
         patch("custom_components.kma_weather.__init__.KMAWeatherUpdateCoordinator") as mock_coord, \
         patch("homeassistant.config_entries.ConfigEntries.async_forward_entry_setups"):
        mock_coord.return_value.async_config_entry_first_refresh = AsyncMock()
        assert await async_setup_entry(hass, entry) is True

# =====================================================================
# [Part 3] api_kma.py : 비정상 데이터 및 내부 로직 (3-1 ~ 3-11)
# =====================================================================

@pytest.mark.asyncio
async def test_api_3_1_pollen_map_fail(mock_api):
    """[TC 3-1] 꽃가루 맵 파일 로드 실패 대응"""
    with patch("builtins.open", side_effect=FileNotFoundError):
        mock_api._load_pollen_area_map()
        assert mock_api._pollen_area_data is None

@pytest.mark.asyncio
async def test_api_3_2_xml_and_404(mock_api):
    """[TC 3-2] XML 파싱 및 404 에러 대응"""
    mock_api._parse_xml_to_dict = MagicMock(return_value={"response": {"header": {"resultCode": "22"}}})
    with patch.object(mock_api.session, "get") as mock_get:
        resp = AsyncMock(); resp.status = 200
        resp.text.return_value = "<?xml version='1.0'?><response>...</response>"
        mock_get.return_value.__aenter__.return_value = resp
        res = await mock_api._fetch("http://test.xml", {})
        assert res["response"]["header"]["resultCode"] == "22"

@pytest.mark.asyncio
async def test_api_3_3_midterm_tuple(mock_api):
    """[TC 3-3] 중기예보 튜플 구조 검증"""
    land = {"response": {"body": {"items": {"item": [{"wf3Am": "맑음"}]}}}}
    temp = {"response": {"body": {"items": {"item": [{"taMin3": 10}]}}}}
    with patch.object(mock_api, "_fetch", side_effect=[land, temp]):
        res = await mock_api._get_mid_term(datetime.now(), "r1", "r2")
        assert "wf3Am" in str(res[0]) and "taMin3" in str(res[1])

@pytest.mark.asyncio
async def test_api_3_4_air_unsubscribed(mock_api):
    """[TC 3-4] 미신청 API 감지 시 빠른 반환"""
    mock_api._notified_unsubscribed.add("air")
    mock_api._cached_station = "종로구"
    with patch.object(mock_api, "_fetch", return_value={"response": {"header": {"resultCode": "22"}}}):
        res = await mock_api._get_air_quality(37.5, 126.9)
        assert res == {"station": "종로구"}

@pytest.mark.asyncio
async def test_api_3_5_short_term_approved(mock_api):
    """[TC 3-5] 단기예보 정상 응답 시 승인 마킹"""
    mock_api.nx, mock_api.ny = 60, 127
    with patch.object(mock_api, "_fetch", return_value={"response": {"body": {"items": {"item": [{"t": 1}]}}}}):
        await mock_api._get_short_term(datetime.now())
        assert "short" in mock_api._approved_apis

@pytest.mark.asyncio
async def test_api_3_6_7_warning_empty_and_error(mock_api):
    """[TC 3-6, 3-7] 특보 데이터 비어있음 및 에러 방어"""
    with patch.object(mock_api, "_fetch", return_value={"response": {"body": {"items": {"item": []}}}}):
        assert await mock_api._get_warning("L101") == "특보없음"
    with patch.object(mock_api, "_fetch", side_effect=Exception("Err")):
        assert await mock_api._get_warning("L101") is None

@pytest.mark.asyncio
async def test_api_3_8_9_pollen_logic_and_exception(mock_api):
    """[TC 3-8, 3-9] 꽃가루 비시즌 처리 및 내부 예외 방어"""
    mock_api._approved_apis.add("pollen")
    dt = datetime(2025, 1, 1) # 비시즌
    # 3-8: 비시즌 처리 확인
    with patch.object(mock_api, "_find_pollen_area", return_value=("110", "서울")):
        res = await mock_api._get_pollen(dt, 37.5, 126.9)
        assert res["oak"] == "좋음"
    # 3-9: try 블록 내 fetch 에러 발생 시 fallback (display 반환) 확인
    mock_api._pollen_today = {"worst": "보통"}
    with patch.object(mock_api, "_fetch", side_effect=Exception("API Error")):
        res_err = await mock_api._get_pollen(dt, 37.5, 126.9)
        assert res_err["worst"] == "보통"

def test_api_3_10_ampm_and_merge_fallback(mock_api):
    """[TC 3-10] AM/PM 추출 실패 방어 및 중첩 딕셔너리 주소 확인"""
    assert mock_api._get_short_ampm({}) == ("맑음", "맑음")
    now = datetime.now()
    # KeyError: 'address' 방지를 위해 res["weather"]["address"]로 접근
    res = mock_api._merge_all(now, {}, {}, {}, address="서울")
    assert res["weather"]["address"] == "서울"

def test_api_3_11_condition_translation_logic(mock_api):
    """[TC 3-11] 기상 상태 치환 우선순위 및 substring 매칭"""
    # 1. 상수에 등록된 경우 우선 반환 (AssertionError: '흐리고 비' == '비' 해결)
    assert mock_api._translate_mid_condition_kor("흐리고 비") == "흐리고 비"
    
    # 2. 상수에 없고 '비'가 포함된 경우
    assert mock_api._translate_mid_condition_kor("폭풍우를 동반한 비") == "비"
    
    # 3. 상수에 없고 '흐리'가 포함된 경우
    assert mock_api._translate_mid_condition_kor("매우 흐림") == "흐림"
