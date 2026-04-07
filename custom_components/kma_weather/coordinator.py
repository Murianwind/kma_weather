import logging
import asyncio
from datetime import datetime, timedelta, timezone
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .api_kma import KMAWeatherAPI
from .const import (DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY, CONF_REG_ID_TEMP, CONF_REG_ID_LAND, convert_grid)

_LOGGER = logging.getLogger(__name__)

class KMAWeatherUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(hours=1))
        self.entry = entry
        self.api = KMAWeatherAPI(session=async_get_clientsession(hass), api_key=entry.data.get(CONF_API_KEY), reg_id_temp=entry.data.get(CONF_REG_ID_TEMP, "11B10101"), reg_id_land=entry.data.get(CONF_REG_ID_LAND, "11B00000"))
        self._last_lat = self._last_lon = self._last_nx = self._last_ny = None
        self._cached_data = None
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
                    self._last_lat, self._last_lon = lat, lon
                    self._last_nx, self._last_ny = convert_grid(lat, lon)
                elif self._last_lat is not None:
                    lat, lon = self._last_lat, self._last_lon
                else:
                    # 위치 정보 자체가 없는 초기 단계 세이프 가드
                    return self._cached_data or {"weather": {}, "air": {}}

                new_data = await self.api.fetch_data(lat, lon, self._last_nx, self._last_ny)
                
                # ★ 초기 실행 실패 방어 (Fail-Safe)
                if new_data is None:
                    return self._cached_data or {"weather": {}, "air": {}}

                # ★ 구조 무결성 강제 보장 (update() 크래시 방어)
                if not isinstance(new_data, dict): new_data = {}
                if not isinstance(new_data.get("weather"), dict):
                    new_data["weather"] = {}

                new_data["weather"].update({
                    "last_updated": datetime.now(timezone.utc),
                    "debug_nx": self._last_nx, "debug_ny": self._last_ny,
                    "debug_lat": round(lat, 5), "debug_lon": round(lon, 5),
                    "debug_reg_id_temp": self.entry.data.get(CONF_REG_ID_TEMP, "11B10101"),
                    "debug_reg_id_land": self.entry.data.get(CONF_REG_ID_LAND, "11B00000"),
                    "debug_station": new_data.get("air", {}).get("station", "정보없음")
                })
                self._cached_data = new_data
                return new_data
            except Exception as e:
                _LOGGER.warning("업데이트 중 예외 발생, 시스템을 보호합니다: %s", e)
                return self._cached_data or {"weather": {}, "air": {}}
