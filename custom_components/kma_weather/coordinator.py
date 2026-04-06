"""DataUpdateCoordinator for KMA Weather."""
import logging
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .api_kma import KMAApiClient
from .const import DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY

_LOGGER = logging.getLogger(__name__)

class KMAWeatherUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching KMA Weather data."""

    def __init__(self, hass, entry):
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            # 사용자님의 기존 주기인 3시간을 유지 (수동 업데이트 버튼으로 즉시 갱신 가능)
            update_interval=timedelta(hours=3), 
        )
        self.entry = entry
        self.api = KMAApiClient(entry.data[CONF_API_KEY], async_get_clientsession(hass))

    async def _async_update_data(self):
        """Update data via KMA API."""
        try:
            # 1. 설정된 위치 엔티티(Zone 또는 device_tracker) 가져오기
            entity_id = self.entry.data.get(CONF_LOCATION_ENTITY, "")
            state = self.hass.states.get(entity_id)
            
            # 2. 기본 좌표 (서울 기준)
            lat, lon = 37.5665, 126.9780 
            
            # 3. [핵심] 업데이트가 실행될 때마다 엔티티의 최신 GPS를 동적으로 읽어옴 (이동 기기 완벽 지원)
            if state and "latitude" in state.attributes and "longitude" in state.attributes:
                lat = float(state.attributes["latitude"])
                lon = float(state.attributes["longitude"])

            # 4. 추적된 최신 좌표로 기상청 API 호출
            return await self.api.fetch_data(lat, lon)
            
        except Exception as exception:
            raise UpdateFailed(f"기상청 API 업데이트 실패: {exception}")
