import logging
import asyncio
from datetime import datetime, timedelta, timezone
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .api_kma import KMAWeatherAPI
from .const import DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY, convert_grid

_LOGGER = logging.getLogger(__name__)

class KMAWeatherUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(hours=1))
        self.entry = entry
        self.api = KMAWeatherAPI(
            session=async_get_clientsession(hass), 
            api_key=entry.data.get(CONF_API_KEY), 
            reg_id_temp="11B10101", 
            reg_id_land="11B00000"
        )
        self._last_lat = self._last_lon = None
        self._last_reg_temp = "11B10101"
        self._last_reg_land = "11B00000"
        self._cached_data = None
        self._update_lock = asyncio.Lock()

    def _get_kma_reg_ids(self, lat, lon):
        """좌표 기반 중기예보 구역 매핑."""
        t_id = "11B10101"
        if lat > 37.5:
            if lon < 127.5: t_id = "11B10101"
            elif lon < 128.5: t_id = "11D10301"
            else: t_id = "11D20501"
        elif lat > 36.3:
            if lon < 127.2: t_id = "11C20401"
            elif lon < 128.0: t_id = "11C10301"
            else: t_id = "11H10701"
        elif lat > 35.3:
            if lon < 127.5: t_id = "11F10201"
            else: t_id = "11H10501"
        elif lat > 33.7:
            if lon < 127.5: t_id = "11F20501"
            else: t_id = "11H20201"
        else: t_id = "11G00201"

        def get_land_code(tid):
            mapping = {"11B": "11B00000", "11D1": "11D10000", "11D2": "11D20000", "11C1": "11C10000", "11C2": "11C20000", "11F1": "11F10000", "11F2": "11F20000", "11H1": "11H10000", "11H2": "11H20000", "11G": "11G00000"}
            for key, val in mapping.items():
                if tid.startswith(key): return val
            return "11B00000"

        return t_id, get_land_code(t_id)

    async def _async_update_data(self):
        async with self._update_lock:
            try:
                entity_id = self.entry.data.get(CONF_LOCATION_ENTITY, "")
                state = self.hass.states.get(entity_id)
                
                if state and state.attributes.get("latitude"):
                    curr_lat = float(state.attributes["latitude"])
                    curr_lon = float(state.attributes["longitude"])
                    reg_temp, reg_land = self._get_kma_reg_ids(curr_lat, curr_lon)
                    self._last_lat, self._last_lon = curr_lat, curr_lon
                    self._last_reg_temp, self._last_reg_land = reg_temp, reg_land
                elif self._last_lat is not None:
                    curr_lat, curr_lon = self._last_lat, self._last_lon
                    reg_temp, reg_land = self._last_reg_temp, self._last_reg_land
                else:
                    curr_lat, curr_lon = self.hass.config.latitude, self.hass.config.longitude
                    reg_temp, reg_land = self._get_kma_reg_ids(curr_lat, curr_lon)

                self.api.reg_id_temp, self.api.reg_id_land = reg_temp, reg_land
                nx, ny = convert_grid(curr_lat, curr_lon)

                new_data = await self.api.fetch_data(curr_lat, curr_lon, nx, ny)
                
                # 4. 센서 유실 방지: 데이터가 아예 없으면 캐시 사용
                if new_data is None:
                    return self._cached_data or {"weather": {}, "air": {}}

                weather = new_data.setdefault("weather", {})
                weather.update({
                    "last_updated": datetime.now(timezone.utc),
                    "debug_nx": nx, "debug_ny": ny,
                    "debug_lat": round(curr_lat, 5), "debug_lon": round(curr_lon, 5),
                    "debug_reg_id_temp": reg_temp, "debug_reg_id_land": reg_land,
                })
                self._cached_data = new_data
                return new_data
            except Exception as e:
                _LOGGER.warning("업데이트 중 오류 발생: %s", e)
                return self._cached_data or {"weather": {}, "air": {}}
