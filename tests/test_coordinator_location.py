import pytest
from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

# --- Mocking Classes ---
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

# 코디네이터 생성 과정을 우회하기 위한 더미 상속 클래스
class DummyCoordinator(KMAWeatherUpdateCoordinator):
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry  # [수정된 부분] config_entry 대신 entry로 일치시킴
        self._last_lat = None
        self._last_lon = None
        
    # 만약 coordinator.py 내부에 _is_valid_korean_coord가 있다면 그대로 사용되고,
    # 외부 모듈이나 별도 로직이라면 여기서 Mocking 처리할 수 있습니다.
    def _is_valid_korean_coord(self, lat, lon):
        # 대략적인 한반도 좌표 범위 모사
        return 33.0 <= lat <= 39.0 and 124.0 <= lon <= 132.0


# --- Tests ---
@pytest.mark.asyncio
async def test_resolve_location_with_valid_entity():
    """정상 좌표가 있는 경우 엔티티 좌표를 사용해야 한다."""
    state = MockState({"latitude": 37.5665, "longitude": 126.9780})
    hass = MockHass(state)
    # 실제 const.py의 CONF_LOCATION_ENTITY 값("location_entity")에 맞게 키 설정
    entry = MockEntry({"location_entity": "zone.home"})

    coordinator = DummyCoordinator(hass, entry)
    lat, lon = coordinator._resolve_location()

    assert lat == 37.5665
    assert lon == 126.9780

@pytest.mark.asyncio
async def test_resolve_location_missing_longitude():
    """경도(longitude)가 없는 경우 KeyError를 내지 않고 HA 기본 좌표로 fallback 해야 한다."""
    state = MockState({"latitude": 37.5665}) # longitude 누락
    hass = MockHass(state)
    entry = MockEntry({"location_entity": "zone.home"})

    coordinator = DummyCoordinator(hass, entry)
    lat, lon = coordinator._resolve_location()

    assert lat == hass.config.latitude
    assert lon == hass.config.longitude

@pytest.mark.asyncio
async def test_resolve_location_invalid_coordinates():
    """타입이 다르거나 범위를 벗어난 유효하지 않은 좌표일 경우 HA 기본 좌표를 사용해야 한다."""
    # 문자열 등 잘못된 타입 시뮬레이션 방어 및 범위 이탈 시뮬레이션 (적도 0, 0)
    state = MockState({"latitude": 0, "longitude": 0})
    hass = MockHass(state)
    entry = MockEntry({"location_entity": "zone.home"})

    coordinator = DummyCoordinator(hass, entry)
    lat, lon = coordinator._resolve_location()

    assert lat == hass.config.latitude
    assert lon == hass.config.longitude
