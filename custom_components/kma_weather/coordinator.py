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
        # 3️⃣ 핵심 수정: 동시성 제어를 위한 락 추가
        self._update_lock = asyncio.Lock()

    async def _async_update_data(self):
        async with self._update_lock:
            try:
                # (기존 위치 획득 및 격자 변환 로직 유지)
                # ...
                
                # 4️⃣ 핵심 수정: API 호출 결과가 None(실패)이면 캐시 데이터 즉시 반환
                new_data = await self.api.fetch_data(current_lat, current_lon, current_nx, current_ny)
                
                if new_data is None:
                    if self._cached_data:
                        _LOGGER.info("새 데이터를 가져오지 못해 캐시된 데이터를 사용합니다.")
                        return self._cached_data
                    raise UpdateFailed("데이터 수집 실패 및 기존 캐시 없음")

                # (성공 시 캐시 업데이트 로직 유지)
                self._cached_data = new_data
                return new_data

            except Exception as e:
                if self._cached_data:
                    _LOGGER.warning("업데이트 중 오류 발생, 캐시 유지: %s", e)
                    return self._cached_data
                raise UpdateFailed(f"업데이트 실패: {e}")
