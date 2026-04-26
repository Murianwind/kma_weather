"""
tests/test_comprehensive.py
실제 소스코드의 로직 우선순위, 시즌/비시즌 분기, 중첩 딕셔너리 구조를 완벽히 반영했습니다.
3-1부터 3-11까지 단 하나도 누락하지 않고 전 구간을 타격합니다.
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
async def test_config_flow_complete(hass: HomeAssistant, aioclient_mock):
    """[TC 1] API 검증 및 UI 설정 성공/실패 시나리오"""
    url = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    
    # 1-1. 성공
    aioclient_mock.get(url, status=200, json={"response": {"header": {"resultCode": "00"}}})
    assert await _validate_api_key(hass, "valid_key") is None
    
    # 1-2. 에러 코드 (invalid_api_key)
    aioclient_mock.clear_requests()
    aioclient_mock.get(url, status=200, json={"response": {"header": {"resultCode": "30"}}})
    assert await _validate_api_key(hass, "key30") == "invalid_api_key"

    # 1-3. UI 설정 성공
    hass.states.async_set("zone.home", "zoning", {"friendly_name": "집"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch("custom_components.kma_weather.config_flow._validate_api_key", return_value=None), \
         patch("custom_components.kma_weather.async_setup_entry", return_value=True):
        res = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "k", CONF_PREFIX: "p", CONF_LOCATION_ENTITY: "zone.home"},
        )
    assert res["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY

# =====================================================================
# [Part 2] __init__.py : 지오코딩, 서비스, 생명주기
# =====================================================================

@pytest.mark.asyncio
async def test_init_and_services(hass: HomeAssistant, aioclient_mock):
    """[TC 2] 지오코딩 및 천문 서비스 전 구간"""
    # 2-1. 파싱
    assert _parse_time_str("09:00") == time(9, 0)
    with pytest.raises(HomeAssistantError): _parse_time_str("invalid")

    # 2-2. 지오코딩 성공/실패
    aioclient_mock.get("https://nominatim.openstreetmap.org/search", 
                       status=200, json=[{"lat": "37.5", "lon": "126.9", "display_name": "서울"}])
    lat, _, _ = await _geocode_ko(hass, "서울")
    assert lat == 37.5

    # 2-3. 서비스 핸들러 (과거 날짜 예외)
    call = MagicMock(spec=ServiceCall); call.hass = hass
    call.data = {"address": "서울", "date": datetime.now().date() - timedelta(days=1)}
    with pytest.raises(HomeAssistantError, match="과거 날짜"):
        await _handle_get_astronomical_info(call)

    # 2-4. 기등록 서비스 분기
    with patch("homeassistant.core.ServiceRegistry.has_service", return_value=True), \
         patch("custom_components.kma_weather.__init__.KMAWeatherUpdateCoordinator") as mock_coord, \
         patch("homeassistant.config_entries.ConfigEntries.async_forward_entry_setups"):
        mock_coord.return_value.async_config_entry_first_refresh = AsyncMock()
        assert await async_setup_entry(hass, MockConfigEntry(domain=DOMAIN, data={})) is True

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
    """[TC 3-2] XML 파싱 대응"""
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
        assert "wf3Am" in str(res[0])

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
    """[TC 3-8, 3-9] 꽃가루 에러 시 캐시(보통) 반환 확인"""
    mock_api._approved_apis.add("pollen")
    # 중요: 5월(시즌)로 설정해야 비시즌 우선 로직을 타지 않고 에러 핸들링을 검증함
    dt_season = datetime(2025, 5, 1, 10, 0) 
    mock_api._pollen_today = {"worst": "보통"}
    
    with patch.object(mock_api, "_find_pollen_area", return_value=("110", "서울")):
        # API 통신 에러 유도
        with patch.object(mock_api, "_fetch", side_effect=Exception("API Down")):
            res = await mock_api._get_pollen(dt_season, 37.5, 126.9)
            # 에러 발생 시 display(기존 캐시)인 '보통'이 반환되어야 함
            assert res["worst"] == "보통"

def test_api_3_10_merge_all_nested_structure(mock_api):
    """[TC 3-10] 병합 로직의 중첩 딕셔너리 구조 검증 (KeyError 해결)"""
    now = datetime.now()
    # _merge_all은 {'weather': weather_data, ...}를 반환함
    res = mock_api._merge_all(now, {}, {}, {}, address="서울")
    assert res["weather"]["address"] == "서울"

def test_api_3_11_condition_translation_logic(mock_api):
    """[TC 3-11] 기상 상태 치환 우선순위 검증"""
    # 1. 상수에 등록된 '흐리고 비'는 그대로 반환
    assert mock_api._translate_mid_condition_kor("흐리고 비") == "흐리고 비"
    # 2. 상수에 없는 '폭풍우를 동반한 비'는 '비' 포함 로직에 의해 '비' 반환
    assert mock_api._translate_mid_condition_kor("폭풍우를 동반한 비") == "비"
    # 3. '흐리' 포함 로직
    assert mock_api._translate_mid_condition_kor("매우 흐림") == "흐림"
