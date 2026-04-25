import pytest
import logging
from urllib.parse import quote
from unittest.mock import MagicMock, patch
from custom_components.kma_weather.api_kma import KMAWeatherAPI

class MockAiohttpResponse:
    def __init__(self, json_data=None, should_raise=False, status=200):
        self._json_data = json_data or {}
        self._should_raise = should_raise
        self.status = status  # _fetch의 response.status 체크 대응
    def raise_for_status(self):
        if self._should_raise:
            raise Exception("HTTP 500 Internal Server Error")
    async def json(self, *args, **kwargs): return self._json_data
    async def __aenter__(self): return self
    async def __aexit__(self, exc_type, exc, tb): pass

class MockAiohttpSession:
    def __init__(self, json_data=None, should_raise=False):
        self.json_data = json_data
        self.should_raise = should_raise
        self.last_kwargs = {}
    def get(self, url, **kwargs):
        self.last_kwargs = kwargs
        return MockAiohttpResponse(json_data=self.json_data, should_raise=self.should_raise)

def test_api_key_decoding():
    original_key = "test_secret_key!@#"
    encoded_key = quote(original_key)
    api = KMAWeatherAPI(None, encoded_key)
    assert api.api_key == original_key

@pytest.mark.asyncio
async def test_fetch_http_error(caplog):
    session = MockAiohttpSession(should_raise=True)
    api = KMAWeatherAPI(session, "TEST_KEY")
    with caplog.at_level(logging.ERROR):
        result = await api._fetch("http://example.com", {})
    assert result is None
    assert any(msg in caplog.text for msg in ["알 수 없는 API 오류", "API 호출 실패"])

@pytest.mark.asyncio
async def test_nominatim_user_agent():
    session = MockAiohttpSession(json_data={"address": {"city": "서울특별시", "borough": "강남구"}})
    api = KMAWeatherAPI(session, "TEST_KEY")
    address = await api._get_address(37.56, 126.98)
    assert address == "서울특별시 강남구"
    assert "headers" in session.last_kwargs
    assert "KMA-Weather" in session.last_kwargs["headers"].get("User-Agent", "")

@pytest.mark.asyncio
async def test_nominatim_user_agent_with_hass_uuid():
    class MockHass:
        installation_uuid = "12345678-1234-5678-1234-567812345678"
    session = MockAiohttpSession(json_data={"address": {"city": "서울특별시", "borough": "강남구"}})
    api = KMAWeatherAPI(session, "TEST_KEY", hass=MockHass())
    await api._get_address(37.56, 126.98)
    user_agent = session.last_kwargs["headers"]["User-Agent"]
    assert "HomeAssistant-KMA-Weather" in user_agent
    assert "123456781234" in user_agent

@pytest.mark.asyncio
async def test_coordinator_passes_hass_to_api(hass):
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
    entry = MagicMock()
    entry.data = {
        "api_key": "TEST_KEY", "nx": 60, "ny": 127,
        "reg_id_temp": "11B10101", "reg_id_land": "11B00000"
    }
    entry.options = {}
    entry.entry_id = "api_hass_test"
    with patch("custom_components.kma_weather.coordinator.KMAWeatherAPI") as mock_api:
        mock_api.return_value = MagicMock()
        KMAWeatherUpdateCoordinator(hass, entry)
    mock_api.assert_called_once()
    _, kwargs = mock_api.call_args
    assert kwargs.get("hass") is hass


# ══════════════════════════════════════════════════════════════════════════════
# 꽃가루 API 테스트 (HealthWthrIdxServiceV3)
# ══════════════════════════════════════════════════════════════════════════════

import pytest
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch


def _make_pollen_response(result_code="00", today="1", tomorrow="1", code="D07"):
    """꽃가루 API 성공 응답 mock 생성."""
    if result_code != "00":
        return {
            "response": {
                "header": {"resultCode": result_code, "resultMsg": "ERROR"},
            }
        }
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
            "body": {
                "dataType": "JSON",
                "items": {"item": [{
                    "code": code,
                    "areaNo": "1111051500",
                    "date": "2026042506",
                    "today": today,
                    "tomorrow": tomorrow,
                    "dayaftertomorrow": "2",
                    "twodaysaftertomorrow": "",
                }]},
                "pageNo": 1, "numOfRows": 10, "totalCount": 1,
            }
        }
    }


def _make_api(session=None):
    """테스트용 KMAWeatherAPI 인스턴스 생성."""
    api = KMAWeatherAPI(session or MagicMock(), "test_key")
    api.hass = None
    api._pollen_area_data = [{"c": "1111051500", "n": "서울특별시 종로구 청운효자동",
                               "la": 37.58, "lo": 126.97}]
    api._pollen_cached_lat = api._pollen_cached_lon = None
    api._pollen_cached_area_no = api._pollen_cached_area_name = None
    return api


