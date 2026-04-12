"""
coordinator.py 의 마지막 미커버 분기 2개를 커버하는 테스트.

228->exit : _async_update_data 내에서 curr_lat이 None일 때
             self._cached_data or {"weather": {}, "air": {}} 를 반환하는 분기.

235->exit : fetch_data가 None을 반환하고 _cached_data도 있는 경우
             return self._cached_data 경로.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_update_returns_cached_when_no_location(hass):
    # Given: 위치 엔티티가 없고 HA config 좌표도 한반도 범위 밖(0,0)이며
    #        캐시 데이터 유무에 따라 두 가지 케이스를 확인한다
    # When:  _async_update_data를 호출하면
    # Then:  캐시가 없으면 빈 dict, 캐시가 있으면 캐시 데이터를 반환한다
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": "zone.nonexistent"}
    entry.options = {}
    entry.entry_id = "no_loc_228"

    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    hass.config.latitude = 0.0
    hass.config.longitude = 0.0

    # 케이스 1: 캐시 없음
    coord._cached_data = None
    result = await coord._async_update_data()
    assert result == {"weather": {}, "air": {}}, f"캐시 없을 때 빈 dict 기대, 실제={result}"

    # 케이스 2: 캐시 있음
    coord._cached_data = {"weather": {"TMP": 18}, "air": {}}
    result = await coord._async_update_data()
    assert result["weather"]["TMP"] == 18, "캐시가 있으면 캐시 반환해야 함"


@pytest.mark.asyncio
async def test_update_returns_cached_when_fetch_returns_none_with_cache(hass):
    # Given: 유효한 한반도 좌표가 있고 fetch_data가 None을 반환하는 상황이며
    #        _store_loaded=True로 설정해 _restore_daily_temps 조기 반환을 보장한다
    # When:  _async_update_data를 호출하면
    # Then:  235번 분기(if not new_data)가 True여서 _cached_data를 그대로 반환한다
    from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

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
