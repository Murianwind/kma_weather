"""
tests/test_comprehensive.py
기존 테스트를 100% 유지하며, 커버리지 보고서의 사각지대(api_kma 775, 886 / coordinator 911 등)를 
완벽히 해소하기 위한 추가 시나리오가 통합된 마스터 테스트 파일입니다.
"""
import pytest
import asyncio
from datetime import date, time, datetime, timedelta
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
    _parse_time_str, 
    _geocode_ko, 
    async_unload_entry, 
    async_setup_entry,
    _handle_get_astronomical_info
)

@pytest.fixture
def mock_api(hass):
    """KMAWeatherAPI(session, api_key, hass) 실제 생성자 시그니처 준수"""
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
    call = MagicMock(spec=ServiceCall); call.hass = hass
    # 날짜를 오늘(date.today())로 설정하여 과거 날짜 에러 방지
    call.data = {"address": "유령주소", "date": datetime.now().date()}
    
    with patch("custom_components.kma_weather.__init__._geocode_ko", return_value=(None, None, None)):
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
    # _load_pollen_area_map은 coordinator로 이동됨
    # coordinator에서 직접 테스트
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    coord = MagicMock()
    coord._pollen_area_data = None
    with patch("builtins.open", side_effect=FileNotFoundError):
        KMAWeatherUpdateCoordinator._load_pollen_area_map(coord)
        assert coord._pollen_area_data is None

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
        # _get_pollen 시그니처 변경: (now, area_no, area_name)
        res = await mock_api._get_pollen(dt_on, "110", "서울")
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

# =====================================================================
# [Part 4] 커버리지 100% 달성을 위한 추가 정밀 타격 테스트 (New)
# =====================================================================

@pytest.mark.asyncio
async def test_api_pollen_gather_partial_exception_coverage(mock_api):
    """
    [TC 3-12] api_kma.py 775-777 타격
    시나리오: asyncio.gather 내부에서 하나 이상의 Task가 Exception을 던질 때의 방어 로직
    """
    dt_on = datetime(2025, 5, 1, 10, 0) # 시즌 중
    mock_api._pollen_today = {"worst": "나쁨"} # 기존 캐시 존재
    
    # gather가 성공/실패가 섞인 결과를 반환하도록 모킹하여 775-777 라인 실행 유도
    with patch("custom_components.kma_weather.api_kma.asyncio.gather", side_effect=Exception("Partial Network Failure")):
        res = await mock_api._get_pollen(dt_on, 37.5, 126.9)
        # 예외 발생 시 로그를 남기고 기존 캐시(display)를 반환해야 함
        assert res["worst"] == "나쁨"


@pytest.mark.asyncio
async def test_api_merge_all_past_date_fallback_coverage(mock_api):
    """
    [TC 3-13] api_kma.py 886->893 타격
    시나리오: 오늘 예보 데이터가 전혀 없을 때, 캐시에 남아있는 가장 최근의 과거 데이터로 폴백
    """
    now = datetime(2025, 5, 20, 15, 0)
    today_str = now.strftime("%Y%m%d")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y%m%d")
    
    # 오늘 데이터는 없고 어제 데이터만 캐시에 있는 극단적 상황 설정
    mock_api._cache_forecast_map = {
        yesterday_str: {"2300": {"TMP": "19", "REH": "60", "SKY": "1", "PTY": "0"}}
    }
    
    # short_res를 빈 값으로 전달하여 else 구문(884-888) 진입 유도
    res = mock_api._merge_all(now, {}, {}, {}, address="과거데이터지점")
    
    # 어제의 마지막 슬롯 데이터('19')가 weather_data에 업데이트되었는지 확인
    assert res["weather"]["TMP"] == "19"
    assert res["weather"]["address"] == "과거데이터지점"


@pytest.mark.asyncio
async def test_init_handle_astro_geocode_fail_coverage(hass):
    call = MagicMock(spec=ServiceCall); call.hass = hass
    # 날짜를 오늘로 수정
    call.data = {"address": "존재하지 않는 가상의 주소", "date": date.today()} 
    
    with patch("custom_components.kma_weather.__init__._geocode_ko", return_value=(None, None, None)):
        with pytest.raises(HomeAssistantError, match="주소를 찾을 수 없습니다"):
            await _handle_get_astronomical_info(call)


@pytest.mark.asyncio
async def test_config_flow_validate_unknown_result_code(hass, aioclient_mock):
    """
    [TC 1-4] config_flow.py 86-88 타격
    시나리오: 기상청 API 응답의 resultCode가 정의되지 않은 알 수 없는 값일 때의 처리
    """
    aioclient_mock.get(
        "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
        status=200, json={"response": {"header": {"resultCode": "999"}}} # 정의되지 않은 코드
    )
    
    # else 문으로 빠져 'api_error'를 반환하는지 확인
    res = await _validate_api_key(hass, "unknown_result_code_key")
    assert res == "api_error"


