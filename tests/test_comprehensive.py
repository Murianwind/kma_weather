"""
tests/test_comprehensive.py
커버리지 보고서의 모든 누락 라인을 타격하며, 실제 소스코드 로직과 100% 일치하는 테스트입니다.
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
    """KMAWeatherAPI(session, api_key, hass) 실제 생성자 준수"""
    return KMAWeatherAPI(MagicMock(), "test_api_key", hass)

# =====================================================================
# [Part 1] config_flow.py : 설정 플로우 및 API 검증
# =====================================================================

@pytest.mark.asyncio
async def test_config_flow_complete(hass: HomeAssistant, aioclient_mock):
    """[TC 1] API 검증 및 UI 설정 성공 시나리오"""
    url = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    aioclient_mock.get(url, status=200, json={"response": {"header": {"resultCode": "00"}}})
    assert await _validate_api_key(hass, "valid_key") is None
    
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
# [Part 2] __init__.py : 지오코딩 및 천문 서비스 (159-211 라인)
# =====================================================================

@pytest.mark.asyncio
async def test_init_and_services(hass: HomeAssistant, aioclient_mock):
    """[TC 2] 지오코딩 및 천문 서비스 예외(한국 밖, 미등록) 타격"""
    # 2-1. 지오코딩 실패
    aioclient_mock.get("https://nominatim.openstreetmap.org/search", status=500)
    lat, _, _ = await _geocode_ko(hass, "에러")
    assert lat is None

    # 2-2. 서비스 핸들러 (통합 구성요소 미등록 에러 - Line 185)
    call = MagicMock(spec=ServiceCall); call.hass = hass
    hass.data[DOMAIN] = {} 
    with patch("custom_components.kma_weather.__init__._geocode_ko", return_value=(37.5, 126.9, "서울")), \
         patch("custom_components.kma_weather.__init__.is_korean_coord_strict", return_value=True):
        call.data = {"address": "서울", "date": datetime.now().date()}
        with pytest.raises(HomeAssistantError, match="통합 구성요소가 등록되지 않았습니다"):
            await _handle_get_astronomical_info(call)

# =====================================================================
# [Part 3] api_kma.py : 3-1 ~ 3-11 전 구간 완벽 복구
# =====================================================================

@pytest.mark.asyncio
async def test_api_3_1_file_error(mock_api):
    """[TC 3-1] 파일 로드 실패 대응"""
    with patch("builtins.open", side_effect=FileNotFoundError):
        mock_api._load_pollen_area_map()
        assert mock_api._pollen_area_data is None

@pytest.mark.asyncio
async def test_api_3_2_xml_and_404(mock_api):
    """[TC 3-2] XML 파싱 및 401 에러 대응 (Line 267)"""
    mock_api._parse_xml_to_dict = MagicMock(return_value={"response": {"header": {"resultCode": "22"}}})
    with patch.object(mock_api.session, "get") as mock_get:
        resp = AsyncMock(); resp.status = 401
        mock_get.return_value.__aenter__.return_value = resp
        res = await mock_api._fetch("http://test", {})
        assert res["_http_error"] == "401"

@pytest.mark.asyncio
async def test_api_3_3_midterm_tuple(mock_api):
    """[TC 3-3] 중기예보 튜플 구조 검증 (Line 499)"""
    land = {"response": {"body": {"items": {"item": [{"wf3Am": "맑음"}]}}}}
    temp = {"response": {"body": {"items": {"item": [{"taMin3": 10}]}}}}
    with patch.object(mock_api, "_fetch", side_effect=[land, temp]):
        res = await mock_api._get_mid_term(datetime.now(), "r1", "r2")
        assert isinstance(res, tuple)

@pytest.mark.asyncio
async def test_api_3_4_air_unsubscribed(mock_api):
    """[TC 3-4] 미신청 API 빠른 반환 (Line 519)"""
    mock_api._notified_unsubscribed.add("air")
    mock_api._cached_station = "종로구"
    with patch.object(mock_api, "_fetch", return_value={"response": {"header": {"resultCode": "22"}}}):
        res = await mock_api._get_air_quality(37.5, 126.9)
        assert res == {"station": "종로구"}

@pytest.mark.asyncio
async def test_api_3_5_short_term_approved(mock_api):
    """[TC 3-5] 단기예보 승인 마킹 (Line 567)"""
    mock_api.nx, mock_api.ny = 60, 127
    with patch.object(mock_api, "_fetch", return_value={"response": {"body": {"items": {"item": [{"t": 1}]}}}}):
        await mock_api._get_short_term(datetime.now())
        assert "short" in mock_api._approved_apis

@pytest.mark.asyncio
async def test_api_3_6_7_warning_logic(mock_api):
    """[TC 3-6, 3-7] 특보 데이터 부재 및 에러 방어"""
    with patch.object(mock_api, "_fetch", return_value={"response": {"body": {"items": {"item": []}}}}):
        assert await mock_api._get_warning("L101") == "특보없음"
    with patch.object(mock_api, "_fetch", side_effect=Exception):
        assert await mock_api._get_warning("L101") is None

@pytest.mark.asyncio
async def test_api_3_8_pollen_gather_exception(mock_api):
    """[TC 3-8] 꽃가루 gather 중 예외 발생 시 캐시 반환 (Line 775)"""
    dt_on = datetime(2025, 5, 1, 10, 0)
    mock_api._pollen_today = {"worst": "나쁨"}
    with patch("asyncio.gather", side_effect=Exception):
        res = await mock_api._get_pollen(dt_on, 37.5, 126.9)
        assert res["worst"] == "나쁨"

@pytest.mark.asyncio
async def test_api_3_9_pollen_grade_99_fix(mock_api):
    """[TC 3-9] _get_grade rc='99' 분기 타격 (Line 710) 및 KeyError 방지"""
    mock_api._approved_apis.add("pollen")
    dt_on = datetime(2025, 5, 1, 10, 0)
    # pine_data가 '00'이어야 함수가 중단되지 않고 oak_data('99')를 처리함
    pine_ok = {"response": {"header": {"resultCode": "00"}}}
    oak_99 = {"response": {"header": {"resultCode": "99"}}}
    with patch("asyncio.gather", return_value=(pine_ok, oak_99, pine_ok)):
        with patch.object(mock_api, "_find_pollen_area", return_value=("110", "서울")):
            res = await mock_api._get_pollen(dt_on, 37.5, 126.9)
            assert res["oak"] == "좋음" # rc=99는 '좋음' 반환

@pytest.mark.asyncio
async def test_api_3_10_merge_past_data_nested(mock_api):
    """[TC 3-10] 오늘 데이터 부재 시 과거 데이터 사용 (Line 884)"""
    now = datetime.now()
    past = (now - timedelta(days=1)).strftime("%Y%m%d")
    mock_api._cache_forecast_map = {past: {"2300": {"TMP": "25"}}}
    res = mock_api._merge_all(now, {}, {}, {}, address="서울")
    assert res["weather"]["TMP"] == "25"

def test_api_3_11_translation_priority_fix(mock_api):
    """[TC 3-11] 기상 상태 치환 우선순위 (소나기 > 비)"""
    # 소스코드 로직: '소나기' 체크가 '비' 체크보다 먼저 수행됨
    assert mock_api._translate_mid_condition_kor("소나기를 동반한 비") == "소나기"
    assert mock_api._translate_mid_condition_kor("흐리고 비") == "흐리고 비"
    assert mock_api._translate_mid_condition_kor("매우 흐림") == "흐림"
