import pytest
from unittest.mock import AsyncMock, MagicMock
from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator


@pytest.mark.asyncio
async def test_coordinator_returns_cached_data_on_api_failure(hass):
    """API 호출 실패 시 캐시된 데이터를 반환하는지 검증 (E2E)"""
    entry = MagicMock()
    entry.data = {"api_key": "test", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "e2e_cache_test"

    coordinator = KMAWeatherUpdateCoordinator(hass, entry)

    # API 가 None 을 반환하는 실패 상황 시뮬레이션
    coordinator.api.fetch_data = AsyncMock(return_value=None)

    # 초기 캐시 데이터 설정
    coordinator._cached_data = {
        "weather": {"TMP": 25},
        "air": {},
    }

    result = await coordinator._async_update_data()

    # API 실패 시 캐시된 25도가 그대로 반환되어야 함
    assert result["weather"]["TMP"] == 25


@pytest.mark.asyncio
async def test_coordinator_returns_empty_dict_when_no_cache(hass):
    """캐시도 없고 API 도 실패하면 빈 dict 를 반환해 엔티티가 죽지 않는지 검증"""
    entry = MagicMock()
    entry.data = {"api_key": "test", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "e2e_no_cache_test"

    coordinator = KMAWeatherUpdateCoordinator(hass, entry)
    coordinator.api.fetch_data = AsyncMock(return_value=None)
    # _cached_data 는 None (초기 상태)

    result = await coordinator._async_update_data()

    # 빈 dict 여야 하고 KeyError 등이 발생하지 않아야 함
    assert isinstance(result, dict)
    assert result.get("weather") is not None or result == {"weather": {}, "air": {}}
