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
            update_interval=timedelta(hours=3),
        )
        self.entry = entry
        # API 키를 안전하게 가져오도록 get() 사용
        self.api = KMAApiClient(entry.data.get(CONF_API_KEY), async_get_clientsession(hass))

    async def _async_update_data(self):
        """Update data via KMA API."""
        try:
            # 1. 위치 엔티티 가져오기
            entity_id = self.entry.data.get(CONF_LOCATION_ENTITY, "")
            state = self.hass.states.get(entity_id)

            # 2. 기본 좌표 (서울 중심)
            lat, lon = 37.5665, 126.9780

            # 3. 모바일 기기(device_tracker)일 경우 최신 GPS 추적
            if state and "latitude" in state.attributes and "longitude" in state.attributes:
                lat = float(state.attributes["latitude"])
                lon = float(state.attributes["longitude"])

            # 4. API 호출
            return await self.api.fetch_data(lat, lon)

        except Exception as exception:
            raise UpdateFailed(f"기상청 API 업데이트 실패: {exception}")
