"""
test_api_client.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[통합] test_api.py + test_address.py + test_additional_coverage.py

세 파일이 동일한 테스트 5개를 100% 중복 보유하고 있었음.
→ 이 파일 하나로 대체하고 기존 3파일 삭제.

검증 대상:
  - KMAWeatherAPI 초기화 · API 키 디코딩 · Nominatim User-Agent
  - _fetch 네트워크 에러 처리
  - _get_address Nominatim 응답 파싱
  - coordinator → KMAWeatherAPI 생성자 연동
"""

import pytest
import logging
import hashlib
from urllib.parse import quote
from unittest.mock import MagicMock, patch, AsyncMock

from custom_components.kma_weather.api_kma import KMAWeatherAPI
from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator


# ─────────────────────────────────────────────────────────────────────────────
# 공통 Mock 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

class MockResponse:
    """aiohttp ClientResponse 최소 구현체"""

    def __init__(self, json_data=None, status=200, should_raise=False):
        self._json_data = json_data or {}
        self.status = status
        self._should_raise = should_raise

    def raise_for_status(self):
        if self._should_raise:
            raise Exception("HTTP 500 Internal Server Error")

    async def json(self, *args, **kwargs):
        return self._json_data

    async def text(self, *args, **kwargs):
        import json
        return json.dumps(self._json_data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class MockSession:
    """aiohttp ClientSession 최소 구현체"""

    def __init__(self, json_data=None, should_raise=False):
        self.json_data = json_data
        self.should_raise = should_raise
        self.last_kwargs = {}  # _get_address 헤더 검증용

    def get(self, url, **kwargs):
        self.last_kwargs = kwargs
        return MockResponse(
            json_data=self.json_data,
            should_raise=self.should_raise,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 1. KMAWeatherAPI 초기화 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestApiInit:
    """KMAWeatherAPI 생성자 동작 검증"""

    def test_url_encoded_api_key_is_decoded(self):
        """
        [Given] URL 인코딩된 특수문자 포함 API 키
        [When]  KMAWeatherAPI(session, encoded_key) 생성
        [Then]  api.api_key가 디코딩된 원본 문자열이어야 함
        """
        original = "test_secret_key!@#"
        encoded = quote(original)
        api = KMAWeatherAPI(None, encoded)
        assert api.api_key == original

    def test_nominatim_user_agent_contains_kma_weather(self):
        """
        [Given] hass 없이 생성한 API 인스턴스
        [When]  _nominatim_user_agent 속성 확인
        [Then]  'KMA-Weather' 문자열이 포함되어야 함
        """
        api = KMAWeatherAPI(MagicMock(), "key")
        assert "KMA-Weather" in api._nominatim_user_agent

    def test_nominatim_user_agent_includes_hass_uuid(self):
        """
        [Given] installation_uuid가 있는 hass 객체
        [When]  KMAWeatherAPI(session, key, hass=hass) 생성
        [Then]  User-Agent에 UUID 앞 12자리가 포함되어야 함
        """
        class MockHass:
            installation_uuid = "12345678-1234-5678-1234-567812345678"

        session = MockSession(json_data={"address": {"city": "서울특별시", "borough": "강남구"}})
        api = KMAWeatherAPI(session, "TEST_KEY", hass=MockHass())
        assert "HomeAssistant-KMA-Weather" in api._nominatim_user_agent
        assert "123456781234" in api._nominatim_user_agent

    def test_nominatim_user_agent_falls_back_to_hash_without_hass(self):
        """
        [Given] hass 없는 환경
        [When]  KMAWeatherAPI 생성
        [Then]  User-Agent가 api_key SHA-1 해시 앞 12자리 기반이어야 함
        """
        api = KMAWeatherAPI(None, "test_key")
        expected_hash = hashlib.sha1("test_key".encode()).hexdigest()[:12]
        assert expected_hash in api._nominatim_user_agent

    def test_no_reg_id_stored_as_instance_variable(self):
        """
        [Given] 새 시그니처로 생성한 KMAWeatherAPI
        [When]  인스턴스 변수 확인
        [Then]  reg_id_temp / reg_id_land 속성이 없어야 함 (리팩터링 사수)
        """
        api = KMAWeatherAPI(MagicMock(), "key")
        assert not hasattr(api, "reg_id_temp")
        assert not hasattr(api, "reg_id_land")


# ─────────────────────────────────────────────────────────────────────────────
# 2. _fetch 네트워크 에러 처리
# ─────────────────────────────────────────────────────────────────────────────

class TestFetch:
    """_fetch HTTP 에러 / 재시도 / JSON 파싱 실패 검증"""

    @pytest.mark.asyncio
    async def test_http_exception_logs_error_and_returns_none(self, caplog):
        """
        [Given] 모든 요청에서 예외를 발생시키는 세션
        [When]  _fetch 호출
        [Then]  None 반환 + ERROR 레벨 로그 출력
        """
        session = MockSession(should_raise=True)
        api = KMAWeatherAPI(session, "TEST_KEY")
        with caplog.at_level(logging.ERROR):
            result = await api._fetch("http://example.com", {})
        assert result is None
        assert any(msg in caplog.text for msg in ["알 수 없는 API 오류", "API 호출 실패"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 403, 404])
    async def test_auth_and_not_found_status_returns_http_error(self, status):
        """
        [Given] 인증 실패(401/403) 또는 미발견(404) HTTP 상태
        [When]  _fetch 호출
        [Then]  {"_http_error": "<status>"} 형태의 dict 반환
        """
        api = KMAWeatherAPI(MagicMock(), "key")

        class FixedResp:
            def __init__(self): self.status = status
            def raise_for_status(self): pass
            async def text(self): return "{}"
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass

        api.session.get = lambda *a, **kw: FixedResp()
        result = await api._fetch("http://example.com", {})
        assert "_http_error" in result

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    async def test_retryable_status_retries_once_then_returns_error(self, status):
        """
        [Given] 재시도 대상 HTTP 상태 코드 (429 / 5xx)
        [When]  _fetch 호출
        [Then]  최초 1회 + 재시도 1회(총 2회) 후 _http_error 반환
        """
        call_count = {"n": 0}
        api = KMAWeatherAPI(MagicMock(), "key")

        class RetryResp:
            def __init__(self): self.status = status
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass

        def mock_get(*a, **kw):
            call_count["n"] += 1
            return RetryResp()

        api.session.get = mock_get
        with patch("custom_components.kma_weather.api_kma.asyncio.sleep", return_value=None):
            result = await api._fetch("http://example.com", {})

        assert result == {"_http_error": str(status)}
        assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_invalid_json_returns_none(self):
        """
        [Given] JSON이 아닌 텍스트(XML 등)를 반환하는 세션
        [When]  _fetch 호출
        [Then]  None 반환 (파싱 실패 방어)
        """
        api = KMAWeatherAPI(MagicMock(), "key")

        class BadJsonResp:
            status = 200
            def raise_for_status(self): pass
            async def text(self): return "<xml>NOT JSON</xml>"
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass

        api.session.get = lambda *a, **kw: BadJsonResp()
        result = await api._fetch("http://example.com", {})
        assert result is None

    @pytest.mark.asyncio
    async def test_non_retryable_exception_returns_none_without_retry(self):
        """
        [Given] ValueError 같은 재시도 불가 예외
        [When]  _fetch 호출
        [Then]  재시도 없이 즉시 None 반환 (호출 횟수 = 1)
        """
        call_count = {"n": 0}
        api = KMAWeatherAPI(MagicMock(), "key")

        def mock_get(*a, **kw):
            call_count["n"] += 1
            raise ValueError("unexpected")

        api.session.get = mock_get
        result = await api._fetch("http://example.com", {})
        assert result is None
        assert call_count["n"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# 3. _get_address Nominatim 연동
# ─────────────────────────────────────────────────────────────────────────────

class TestGetAddress:
    """_get_address: Nominatim 응답 파싱 및 User-Agent 헤더 검증"""

    @pytest.mark.asyncio
    async def test_returns_city_borough_from_nominatim(self):
        """
        [Given] city + borough를 담은 Nominatim JSON 응답
        [When]  _get_address(lat, lon) 호출
        [Then]  'city borough' 형식 문자열 반환
        """
        session = MockSession(json_data={"address": {"city": "서울특별시", "borough": "강남구"}})
        api = KMAWeatherAPI(session, "TEST_KEY")
        result = await api._get_address(37.56, 126.98)
        assert result == "서울특별시 강남구"

    @pytest.mark.asyncio
    async def test_request_header_contains_kma_weather(self):
        """
        [Given] 정상 Nominatim 응답
        [When]  _get_address 호출
        [Then]  요청 헤더 User-Agent에 'KMA-Weather' 문자열이 포함되어야 함
        """
        session = MockSession(json_data={"address": {"city": "서울특별시", "borough": "강남구"}})
        api = KMAWeatherAPI(session, "TEST_KEY")
        await api._get_address(37.56, 126.98)
        headers = session.last_kwargs.get("headers", {})
        assert "KMA-Weather" in headers.get("User-Agent", "")

    @pytest.mark.asyncio
    async def test_request_header_contains_hass_uuid(self):
        """
        [Given] hass.installation_uuid가 있는 환경
        [When]  _get_address 호출
        [Then]  User-Agent에 UUID 앞 12자리가 포함되어야 함
        """
        class MockHass:
            installation_uuid = "12345678-1234-5678-1234-567812345678"

        session = MockSession(json_data={"address": {"city": "서울특별시", "borough": "강남구"}})
        api = KMAWeatherAPI(session, "TEST_KEY", hass=MockHass())
        await api._get_address(37.56, 126.98)
        user_agent = session.last_kwargs["headers"]["User-Agent"]
        assert "HomeAssistant-KMA-Weather" in user_agent
        assert "123456781234" in user_agent

    @pytest.mark.asyncio
    async def test_returns_coordinate_string_on_empty_response(self):
        """
        [Given] 빈 JSON 응답 ({}) 을 반환하는 Nominatim
        [When]  _get_address(37.56, 126.98) 호출
        [Then]  좌표 문자열('37.56', '126.98' 포함) 반환
        """
        session = MockSession(json_data={})
        api = KMAWeatherAPI(session, "TEST_KEY")
        result = await api._get_address(37.56, 126.98)
        assert "37.56" in result or "37.5600" in result
        assert "126.98" in result or "126.9800" in result

    @pytest.mark.asyncio
    async def test_returns_coordinate_string_on_network_error(self):
        """
        [Given] 네트워크 오류가 발생하는 세션
        [When]  _get_address 호출
        [Then]  크래시 없이 좌표 문자열 반환 (Graceful Degradation)
        """
        session = MockSession(should_raise=True)
        api = KMAWeatherAPI(session, "TEST_KEY")
        result = await api._get_address(37.56, 126.98)
        assert "37.5" in result or "126.9" in result


# ─────────────────────────────────────────────────────────────────────────────
# 4. coordinator → KMAWeatherAPI 생성자 연동
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordinatorApiWiring:
    """coordinator가 KMAWeatherAPI를 올바른 인수로 생성하는지 검증"""

    @pytest.mark.asyncio
    async def test_coordinator_passes_hass_to_api(self, hass):
        """
        [Given] hass + entry로 KMAWeatherUpdateCoordinator 생성
        [When]  내부적으로 KMAWeatherAPI 생성자 호출
        [Then]  kwargs["hass"]가 hass 인스턴스여야 함
        """
        entry = MagicMock()
        entry.data = {"api_key": "TEST_KEY", "location_entity": ""}
        entry.options = {}
        entry.entry_id = "wiring_test"

        with patch("custom_components.kma_weather.coordinator.KMAWeatherAPI") as mock_cls:
            mock_cls.return_value = MagicMock()
            KMAWeatherUpdateCoordinator(hass, entry)

        mock_cls.assert_called_once()
        _, kwargs = mock_cls.call_args
        assert kwargs.get("hass") is hass

    @pytest.mark.asyncio
    async def test_coordinator_does_not_pass_reg_ids_to_api_init(self, hass):
        """
        [Given] coordinator 생성
        [When]  KMAWeatherAPI 생성자 호출 인수 확인
        [Then]  reg_id_temp / reg_id_land가 생성자 인수에 없어야 함
                (리팩터링 후 fetch_data 단계로 이동됐으므로)
        """
        entry = MagicMock()
        entry.data = {
            "api_key": "TEST_KEY", "nx": 60, "ny": 127,
            "reg_id_temp": "11B10101", "reg_id_land": "11B00000",
        }
        entry.options = {}
        entry.entry_id = "reg_id_test"

        with patch("custom_components.kma_weather.coordinator.KMAWeatherAPI") as mock_cls:
            mock_cls.return_value = MagicMock()
            KMAWeatherUpdateCoordinator(hass, entry)

        _, kwargs = mock_cls.call_args
        assert "reg_id_temp" not in kwargs
        assert "reg_id_land" not in kwargs