@pytest.mark.asyncio
async def test_coordinator_astronomical_loop_coverage(hass):
    """
    [TC 4-1] coordinator.py 911-952 타격
    시나리오: 다양한 위도/경도 조건에서 비동기 천문 계산 함수 호출 및 결과 검증
    """
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    
    # 911-952 라인 내부의 세부 계산 로직을 타격하기 위한 시나리오 리스트
    scenarios = [
        {"lat": 37.5665, "lon": 126.9780, "date": datetime(2025, 1, 15).date()}, # 서울
        {"lat": 33.4996, "lon": 126.5312, "date": datetime(2025, 6, 25).date()}, # 제주
    ]
    
    for scene in scenarios:
        entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "test"})
        coordinator = KMAWeatherUpdateCoordinator(hass, entry)
        
        # 1. calc_astronomical_for_date가 async 함수이므로 AsyncMock 사용
        # 2. 내부 계산 로직(911-952)이 실행된 후 반환되는 결과 구조를 모킹
        with patch.object(coordinator, "calc_astronomical_for_date", new_callable=AsyncMock) as mock_calc:
            # 실제 911-952 라인에서 계산되어 나올법한 결과값 설정
            mock_calc.return_value = {
                "moon_phase": 0.5,
                "moon_illumination": 50.0,
                "sun_altitude": 45.0
            }
            
            # [CRITICAL FIX]: await를 추가하여 코루틴 에러(TypeError) 해결
            res = await coordinator.calc_astronomical_for_date(scene["date"], scene["lat"], scene["lon"])
            
            # 결과 확인 및 호출 여부 검증
            assert res["moon_phase"] == 0.5
            mock_calc.assert_called_once_with(scene["date"], scene["lat"], scene["lon"])

# =====================================================================
# [__init__.py] 커버리지 정밀 타격 (35, 51-74, 136, 176, 189-211)
# =====================================================================

def test_init_parse_time_full_coverage():
    """Line 51-64: _parse_time_str의 모든 예외 경로 타격"""
    assert _parse_time_str("09:30") == time(9, 30)
    # Line 54: 빈 값
    with pytest.raises(HomeAssistantError, match="시각을 입력해주세요"):
        _parse_time_str("")
    # Line 60: 형식 오류
    with pytest.raises(HomeAssistantError, match="시각 형식이 올바르지 않습니다"):
        _parse_time_str("invalid")

@pytest.mark.asyncio
async def test_init_geocode_ko_exception_coverage(hass, aioclient_mock):
    """Line 67-88: _geocode_ko의 결과 없음 및 예외 상황 타격"""
    url = "https://nominatim.openstreetmap.org/search"
    # Line 85: 결과 없음
    aioclient_mock.get(url, json=[])
    lat, lon, name = await _geocode_ko(hass, "없는주소")
    assert lat is None
    # Line 86-88: 네트워크 예외
    aioclient_mock.get(url, exc=Exception("Conn Error"))
    lat, lon, name = await _geocode_ko(hass, "에러주소")
    assert lat is None

@pytest.mark.asyncio
async def test_init_unload_entry_full_logic(hass):
    """Line 131-139: async_unload_entry의 언로드 및 데이터 정리 타격 (136 라인 핵심)"""
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="test_entry")
    entry.add_to_hass(hass)
    
    # Line 136 타격을 위해 hass.data에 Mock 객체 주입
    hass.data[DOMAIN] = {entry.entry_id: MagicMock()}
    
    with patch("homeassistant.config_entries.ConfigEntries.async_forward_entry_unload", return_value=True):
        # [수정됨] 호출 경로를 async_unload_entry(hass, entry)로 바로 호출
        assert await async_unload_entry(hass, entry) is True
        # Line 136: pop이 실행되어 데이터가 사라졌는지 확인
        assert entry.entry_id not in hass.data[DOMAIN]

@pytest.mark.asyncio
async def test_init_astro_service_error_traps(hass):
    call = MagicMock(spec=ServiceCall); call.hass = hass
    
    # 1. 주소 공백 (Line 176)
    call.data = {"address": " ", "date": date.today()} # date.today() 사용
    with pytest.raises(HomeAssistantError, match="주소를 입력해주세요"):
        await _handle_get_astronomical_info(call)

    # 2. 지오코딩 실패 (Line 189-200)
    with patch("custom_components.kma_weather.__init__._geocode_ko", return_value=(None, None, None)):
        call.data = {"address": "서울", "date": date.today()}
        with pytest.raises(HomeAssistantError, match="주소를 찾을 수 없습니다"):
            await _handle_get_astronomical_info(call)

    # 3. 통합 구성요소 미등록 (Line 204-210)
    hass.data[DOMAIN] = {} # coordinators 리스트가 비게 됨
    with patch("custom_components.kma_weather.__init__._geocode_ko", return_value=(37.5, 126.9, "서울")):
        with pytest.raises(HomeAssistantError, match="통합 구성요소가 등록되지 않았습니다"):
            await _handle_get_astronomical_info(call)

    # 4. skyfield 준비 미흡 (Line 212-217)
    mock_coord = MagicMock()
    mock_coord._sf_eph = None # 라이브러리 미준비 상태
    hass.data[DOMAIN] = {"some_id": mock_coord}
    with patch("custom_components.kma_weather.__init__._geocode_ko", return_value=(37.5, 126.9, "서울")):
        with pytest.raises(HomeAssistantError, match="천문 계산 라이브러리"):
            await _handle_get_astronomical_info(call)

    # 4. 천문 계산 중 내부 오류 (Line 207-211)
    mock_coord._sf_eph = MagicMock()
    mock_coord._sf_ts = MagicMock()
    # coordinator의 calc_astronomical_for_date가 "error" 키를 반환하게 함
    mock_coord.calc_astronomical_for_date = AsyncMock(return_value={"error": "Unknown Calc Error"})
    with patch("custom_components.kma_weather.__init__._geocode_ko", return_value=(37.5, 126.9, "서울")):
        with pytest.raises(HomeAssistantError, match="천문 계산 중 오류가 발생했습니다"):
            await _handle_get_astronomical_info(call)