class TestPollenEndpoints:
    """꽃가루 API 엔드포인트 및 파라미터 검증."""

    @pytest.mark.asyncio
    async def test_pollen_endpoints_correct(self):
        """
        [Given] 소나무·참나무·잡초류 API 호출 시
        [When] fetch URL 확인
        [Then] HealthWthrIdxServiceV3 하위 각 오퍼레이션이어야 함
        """
        api = _make_api()
        called_urls = []

        async def mock_fetch(url, params):
            called_urls.append(url)
            return _make_pollen_response()

        api._fetch = mock_fetch
        now = datetime(2026, 4, 25, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        await api._get_pollen(now, 37.58, 126.97)

        assert any("getPinePollenRiskIdxV3"  in u for u in called_urls), "소나무 엔드포인트 없음"
        assert any("getOakPollenRiskIdxV3"   in u for u in called_urls), "참나무 엔드포인트 없음"
        assert any("getWeedsPollenRiskndxV3" in u for u in called_urls), "잡초류 엔드포인트 없음"

    @pytest.mark.asyncio
    async def test_pollen_area_no_is_eup_myeon_dong(self):
        """
        [Given] 위경도로 areaNo 조회 시
        [When] API 파라미터 확인
        [Then] 읍면동 단위 코드(1111051500)를 사용해야 함
        """
        api = _make_api()
        received_params = {}

        async def mock_fetch(url, params):
            received_params.update(params)
            return _make_pollen_response()

        api._fetch = mock_fetch
        now = datetime(2026, 4, 25, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        await api._get_pollen(now, 37.58, 126.97)

        assert received_params.get("areaNo") == "1111051500"

    @pytest.mark.asyncio
    async def test_time_param_morning(self):
        """
        [Given] 06~17시 사이 호출 시
        [When] time 파라미터 확인
        [Then] 당일 06시 발표 시각 사용, today 키 기준
        """
        api = _make_api()
        received_params = {}

        async def mock_fetch(url, params):
            received_params.update(params)
            return _make_pollen_response()

        api._fetch = mock_fetch
        now = datetime(2026, 4, 25, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        result = await api._get_pollen(now, 37.58, 126.97)

        assert received_params.get("time") == "2026042506"
        assert result.get("announcement", "").endswith("06시 발표")

    @pytest.mark.asyncio
    async def test_time_param_evening(self):
        """
        [Given] 18시 이후 호출 시
        [When] time 파라미터 확인
        [Then] 당일 18시 발표 시각 사용
        """
        api = _make_api()
        received_params = {}

        async def mock_fetch(url, params):
            received_params.update(params)
            return _make_pollen_response()

        api._fetch = mock_fetch
        now = datetime(2026, 4, 25, 20, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        result = await api._get_pollen(now, 37.58, 126.97)

        assert received_params.get("time") == "2026042518"
        assert result.get("announcement", "").endswith("18시 발표")

    @pytest.mark.asyncio
    async def test_time_param_midnight(self):
        """
        [Given] 00~05시 (자정~새벽) 호출 시
        [When] time 파라미터 확인
        [Then] 전날 18시 발표 사용, tomorrow 값이 오늘 값으로 사용됨
        """
        api = _make_api()
        received_params = {}

        async def mock_fetch(url, params):
            received_params.update(params)
            return _make_pollen_response(today="1", tomorrow="2")

        api._fetch = mock_fetch
        now = datetime(2026, 4, 25, 3, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        result = await api._get_pollen(now, 37.58, 126.97)

        assert received_params.get("time") == "2026042418"
        # 전날 18시 발표의 tomorrow(="2") = 오늘 값 → 나쁨
        assert result.get("pine") == "나쁨"


class TestPollenSeasons:
    """꽃가루 시즌 체크 검증."""

    @pytest.mark.asyncio
    async def test_pine_oak_in_season_april(self):
        """[Given] 4월 [Then] 소나무·참나무 시즌 중"""
        api = _make_api()
        async def mock_fetch(url, params):
            return _make_pollen_response(today="2")
        api._fetch = mock_fetch
        now = datetime(2026, 4, 25, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        result = await api._get_pollen(now, 37.58, 126.97)
        assert result.get("pine") == "나쁨"
        assert result.get("oak") == "나쁨"

    @pytest.mark.asyncio
    async def test_grass_offseason_april(self):
        """[Given] 4월 [Then] 잡초류 비시즌 → 좋음"""
        api = _make_api()
        grass_response = {"response": {"header": {"resultCode": "99",
                          "resultMsg": "해당지수자료 제공기간이 아닙니다! [자료제공기간 8월 ~ 10월]"}}}
        async def mock_fetch(url, params):
            if "Weeds" in url:
                return grass_response
            return _make_pollen_response(today="2")
        api._fetch = mock_fetch
        now = datetime(2026, 4, 25, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        result = await api._get_pollen(now, 37.58, 126.97)
        assert result.get("grass") == "좋음"

    @pytest.mark.asyncio
    async def test_all_offseason_july(self):
        """[Given] 7월 (참나무·소나무 비시즌, 잡초류 비시즌) [Then] 모두 좋음"""
        api = _make_api()
        api._approved_apis.add("pollen")  # 이미 승인됨
        now = datetime(2026, 7, 15, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        result = await api._get_pollen(now, 37.58, 126.97)
        assert result == {
            "oak": "좋음", "pine": "좋음", "grass": "좋음", "worst": "좋음",
            "area_name": "서울특별시 종로구 청운효자동",
            "area_no": "1111051500", "announcement": "비시즌",
        }

    @pytest.mark.asyncio
    async def test_grass_in_season_september(self):
        """[Given] 9월 [Then] 잡초류 시즌 중"""
        api = _make_api()
        api._approved_apis.add("pollen")
        grass_response = _make_pollen_response(today="3", code="D09")
        async def mock_fetch(url, params):
            return grass_response
        api._fetch = mock_fetch
        now = datetime(2026, 9, 10, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        result = await api._get_pollen(now, 37.58, 126.97)
        assert result.get("grass") == "매우나쁨"


class TestPollenGrades:
    """꽃가루 등급 매핑 검증."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("today_val,expected", [
        ("0", "좋음"),
        ("1", "보통"),
        ("2", "나쁨"),
        ("3", "매우나쁨"),
    ])
    async def test_grade_mapping(self, today_val, expected):
        """
        [Given] today 값 0~3
        [Then] 좋음/보통/나쁨/매우나쁨으로 매핑
        """
        api = _make_api()
        async def mock_fetch(url, params):
            return _make_pollen_response(today=today_val)
        api._fetch = mock_fetch
        now = datetime(2026, 4, 25, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        result = await api._get_pollen(now, 37.58, 126.97)
        assert result.get("pine") == expected

    @pytest.mark.asyncio
    async def test_worst_is_max_grade(self):
        """
        [Given] 소나무=보통, 참나무=나쁨
        [Then] worst=나쁨
        """
        api = _make_api()
        async def mock_fetch(url, params):
            if "Pine" in url:
                return _make_pollen_response(today="1")  # 보통
            if "Oak" in url:
                return _make_pollen_response(today="2", code="D06")  # 나쁨
            return {"response": {"header": {"resultCode": "99"}}}
        api._fetch = mock_fetch
        now = datetime(2026, 4, 25, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        result = await api._get_pollen(now, 37.58, 126.97)
        assert result.get("worst") == "나쁨"


class TestPollenApprovalLogic:
    """꽃가루 API 승인 로직 검증."""

    @pytest.mark.asyncio
    async def test_approved_on_00(self):
        """[Given] resultCode=00 [Then] _approved_apis에 pollen 추가"""
        api = _make_api()
        async def mock_fetch(url, params):
            return _make_pollen_response()
        api._fetch = mock_fetch
        now = datetime(2026, 4, 25, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        await api._get_pollen(now, 37.58, 126.97)
        assert "pollen" in api._approved_apis

    @pytest.mark.asyncio
    async def test_not_approved_on_30(self):
        """[Given] resultCode=30 (미신청) [Then] _approved_apis에 없음, _pending에 유지"""
        api = _make_api()
        async def mock_fetch(url, params):
            return _make_pollen_response(result_code="30")
        api._fetch = mock_fetch
        now = datetime(2026, 4, 25, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        result = await api._get_pollen(now, 37.58, 126.97)
        assert "pollen" not in api._approved_apis
        assert result == {}

    @pytest.mark.asyncio
    async def test_approved_removed_on_unsubscribe(self):
        """[Given] 기존 승인됨 → resultCode=30 감지 [Then] _approved_apis에서 제거"""
        api = _make_api()
        api._approved_apis.add("pollen")
        async def mock_fetch(url, params):
            return _make_pollen_response(result_code="30")
        api._fetch = mock_fetch
        now = datetime(2026, 4, 25, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        await api._get_pollen(now, 37.58, 126.97)
        assert "pollen" not in api._approved_apis

    @pytest.mark.asyncio
    async def test_offseason_no_api_call_when_approved(self):
        """[Given] 비시즌 + 승인됨 [Then] API 호출 없이 좋음 반환"""
        api = _make_api()
        api._approved_apis.add("pollen")
        called = []
        async def mock_fetch(url, params):
            called.append(url)
            return _make_pollen_response()
        api._fetch = mock_fetch
        now = datetime(2026, 7, 15, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        await api._get_pollen(now, 37.58, 126.97)
        assert len(called) == 0, "비시즌+승인 시 API 호출 없어야 함"

    @pytest.mark.asyncio
    async def test_offseason_api_called_when_pending(self):
        """[Given] 비시즌 + 미확인(pending) [Then] 승인 여부 확인 위해 API 호출"""
        api = _make_api()
        # pollen이 _pending_apis에 있고 _approved_apis에 없음 (초기 상태)
        called = []
        async def mock_fetch(url, params):
            called.append(url)
            return _make_pollen_response()
        api._fetch = mock_fetch
        now = datetime(2026, 7, 15, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        await api._get_pollen(now, 37.58, 126.97)
        assert len(called) > 0, "비시즌+미확인 시 승인 확인 위해 API 호출해야 함"
