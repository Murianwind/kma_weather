import pytest
from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

# --- [Given] Mocking Classes (로직 보존) ---
class MockState:
    def __init__(self, attributes):
        self.attributes = attributes

class MockStates:
    def __init__(self, state):
        self._state = state
    def get(self, entity_id):
        return self._state

class MockConfig:
    latitude = 37.5665
    longitude = 126.9780

class MockHass:
    def __init__(self, state):
        self.states = MockStates(state)
        self.config = MockConfig()

class MockEntry:
    def __init__(self, data):
        self.data = data

class DummyCoordinator(KMAWeatherUpdateCoordinator):
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self._last_lat = None
        self._last_lon = None
        
    def _is_valid_korean_coord(self, lat, lon):
        # 대략적인 한반도 좌표 범위 모사 (33~39, 124~132)
        return 33.0 <= lat <= 39.0 and 124.0 <= lon <= 132.0


# --- [Tests] ---

@pytest.mark.asyncio
async def test_resolve_location_with_valid_entity():
    """시나리오: 엔티티에 유효한 좌표가 있는 경우 해당 좌표를 우선 사용함"""
    
    # [Given] 유효한 위경도 속성을 가진 엔티티 상태와 설정 정보가 준비되었을 때
    state = MockState({"latitude": 37.5665, "longitude": 126.9780})
    hass = MockHass(state)
    entry = MockEntry({"location_entity": "zone.home"})
    coordinator = DummyCoordinator(hass, entry)

    # [When] 좌표 해결(_resolve_location) 로직을 실행하면
    lat, lon = coordinator._resolve_location()

    # [Then] 엔티티에 설정된 위경도(37.5665, 126.9780)가 반환되어야 함
    assert lat == 37.5665
    assert lon == 126.9780


@pytest.mark.asyncio
async def test_resolve_location_missing_longitude():
    """시나리오: 엔티티 데이터 중 일부(경도)가 누락된 경우 HA 기본 좌표로 대체함"""
    
    # [Given] 위도는 있으나 경도가 누락된 상태 데이터가 주어졌을 때
    state = MockState({"latitude": 37.5665}) 
    hass = MockHass(state)
    entry = MockEntry({"location_entity": "zone.home"})
    coordinator = DummyCoordinator(hass, entry)

    # [When] 좌표 해결을 시도하면
    lat, lon = coordinator._resolve_location()

    # [Then] KeyError 없이 HA 설정의 기본 위경도로 Fallback 되어야 함
    assert lat == hass.config.latitude
    assert lon == hass.config.longitude


@pytest.mark.asyncio
async def test_resolve_location_invalid_coordinates():
    """시나리오: 좌표가 범위를 벗어난 유효하지 않은 값일 경우 HA 기본 좌표를 사용함"""
    
    # [Given] 한국 범위를 벗어난 좌표(0, 0)를 가진 엔티티 상태가 주어졌을 때
    state = MockState({"latitude": 0, "longitude": 0})
    hass = MockHass(state)
    entry = MockEntry({"location_entity": "zone.home"})
    coordinator = DummyCoordinator(hass, entry)

    # [When] 좌표 해결 로직이 작동하면
    lat, lon = coordinator._resolve_location()

    # [Then] 유효성 검사를 통과하지 못하므로 HA 시스템 기본 좌표가 반환되어야 함
    assert lat == hass.config.latitude
    assert lon == hass.config.longitude
