# tests/test_coordinator_e2e.py

import pytest
from unittest.mock import AsyncMock, MagicMock
from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

@pytest.mark.asyncio
async def test_coordinator_returns_cached_data_on_api_failure(hass):
    """API 호출 실패 시 캐시된 데이터를 반환하는지 검증 (E2E)"""
    entry = MagicMock()
    entry.data = {"api_key": "test", "location_entity": ""}

    coordinator = KMAWeatherUpdateCoordinator(hass, entry)
    
    # API가 실패(None 반환)하는 상황 시뮬레이션
    coordinator.api.fetch_data = AsyncMock(return_value=None)

    # 초기 캐시 데이터 설정
    coordinator._cached_data = {
        "weather": {"TMP": 25},
        "air": {},
    }

    result = await coordinator._async_update_data()
    
    # API가 실패했음에도 캐시된 25도가 반환되어야 함
    assert result["weather"]["TMP"] == 25

def test_get_kma_reg_ids_logic():
    """좌표를 통한 기상청 구역 코드 매핑 로직 검증"""
    from custom_components.kma_weather.coordinator import _get_kma_reg_ids

    # 서울 시청 좌표
    reg_temp, reg_land = _get_kma_reg_ids(37.5665, 126.9780)
    assert reg_temp == "11B10101" # 서울 코드
    assert reg_land == "11B00000" # 서울/인천/경기 코드

def test_valid_korean_coord_boundary():
    """한반도 유효 좌표 범위 검증"""
    from custom_components.kma_weather.coordinator import _is_valid_korean_coord

    assert _is_valid_korean_coord(37.5665, 126.9780) is True # 서울
    assert _is_valid_korean_coord(33.1, 126.2) is True     # 이어도/제주 인근
    assert _is_valid_korean_coord(0.0, 0.0) is False       # 적도
