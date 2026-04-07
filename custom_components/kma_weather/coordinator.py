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
        
        self._last_lat = None
        self._last_lon = None
        self._last_nx = None
        self._last_ny = None
        self._last_api_update = None
        self._cached_data = None
        # ★ 동시성 제어용 락
        self._update_lock = asyncio.Lock()

    async def _async_update_data(self):
        """Fetch data with fail-safe cache fallback and concurrency control."""
        async with self._update_lock:
            try:
                entity_id = self.entry.data.get(CONF_LOCATION_ENTITY, "")
                state = self.hass.states.get(entity_id)

                lat, lon = None, None
                if state and "latitude" in state.attributes and "longitude" in state.attributes:
                    lat = float(state.attributes["latitude"])
                    lon = float(state.attributes["longitude"])
                elif self.hass.config.latitude:
                    lat = float(self.hass.config.latitude)
                    lon = float(self.hass.config.longitude)

                if lat is not None and lon is not None:
                    current_lat, current_lon = lat, lon
                elif self._last_lat is not None:
                    current_lat, current_lon = self._last_lat, self._last_lon
                else:
                    if self._cached_data: return self._cached_data
                    raise UpdateFailed("위치 정보 없음")

                current_nx, current_ny = convert_grid(current_lat, current_lon)
                now = datetime.now(timezone.utc)

                needs_api_call = False
                if not self._cached_data:
                    needs_api_call = True
                elif self._last_nx != current_nx or self._last_ny != current_ny:
                    needs_api_call = True
                elif self._last_api_update is None or (now - self._last_api_update) >= timedelta(hours=1):
                    needs_api_call = True

                if needs_api_call:
                    new_data = await self.api.fetch_data(current_lat, current_lon, current_nx, current_ny)
                    
                    if new_data is None:
                        # Fail-Safe: API 실패 시 캐시 유지
                        if self._cached_data: return self._cached_data
                        raise UpdateFailed("데이터 수집 실패")

                    # ★ KMALocationDebugSensor가 요구하는 필드들 정확히 매핑
                    new_data["weather"]["last_updated"] = now
                    new_data["weather"]["debug_nx"] = current_nx
                    new_data["weather"]["debug_ny"] = current_ny
                    new_data["weather"]["debug_lat"] = round(current_lat, 5)
                    new_data["weather"]["debug_lon"] = round(current_lon, 5)
                    new_data["weather"]["debug_reg_id_temp"] = self.entry.data.get(CONF_REG_ID_TEMP, "11B10101")
                    new_data["weather"]["debug_reg_id_land"] = self.entry.data.get(CONF_REG_ID_LAND, "11B00000")

                    self._cached_data = new_data
                    self._last_api_update = now
                    self._last_lat, self._last_lon = current_lat, current_lon
                    self._last_nx, self._last_ny = current_nx, current_ny
                    
                    return new_data
                
                return self._cached_data

            except Exception as e:
                if self._cached_data:
                    _LOGGER.warning("오류 발생, 캐시 데이터 유지: %s", e)
                    return self._cached_data
                raise UpdateFailed(f"업데이트 실패: {e}")
