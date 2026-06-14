"""
test_coordinator_scenarios.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[통합] test_coordinator.py + test_coordinator_e2e.py + test_missing_coverage.py

검증 대상:
  - 정상 데이터 / 누락 데이터 시나리오에서 통합 구성요소 로드
  - API 실패 시 캐시 반환 (서비스 연속성)
  - 좌표 없음 분기 (228->exit)
  - fetch_data None + 캐시 있음 분기 (235->exit)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from custom_components.kma_weather.const import DOMAIN
from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator


# ─────────────────────────────────────────────────────────────────────────────
# 1. 통합 구성요소 셋업 시나리오
# ─────────────────────────────────────────────────────────────────────────────

class TestSetupScenarios:
    """정상/누락 데이터 시나리오에서 HA 통합 구성요소 로드 검증"""

    @pytest.mark.asyncio
    async def test_normal_data_registers_coordinator(self, hass, mock_config_entry, kma_api_mock_factory):
        """
        [Given] 서울 좌표 + 정상 데이터(full_test) 시나리오
        [When]  통합 구성요소를 HA에 추가하고 셋업하면
        [Then]  coordinator가 DOMAIN 데이터 영역에 정상 등록되어야 함
        """
        hass.config.latitude = 37.56
        hass.config.longitude = 126.98
        kma_api_mock_factory("full_test")

        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.entry_id in hass.data[DOMAIN]

    @pytest.mark.asyncio
    async def test_missing_data_still_loads_without_error(self, hass, mock_config_entry, kma_api_mock_factory):
        """
        [Given] 제주 좌표 + 데이터 누락 시나리오(jeju_missing)
        [When]  통합 구성요소를 등록하고 셋업하면
        [Then]  데이터 불완전 상태에서도 오류 없이 로드되어야 함
        """
        hass.config.latitude = 33.51
        hass.config.longitude = 126.52
        kma_api_mock_factory("jeju_missing")

        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.entry_id in hass.data[DOMAIN]


# ─────────────────────────────────────────────────────────────────────────────
# 2. API 실패 시 캐시 반환 (서비스 연속성)
# ─────────────────────────────────────────────────────────────────────────────

class TestCacheFallback:
    """API 호출 실패 시 캐시 데이터로 서비스 연속성 보장"""

    def _make_coordinator(self, hass, entry_id="cache_test"):
        entry = MagicMock()
        entry.data = {"api_key": "test", "location_entity": ""}
        entry.options = {}
        entry.entry_id = entry_id
        return KMAWeatherUpdateCoordinator(hass, entry)

    @pytest.mark.asyncio
    async def test_returns_cached_data_when_api_fails(self, hass):
        """
        [Given] 이전에 성공한 캐시 데이터(TMP=25)가 존재
        [When]  fetch_data가 None을 반환(API 실패)하면
        [Then]  캐시값인 TMP=25가 반환되어야 함
        """
        coord = self._make_coordinator(hass)
        coord.api.fetch_data = AsyncMock(return_value=None)
        coord._cached_data = {"weather": {"TMP": 25}, "air": {}}

        result = await coord._async_update_data()

        assert result["weather"]["TMP"] == 25

    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_no_cache_and_api_fails(self, hass):
        """
        [Given] 캐시 없음(초기 상태) + API 실패
        [When]  _async_update_data 호출
        [Then]  예외 없이 dict 형태 반환 (엔티티 비정상 종료 방지)
        """
        coord = self._make_coordinator(hass, "no_cache_test")
        coord.api.fetch_data = AsyncMock(return_value=None)
        # _cached_data = None (초기 상태)

        result = await coord._async_update_data()

        assert isinstance(result, dict)


# ─────────────────────────────────────────────────────────────────────────────
# 3. 좌표 없음 분기 (coordinator.py 228->exit)
# ─────────────────────────────────────────────────────────────────────────────

class TestNoLocationBranch:
    """유효한 한반도 좌표를 얻지 못했을 때의 분기 검증"""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_location_and_no_cache(self, hass):
        """
        [Given] 위치 엔티티 없음 + HA 좌표가 한반도 범위 밖(0, 0) + 캐시 없음
        [When]  _async_update_data 호출
        [Then]  빈 weather/air dict 반환 (228->exit 분기)
        """
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "zone.nonexistent"}
        entry.options = {}
        entry.entry_id = "no_loc_228"

        coord = KMAWeatherUpdateCoordinator(hass, entry)
        coord._store_loaded = True
        hass.config.latitude = 0.0
        hass.config.longitude = 0.0
        coord._cached_data = None

        result = await coord._async_update_data()

        assert result == {"weather": {}, "air": {}}

    @pytest.mark.asyncio
    async def test_returns_cache_when_no_location_but_cache_exists(self, hass):
        """
        [Given] 위치 없음 + 이전 캐시 데이터(TMP=18) 존재
        [When]  _async_update_data 호출
        [Then]  캐시 데이터 반환 (위치 없어도 서비스 유지)
        """
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "zone.nonexistent"}
        entry.options = {}
        entry.entry_id = "no_loc_cache"

        coord = KMAWeatherUpdateCoordinator(hass, entry)
        coord._store_loaded = True
        hass.config.latitude = 0.0
        hass.config.longitude = 0.0
        coord._cached_data = {"weather": {"TMP": 18}, "air": {}}

        result = await coord._async_update_data()

        assert result["weather"]["TMP"] == 18


# ─────────────────────────────────────────────────────────────────────────────
# 4. fetch_data None 반환 + 캐시 있음 (coordinator.py 235->exit)
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchNoneWithCache:
    """유효한 좌표 있지만 fetch_data가 None 반환할 때 캐시 사용"""

    @pytest.mark.asyncio
    async def test_returns_cached_data_when_fetch_returns_none(self, hass):
        """
        [Given] 유효한 한반도 좌표 + fetch_data → None + 캐시(TMP=21) 존재
        [When]  _async_update_data 호출
        [Then]  캐시 그대로 반환 (235->exit 분기)
        """
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": ""}
        entry.options = {}
        entry.entry_id = "fetch_none_235"

        coord = KMAWeatherUpdateCoordinator(hass, entry)
        coord._store_loaded = True
        hass.config.latitude = 37.56
        hass.config.longitude = 126.98

        coord.api.fetch_data = AsyncMock(return_value=None)
        coord._cached_data = {"weather": {"TMP": 21}, "air": {"pm10Value": 30}}

        result = await coord._async_update_data()

        assert result["weather"]["TMP"] == 21
        assert result["air"]["pm10Value"] == 30
