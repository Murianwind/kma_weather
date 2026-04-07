import logging
import asyncio
from datetime import datetime, timedelta, timezone
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
            update_interval=timedelta(minutes=5),
        )
        self.entry = entry
        self.api = KMAWeatherAPI(
            session=async_get_clientsession(hass),
            api_key=entry.data.get(CONF_API_KEY),
            reg_id_temp=entry.data.get(CONF_REG_ID_TEMP, "11B10101"),
            reg_id_land=entry.data.get(CONF_REG_ID_LAND, "11B00000"),
        )
        self._last_lat = self._last_lon = self._last_nx = self._last_ny = None
        self._last_api_update = self._cached_data = None
        self._update_lock = asyncio.Lock()

    async def _async_update_data(self):
        async with self._update_lock:
            try:
                entity_id = self.entry.data.get(CONF_LOCATION_ENTITY, "")
                state = self.hass.states.get(entity_id)
                lat, lon = None, None
                if state and "latitude" in state.attributes and "longitude" in state.attributes:
                    lat, lon = float(state.attributes["latitude"]), float(state.attributes["longitude"])
                elif self.hass.config.latitude:
                    lat, lon = float(self.hass.config.latitude), float(self.hass.config.longitude)

                if lat is not None and lon is not None:
                    curr_lat, curr_lon = lat, lon
                elif self._last_lat is not None:
                    curr_lat, curr_lon = self._last_lat, self._last_lon
                else:
                    if self._cached_data: return self._cached_data
                    raise UpdateFailed("위치 정보 없음")

                curr_nx, curr_ny = convert_grid(curr_lat, curr_lon)
                now = datetime.now(timezone.utc)
                needs_call = not self._cached_data or (self._last_nx != curr_nx or self._last_ny != curr_ny) or \
                             (self._last_api_update is None or (now - self._last_api_update) >= timedelta(hours=1))

                if needs_call:
                    new_data = await self.api.fetch_data(curr_lat, curr_lon, curr_nx, curr_ny)
                    if new_data is None:
                        if self._cached_data: return self._cached_data
                        raise UpdateFailed("API 호출 실패")

                    new_data["weather"].update({
                        "last_updated": now, "debug_nx": curr_nx, "debug_ny": curr_ny,
                        "debug_lat": round(curr_lat, 5), "debug_lon": round(curr_lon, 5),
                        "debug_reg_id_temp": self.entry.data.get(CONF_REG_ID_TEMP, "11B10101"),
                        "debug_reg_id_land": self.entry.data.get(CONF_REG_ID_LAND, "11B00000"),
                    })
                    self._cached_data, self._last_api_update = new_data, now
                    self._last_lat, self._last_lon, self._last_nx, self._last_ny = curr_lat, curr_lon, curr_nx, curr_ny
                    return new_data
                return self._cached_data
            except Exception as e:
                if self._cached_data:
                    _LOGGER.warning("업데이트 오류 발생, 캐시 유지: %s", e)
                    return self._cached_data
                raise UpdateFailed(f"업데이트 실패: {e}")
