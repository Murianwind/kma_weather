import logging
from datetime import datetime, timedelta, timezone # 피드백 1, 2번 반영
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_kma import KMAWeatherAPI
from .const import (
    DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY,
    CONF_REG_ID_TEMP, CONF_REG_ID_LAND, convert_grid
)

_LOGGER = logging.getLogger(__name__)

class KMAWeatherUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            update_interval=timedelta(hours=1),
        )
        self.entry = entry
        self.api = KMAWeatherAPI(
            session=async_get_clientsession(hass),
            api_key=entry.data.get(CONF_API_KEY),
            reg_id_temp=entry.data.get(CONF_REG_ID_TEMP, "11B10101"),
            reg_id_land=entry.data.get(CONF_REG_ID_LAND, "11B00000"),
        )

    async def _async_update_data(self):
        try:
            entity_id = self.entry.data.get(CONF_LOCATION_ENTITY, "")
            state = self.hass.states.get(entity_id)
            lat, lon = 37.5665, 126.9780

            if state and "latitude" in state.attributes:
                lat = float(state.attributes["latitude"])
                lon = float(state.attributes["longitude"])
            elif self.hass.config.latitude:
                lat = self.hass.config.latitude
                lon = self.hass.config.longitude

            nx, ny = convert_grid(lat, lon)
            data = await self.api.fetch_data(lat, lon, nx, ny)
            
            # 피드백 2번 반영: HA TIMESTAMP 규격을 위한 UTC datetime 객체 저장
            data["weather"]["last_updated"] = datetime.now(timezone.utc)
            
            return data
        except Exception as e:
            raise UpdateFailed(f"기상청 업데이트 실패: {e}")
