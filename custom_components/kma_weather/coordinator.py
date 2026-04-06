from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from .const import DOMAIN, CONF_LOCATION_TYPE, LOCATION_TYPE_ZONE, LOCATION_TYPE_MOBILE, CONF_ZONE_ID, CONF_MOBILE_DEVICE_ID

class KMADataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, api, entry):
        self.api = api
        self.entry = entry
        super().__init__(hass, name=DOMAIN, update_interval=timedelta(minutes=30))

    async def _async_update_data(self):
        lat, lon = self._get_current_coords()
        return await self.api.fetch_data(lat, lon)

    def _get_current_coords(self):
        loc_type = self.entry.data.get(CONF_LOCATION_TYPE)
        if loc_type == LOCATION_TYPE_ZONE:
            state = self.hass.states.get(self.entry.data.get(CONF_ZONE_ID))
        else:
            state = self.hass.states.get(self.entry.data.get(CONF_MOBILE_DEVICE_ID))
        
        if state and "latitude" in state.attributes:
            return state.attributes["latitude"], state.attributes["longitude"]
        return self.hass.config.latitude, self.hass.config.longitude
