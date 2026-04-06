"""DataUpdateCoordinator for KMA Weather."""
from datetime import timedelta
import logging

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .const import DOMAIN, CONF_LOCATION_ENTITY

_LOGGER = logging.getLogger(__name__)

class KMADataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching KMA Weather data."""

    def __init__(self, hass, api, entry):
        """Initialize."""
        self.api = api
        self.entry = entry
        super().__init__(
            hass, 
            _LOGGER, 
            name=DOMAIN, 
            update_interval=timedelta(minutes=30)
        )

    async def _async_update_data(self):
        """Update data via library."""
        lat, lon = self._get_current_coordinates()
        try:
            return await self.api.fetch_data(lat, lon)
        except Exception as exception:
            raise UpdateFailed(f"Error communicating with API: {exception}") from exception

    def _get_current_coordinates(self):
        """Get coordinates from the selected entity."""
        entity_id = self.entry.data.get(CONF_LOCATION_ENTITY)
        state = self.hass.states.get(entity_id)
        
        if state and "latitude" in state.attributes and "longitude" in state.attributes:
            return state.attributes["latitude"], state.attributes["longitude"]
            
        # 기본값: Home Assistant 설정 좌표
        return self.hass.config.latitude, self.hass.config.longitude
