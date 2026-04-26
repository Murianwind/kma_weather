"""
tests/test_comprehensive.py
커버리지 보고서의 모든 누락 라인(api_kma 884, 775 등)을 타격하며 
3-1 ~ 3-11 전 구간을 100% 복구한 최종 마스터 테스트입니다.
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
    """KMAWeatherAPI(session, api_key, hass) 실제 생성자 준수 피스처"""
    return KMAWeatherAPI(MagicMock(), "test_api_key", hass)

# =====================================================================
# [Part 1] config_flow.py : 설정 플로우 및 모든 에러 코드 분기
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("res_code, expected", [
    ("22", "api_quota_exceeded"),
    ("20", "api_access_denied"),
    ("32", "api_access_denied"),
    ("99", "api_error"),
])
async def test_config_flow_error_codes(hass: HomeAssistant, aioclient_mock, res_code, expected):
    """[TC 1-1] 소스코드 79-88 라인의 모든 resultCode 분기 타격"""
    url = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    aioclient_mock.get(url, status=200, json={"response": {"header": {"resultCode": res_code}}})
    assert await _validate_api_key(hass, "test_key") == expected

# =====================================================================
# [Part 2] __init__.py : 천문 서비스 정밀 타격 (159-211 라인)
# =====================================================================

@pytest.mark.asyncio
async def test_astro_service_exact_logic(hass: HomeAssistant):
    """[TC 2-1] 지오코딩 실패 및 통합 구성요소 미등록 에러 메시지 대조"""
    call = MagicMock(spec=ServiceCall); call.hass = hass
    
    # 1. 지오코딩 실패 (Line 175)
    with patch("custom_components.kma_weather.__init__._geocode_ko", return_value=(None, None, None)):
        call.data = {"address": "유령주소", "date": datetime.now().date()}
        with pytest.raises(HomeAssistantError, match="주소를 찾을 수 없습니다"):
            await _handle_get_astronomical_info(call)

    # 2. 통합 구성요소 미등록 (Line 185) - 소스코드의 실제 메시지 반영
    hass.data[DOMAIN] = {}
    with patch("custom_components.kma_weather.__init__._geocode_ko", return_value=(37.5, 126.9, "서울")), \
         patch("custom_components.kma_weather.__init__.is_korean_coord_strict", return_value=True):
        with pytest.raises(HomeAssistantError, match="통합 구성요소가 등록되지 않았습니다"):
            await _handle_get_astronomical_info(call)

# =====================================================================
# [Part 3] api_kma.py : 3-1 ~ 3-11 전 구간 완벽 복구
# =====================================================================

@pytest.mark.asyncio
async def test_api_3_1_pollen_map_error(mock_api):
    """[TC 3-1] 꽃가루 맵 파일 로드 실패 대응 (Line 146)"""
    with patch("builtins.open", side_effect=FileNotFoundError):
        mock_api._load_pollen_area_map()
        assert mock_api._pollen_area_data is None

@pytest.mark.asyncio
async def test_api_3_2_fetch_xml_and_401(mock_api):
    """[TC 3-2] XML 응답 및 401 Unauthorized 에러 (Line 230, 267)"""
    mock_api._parse_xml_to_dict = MagicMock(return_value={"response": {"header": {"resultCode": "22"}}})
    with patch.object(mock_api.session, "get") as mock_get:
        resp = AsyncMock(); resp.status = 401
        mock_get.return_value.__aenter__.return_value = resp
        res = await mock_api._fetch("http://error", {})
        assert res["_http_error"] == "401"

@pytest.mark.asyncio
async def test_api_3_3_midterm_tuple_fix(mock_api):
    """[TC 3-3] 중기예보 튜플 구조 검증 (Line 499)"""
    land = {"response": {"body": {"items": {"item": [{"wf3Am": "맑음"}]}}}}
    temp = {"response": {"body": {"items": {"item": [{"taMin3": 10}]}}}}
    with patch.object(mock_api, "_fetch", side_effect=[land, temp]):
        res = await mock_api._get_mid_term(datetime.now(), "r1", "r2")
        assert isinstance(res, tuple) and "wf3Am" in str(res[0])

@pytest.mark.asyncio
async def test_api_3_4_air_unsubscribed_skip(mock_api):
    """[TC 3-4] 미신청 API 감지 시 알림 중복 방지 (Line 519)"""
    mock_api._notified_unsubscribed.add("air")
    with patch("homeassistant.components.persistent_notification.async_create") as mock_notify:
        assert mock_api._check_unsubscribed("air", "22") is True
        mock_notify.assert_not_called()

@pytest.mark.asyncio
async def test_api_3_5_short_term_approved(mock_api):
    """[TC 3-5] 단기예보 정상 시 승인 마킹 (Line 567)"""
    mock_api.nx, mock_api.ny = 60, 127
    with patch.object(mock_api, "_fetch", return_value={"response": {"body": {"items": {"item": [{"t": 1}]}}}}):
        await mock_api._get_short_term(datetime.now())
        assert "short" in mock_api._approved_apis

@pytest.mark.asyncio
async def test_api_3_6_7_warning_cases(mock_api):
    """[TC 3-6, 3-7] 특보 데이터 부재 및 에러 방어 (Line 808)"""
    with patch.object(mock_api, "_fetch", return_value={"response": {"body": {"items": {"item": []}}}}):
        assert await mock_api._get_warning("L101") == "특보없음"
    with patch.object(mock_api, "_fetch", side_effect=Exception):
        assert await mock_api._get_warning("L101") is None

@pytest.mark.asyncio
async def test_api_3_8_pollen_gather_error_fix(mock_api):
    """[TC 3-8] 꽃가루 gather 중 예외 발생 시 캐시 반환 (Line 775)"""
    dt_on = datetime(2025, 5, 1, 10, 0) # 시즌
    mock_api._pollen_today = {"worst": "나쁨"}
    # asyncio.gather에서 Exception을 발생시켜 catch 구문 실행
    with patch("custom_components.kma_weather.api_kma.asyncio.gather", side_effect=Exception):
        res = await mock_api._get_pollen(dt_on, 37.5, 126.9)
        assert res["worst"] == "나쁨"

@pytest.mark.asyncio
async def test_api_3_9_pollen_grade_99_fix(mock_api):
    """[TC 3-9] _get_grade rc='99' 분기 타격 및 KeyError 방지 (Line 710)"""
    mock_api._approved_apis.add("pollen")
    dt_on = datetime(2025, 5, 1, 10, 0)
    # pine_data는 '00'이어야 로직이 중단되지 않음, oak_data에 '99' 주입
    pine_ok = {"response": {"header": {"resultCode": "00"}}}
    oak_99 = {"response": {"header": {"resultCode": "99"}}}
    with patch("custom_components.kma_weather.api_kma.asyncio.gather", new_callable=AsyncMock) as mock_gather:
        mock_gather.return_value = (pine_ok, oak_99, pine_ok)
        with patch.object(mock_api, "_find_pollen_area", return_value=("110", "서울")):
            res = await mock_api._get_pollen(dt_on, 37.5, 126.9)
            assert res["oak"] == "좋음" # rc=99는 '좋음' 반환

@pytest.mark.asyncio
async def test_api_3_10_merge_fallback_to_past(mock_api):
    """[TC 3-10] 오늘 데이터 부재 시 과거 데이터 사용 (Line 884-888)"""
    now = datetime.now()
    past = (now - timedelta(days=1)).strftime("%Y%m%d")
    mock_api._cache_forecast_map = {past: {"2300": {"TMP": "15"}}}
    # 오늘 데이터는 없고 어제 데이터만 캐시에 있는 상태로 병합 호출
    res = mock_api._merge_all(now, {}, {}, {}, address="서울")
    assert res["weather"]["TMP"] == "15"

def test_api_3_11_condition_translation_logic(mock_api):
    """[TC 3-11] 기상 상태 치환 우선순위 및 상수 매핑"""
    # 1. 상수에 등록된 경우 우선 반환
    assert mock_api._translate_mid_condition_kor("흐리고 비") == "흐리고 비"
    # 2. '소나기' 체크가 '비' 체크보다 먼저 수행됨 (Line 990 vs 992)
    assert mock_api._translate_mid_condition_kor("소나기를 동반한 비") == "소나기"
    # 3. '흐리' 포함 시
    assert mock_api._translate_mid_condition_kor("매우 흐림") == "흐림"
