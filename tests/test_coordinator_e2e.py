import pytest
from unittest.mock import AsyncMock, MagicMock
from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

@pytest.mark.asyncio
async def test_coordinator_returns_cached_data_on_api_failure(hass):
    """시나리오: API 호출 실패 시 캐시된 데이터를 반환하여 서비스 연속성을 보장함"""
    
    # [Given] 코디네이터 설정 및 API 실패(None) 상황 시뮬레이션
    entry = MagicMock()
    entry.data = {"api_key": "test", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "e2e_cache_test"

    coordinator = KMAWeatherUpdateCoordinator(hass, entry)
    coordinator.api.fetch_data = AsyncMock(return_value=None)

    # [Given] 이전에 성공적으로 저장된 캐시 데이터(25도)가 존재할 때
    coordinator._cached_data = {
        "weather": {"TMP": 25},
        "air": {},
    }

    # [When] 데이터 업데이트 메서드를 호출하면
    result = await coordinator._async_update_data()

    # [Then] API 호출이 실패하더라도 기존 캐시값인 25도가 반환되어야 함
    assert result["weather"]["TMP"] == 25


@pytest.mark.asyncio
async def test_coordinator_returns_empty_dict_when_no_cache(hass):
    """시나리오: 캐시와 API가 모두 실패해도 빈 구조를 반환하여 엔티티 비정상 종료를 방지함"""
    
    # [Given] 코디네이터 설정 및 API 실패 상황 시뮬레이션
    entry = MagicMock()
    entry.data = {"api_key": "test", "location_entity": ""}
    entry.options = {}
    entry.entry_id = "e2e_no_cache_test"

    coordinator = KMAWeatherUpdateCoordinator(hass, entry)
    coordinator.api.fetch_data = AsyncMock(return_value=None)
    
    # [Given] 캐시 데이터가 전혀 없는 초기 상태일 때 (_cached_data is None)

    # [When] 데이터를 업데이트하려고 시도하면
    result = await coordinator._async_update_data()

    # [Then] 예외가 발생하지 않고 안정적으로 딕셔너리 형태를 유지해야 함
    assert isinstance(result, dict)
    assert result.get("weather") is not None or result == {"weather": {}, "air": {}}
