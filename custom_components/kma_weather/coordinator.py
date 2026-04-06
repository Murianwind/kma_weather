"""DataUpdateCoordinator for KMA Weather."""
from datetime import timedelta
import logging

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .const import (
    DOMAIN, 
    CONF_LOCATION_TYPE, 
    LOCATION_TYPE_ZONE, 
    CONF_ZONE_ID, 
    CONF_MOBILE_DEVICE_ID
)

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
        """Get coordinates based on location type."""
        location_type = self.entry.data.get(CONF_LOCATION_TYPE)
        
        if location_type == LOCATION_TYPE_ZONE:
            entity_id = self.entry.data.get(CONF_ZONE_ID)
        else:
            entity_id = self.entry.data.get(CONF_MOBILE_DEVICE_ID)
            
        state = self.hass.states.get(entity_id)
        if state and "latitude" in state.attributes and "longitude" in state.attributes:
            return state.attributes["latitude"], state.attributes["longitude"]
            
        # Fallback to home location
        return self.hass.config.latitude, self.hass.config.longitude
