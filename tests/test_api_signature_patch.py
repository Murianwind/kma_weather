"""
리팩터링으로 인한 기존 테스트 호환성 패치.

KMAWeatherAPI 시그니처 변경:
  전: KMAWeatherAPI(session, api_key, reg_id_temp, reg_id_land, hass=None)
  후: KMAWeatherAPI(session, api_key, hass=None)

fetch_data 시그니처 변경:
  전: fetch_data(lat, lon, nx, ny)
  후: fetch_data(lat, lon, nx, ny, reg_id_temp, reg_id_land, warn_area_code)

_get_mid_term 시그니처 변경:
  전: _get_mid_term(now)
  후: _get_mid_term(now, reg_id_temp, reg_id_land)

이 파일은 변경된 시그니처를 직접 검증하는 테스트를 포함한다.
기존 테스트 파일들(test_api.py, test_additional_coverage.py 등)에서
KMAWeatherAPI 생성자 호출 시 reg_id 인자를 제거해야 한다.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from zoneinfo import ZoneInfo

from custom_components.kma_weather.api_kma import KMAWeatherAPI
from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

TZ = ZoneInfo("Asia/Seoul")


# ─────────────────────────────────────────────────────────────────────────────
# 이전 시그니처 호환성 검증
# ─────────────────────────────────────────────────────────────────────────────

def test_new_api_init_signature():
    """새 시그니처로 KMAWeatherAPI 생성이 정상적으로 작동함"""
    api = KMAWeatherAPI(MagicMock(), "test_key")
    assert api.api_key == "test_key"


def test_new_api_init_with_hass():
    """hass 인자를 포함한 새 시그니처도 정상 작동함"""
    mock_hass = MagicMock()
    api = KMAWeatherAPI(MagicMock(), "test_key", hass=mock_hass)
    assert api.hass is mock_hass


def test_api_no_longer_stores_reg_ids():
    """KMAWeatherAPI가 더 이상 reg_id를 인스턴스 변수로 보관하지 않음"""
    api = KMAWeatherAPI(MagicMock(), "key")
    assert not hasattr(api, "reg_id_temp")
    assert not hasattr(api, "reg_id_land")


@pytest.mark.asyncio
async def test_fetch_data_new_signature():
    """새 시그니처로 fetch_data 호출이 정상 작동함"""
    api = KMAWeatherAPI(MagicMock(), "key")
    api._get_short_term = AsyncMock(return_value=None)
    api._get_mid_term = AsyncMock(return_value=(None, None, datetime(2026, 4, 11, 6, 0, tzinfo=TZ)))
    api._get_air_quality = AsyncMock(return_value={})
    api._get_address = AsyncMock(return_value="서울시")
    api._get_warning = AsyncMock(return_value="없음")

    result = await api.fetch_data(
        lat=37.56, lon=126.98,
        nx=60, ny=127,
        reg_id_temp="11B10101",
        reg_id_land="11B00000",
        warn_area_code="L1100200",
    )
    assert result is not None
    assert "weather" in result


@pytest.mark.asyncio
async def test_get_mid_term_new_signature():
    """_get_mid_term이 reg_id를 인자로 올바르게 받음"""
    api = KMAWeatherAPI(MagicMock(), "key")
    called_params = []

    async def mock_fetch(url, params, **kwargs):
        called_params.append(params.get("regId"))
        return {"response": {"body": {"items": {"item": [{"taMax3": "25"}]}}}}

    api._fetch = mock_fetch
    now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
    await api._get_mid_term(now, "11B10101", "11B00000")

    assert "11B10101" in called_params
    assert "11B00000" in called_params


@pytest.mark.asyncio
async def test_coordinator_uses_new_api_signature(hass):
    """coordinator가 새 시그니처로 KMAWeatherAPI를 생성함"""
    entry = MagicMock()
    entry.data = {"api_key": "test_key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "sig_test"

    with patch("custom_components.kma_weather.coordinator.KMAWeatherAPI") as mock_api_cls:
        mock_api_cls.return_value = MagicMock()
        KMAWeatherUpdateCoordinator(hass, entry)

    mock_api_cls.assert_called_once()
    _, kwargs = mock_api_cls.call_args
    # reg_id_temp, reg_id_land가 생성자 인자로 전달되지 않아야 함
    assert "reg_id_temp" not in kwargs
    assert "reg_id_land" not in kwargs
    assert kwargs.get("hass") is hass


@pytest.mark.asyncio
async def test_coordinator_passes_area_codes_via_fetch_data(hass):
    """coordinator가 fetch_data에 reg_id와 warn_area_code를 올바르게 전달함"""
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "area_pass_test"
    hass.config.latitude = 37.56
    hass.config.longitude = 126.98

    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    coord._cached_data = {"weather": {}, "air": {}}

    captured = {}

    async def mock_fetch(lat, lon, nx, ny, reg_id_temp, reg_id_land, warn_area_code, pollen_area_no="1100000000", pollen_area_name=""):
        captured.update(locals())
        return None

    coord.api.fetch_data = mock_fetch
    await coord._async_update_data()

    assert "reg_id_temp" in captured
    assert "reg_id_land" in captured
    assert "warn_area_code" in captured
    assert captured["reg_id_temp"] is not None
    assert captured["warn_area_code"] is not None
