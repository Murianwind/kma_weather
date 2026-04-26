"""
tests/test_comprehensive.py
누락된 구문(api_kma 884, 775 등)과 모든 에러를 해결한 최종 통합본입니다.
* 3-11까지의 모든 케이스를 실제 소스코드 로직에 맞춰 완벽히 복구했습니다.
"""
import pytest
import asyncio
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
# [Part 1] config_flow.py : 79-88 라인 (에러 코드 분기)
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("res_code, expected", [
    ("22", "api_quota_exceeded"),
    ("20", "api_access_denied"),
    ("32", "api_access_denied"),
    ("99", "api_error"),
])
async def test_validate_api_key_all_codes(hass: HomeAssistant, aioclient_mock, res_code, expected):
    """[TC 1-1] 누락된 모든 resultCode 분기 타격"""
    url = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    aioclient_mock.get(url, status=200, json={"response": {"header": {"resultCode": res_code}}})
    assert await _validate_api_key(hass, "test_key") == expected

# =====================================================================
# [Part 2] __init__.py : 159-211 라인 (천문 서비스 예외 정조준)
# =====================================================================

@pytest.mark.asyncio
async def test_astro_service_exact_errors(hass: HomeAssistant):
    """[TC 2-1] 소스코드의 실제 에러 메시지 매칭 (Line 185, 190)"""
    call = MagicMock(spec=ServiceCall); call.hass = hass
    
    # 1. 한국 밖 좌표 (Line 180-184)
    with patch("custom_components.kma_weather.__init__._geocode_ko", return_value=(35.6, 139.6, "도쿄")), \
         patch("custom_components.kma_weather.__init__.is_korean_coord_strict", return_value=False):
        call.data = {"address": "도쿄", "date": datetime.now().date()}
        with pytest.raises(HomeAssistantError, match="한국 영역 밖"):
            await _handle_get_astronomical_info(call)

    # 2. 코디네이터/통합 구성요소 미등록 (Line 185-192) - 에러 메시지 실제 대조
    hass.data[DOMAIN] = {} 
    with patch("custom_components.kma_weather.__init__._geocode_ko", return_value=(37.5, 126.9, "서울")), \
         patch("custom_components.kma_weather.__init__.is_korean_coord_strict", return_value=True):
        with pytest.raises(HomeAssistantError, match="통합 구성요소가 등록되지 않았습니다"):
            await _handle_get_astronomical_info(call)

# =====================================================================
# [Part 3] api_kma.py : 3-1 ~ 3-11 전 구간 완벽 복구 및 에러 수정
# =====================================================================

@pytest.mark.asyncio
async def test_api_3_1_file_fail(mock_api):
    """[TC 3-1] 파일 로드 실패 (Line 146-147)"""
    with patch("builtins.open", side_effect=FileNotFoundError):
        mock_api._load_pollen_area_map()
        assert mock_api._pollen_area_data is None

@pytest.mark.asyncio
async def test_api_3_2_xml_and_404(mock_api):
    """[TC 3-2] XML 파싱 및 404/인증 에러 (Line 230, 267)"""
    mock_api._parse_xml_to_dict = MagicMock(return_value={"response": {"header": {"resultCode": "22"}}})
    with patch.object(mock_api.session, "get") as mock_get:
        resp = AsyncMock(); resp.status = 401
        mock_get.return_value.__aenter__.return_value = resp
        res = await mock_api._fetch("http://auth_fail", {})
        assert res["_http_error"] == "401"

@pytest.mark.asyncio
async def test_api_3_3_midterm_tuple(mock_api):
    """[TC 3-3] 중기예보 튜플 구조 검증 (Line 499)"""
    land = {"response": {"body": {"items": {"item": [{"wf3Am": "맑음"}]}}}}
    temp = {"response": {"body": {"items": {"item": [{"taMin3": 10}]}}}}
    with patch.object(mock_api, "_fetch", side_effect=[land, temp]):
        res = await mock_api._get_mid_term(datetime.now(), "r1", "r2")
        assert isinstance(res, tuple) and "wf3Am" in str(res[0])

@pytest.mark.asyncio
async def test_api_3_4_air_unsubscribed(mock_api):
    """[TC 3-4] 미신청 API 확인 (Line 519)"""
    mock_api._notified_unsubscribed.add("air")
    mock_api._cached_station = "종로구"
    with patch.object(mock_api, "_fetch", return_value={"response": {"header": {"resultCode": "22"}}}):
        res = await mock_api._get_air_quality(37.5, 126.9)
        assert res == {"station": "종로구"}

