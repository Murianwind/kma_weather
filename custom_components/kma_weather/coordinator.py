from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from .const import *

class KMADataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, api, entry):
        self.api = api
        self.entry = entry
        super().__init__(hass, name=f"{DOMAIN}_{entry.entry_id}", update_interval=timedelta(minutes=30))

    def _get_current_coords(self):
        loc_type = self.entry.data.get(CONF_LOCATION_TYPE)
        # 1. Zone 기반일 경우
        if loc_type == LOCATION_TYPE_ZONE:
            entity_id = self.entry.data.get(CONF_ZONE_ID)
            state = self.hass.states.get(entity_id)
        # 2. 모바일 기기 기반일 경우
        else:
            entity_id = self.entry.data.get(CONF_MOBILE_DEVICE_ID)
            state = self.hass.states.get(entity_id)
        
        if state:
            # 모바일 앱의 location 속성 [lat, lon] 또는 일반 속성 확인
            attr = state.attributes
            if "location" in attr: return attr["location"][0], attr["location"][1]
            if "latitude" in attr: return attr["latitude"], attr["longitude"]
            
        return self.hass.config.latitude, self.hass.config.longitude

    async def _async_update_data(self):
        lat, lon = self._get_current_coords()
        return await self.api.fetch_data(lat, lon)