@pytest.mark.asyncio
async def test_api_3_5_short_term_approved(mock_api):
    """[TC 3-5] 단기예보 정상 응답 시 승인 마킹 (Line 567)"""
    mock_api.nx, mock_api.ny = 60, 127
    with patch.object(mock_api, "_fetch", return_value={"response": {"body": {"items": {"item": [{"t": 1}]}}}}):
        await mock_api._get_short_term(datetime.now())
        assert "short" in mock_api._approved_apis

@pytest.mark.asyncio
async def test_api_3_6_warning_empty_and_error(mock_api):
    """[TC 3-6] 특보 데이터 비어있음 및 에러 방어 (Line 808)"""
    with patch.object(mock_api, "_fetch", return_value={"response": {"body": {"items": {"item": []}}}}):
        assert await mock_api._get_warning("L101") == "특보없음"
    with patch.object(mock_api, "_fetch", side_effect=Exception("Err")):
        assert await mock_api._get_warning("L101") is None

@pytest.mark.asyncio
async def test_api_3_7_pollen_offseason(mock_api):
    """[TC 3-7] 꽃가루 비시즌 처리 (Line 610)"""
    mock_api._approved_apis.add("pollen")
    dt_off = datetime(2025, 1, 1) 
    with patch.object(mock_api, "_find_pollen_area", return_value=("110", "서울")):
        res = await mock_api._get_pollen(dt_off, 37.5, 126.9)
        assert res["announcement"] == "비시즌"

@pytest.mark.asyncio
async def test_api_3_8_pollen_gather_exception(mock_api):
    """[TC 3-8] 꽃가루 gather 중 예외 발생 (Line 775-777)"""
    dt_on = datetime(2025, 5, 1, 10, 0) # 시즌
    mock_api._pollen_today = {"worst": "나쁨"}
    with patch("asyncio.gather", side_effect=Exception("API 에러")):
        res = await mock_api._get_pollen(dt_on, 37.5, 126.9)
        assert res["worst"] == "나쁨" # 캐시 반환

@pytest.mark.asyncio
async def test_api_3_9_pollen_grade_99_fix(mock_api):
    """[TC 3-9] _get_grade의 rc='99' 분기 타격 (Line 710)"""
    mock_api._approved_apis.add("pollen")
    dt_on = datetime(2025, 5, 1, 10, 0)
    # _fetch가 rc='99' 데이터를 반환하도록 Mocking하여 내부 함수 _get_grade 타격
    data_99 = {"response": {"header": {"resultCode": "99"}}}
    with patch.object(mock_api, "_fetch", return_value=data_99), \
         patch.object(mock_api, "_find_pollen_area", return_value=("110", "서울")):
        res = await mock_api._get_pollen(dt_on, 37.5, 126.9)
        # rc=99일 경우 '좋음'으로 치환됨
        assert res["oak"] == "좋음"

@pytest.mark.asyncio
async def test_api_3_10_merge_past_data_fix(mock_api):
    """[TC 3-10] 오늘 데이터 부재 시 과거 데이터 사용 (Line 884-888)"""
    now = datetime.now()
    yesterday_str = (now - timedelta(days=1)).strftime("%Y%m%d")
    mock_api._cache_forecast_map = {yesterday_str: {"2300": {"TMP": "25"}}}
    # 오늘 데이터가 없으므로 어제의 25도를 가져오며 Line 884-888 타격
    res = mock_api._merge_all(now, {}, {}, {}, address="서울")
    assert res["weather"]["TMP"] == "25"

def test_api_3_11_translation_logic_fix(mock_api):
    """[TC 3-11] 기상 상태 치환 우선순위 및 상수 매핑"""
    # 1. 상수에 등록된 경우 우선 반환
    assert mock_api._translate_mid_condition_kor("흐리고 비") == "흐리고 비"
    # 2. 상수에 없고 '비' 포함 시
    assert mock_api._translate_mid_condition_kor("소나기를 동반한 비") == "비"
    # 3. '흐리' 포함 시
    assert mock_api._translate_mid_condition_kor("매우 흐림") == "흐림"
