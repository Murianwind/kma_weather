import logging
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
            update_interval=timedelta(hours=1),
        )
        self.entry = entry
        self.api = KMAWeatherAPI(
            session=async_get_clientsession(hass),
            api_key=entry.data.get(CONF_API_KEY),
            reg_id_temp=entry.data.get(CONF_REG_ID_TEMP, "11B10101"),
            reg_id_land=entry.data.get(CONF_REG_ID_LAND, "11B00000"),
        )
        # [6번] 마지막 유효 좌표 캐시 — 기본값 제거, None으로 초기화
        self._last_lat = None
        self._last_lon = None
        self._last_nx = None
        self._last_ny = None

    async def _async_update_data(self):
        try:
            entity_id = self.entry.data.get(CONF_LOCATION_ENTITY, "")
            state = self.hass.states.get(entity_id)

            lat, lon = None, None

            # 엔티티에서 좌표 획득 시도
            if state and "latitude" in state.attributes and "longitude" in state.attributes:
                lat = float(state.attributes["latitude"])
                lon = float(state.attributes["longitude"])
            elif self.hass.config.latitude:
                lat = float(self.hass.config.latitude)
                lon = float(self.hass.config.longitude)

            # [6번] 유효한 좌표가 있으면 캐시 갱신, 없으면 캐시 사용
            if lat is not None and lon is not None:
                self._last_lat = lat
                self._last_lon = lon
                self._last_nx, self._last_ny = convert_grid(lat, lon)
            elif self._last_lat is not None:
                _LOGGER.warning("위치 정보를 가져올 수 없어 이전 좌표를 사용합니다.")
                lat, lon = self._last_lat, self._last_lon
            else:
                raise UpdateFailed("위치 정보가 없습니다. 엔티티 상태를 확인하세요.")

            nx, ny = self._last_nx, self._last_ny

            data = await self.api.fetch_data(lat, lon, nx, ny)
            data["weather"]["last_updated"] = datetime.now(timezone.utc)

            # [5번] 디버그용 메타 정보 저장
            data["weather"]["debug_nx"] = nx
            data["weather"]["debug_ny"] = ny
            data["weather"]["debug_lat"] = round(lat, 5)
            data["weather"]["debug_lon"] = round(lon, 5)
            data["weather"]["debug_reg_id_temp"] = self.entry.data.get(CONF_REG_ID_TEMP, "11B10101")
            data["weather"]["debug_reg_id_land"] = self.entry.data.get(CONF_REG_ID_LAND, "11B00000")
            data["weather"]["debug_station"] = data.get("air", {}).get("station", "정보없음")

            # [2번] 성공한 데이터를 캐시로 보존
            self._cached_data = data
            return data

        except UpdateFailed:
            raise
        except Exception as e:
            # [2번] 실패 시 이전 데이터 유지
            if hasattr(self, '_cached_data') and self._cached_data:
                _LOGGER.warning("API 업데이트 실패, 이전 데이터를 유지합니다: %s", e)
                return self._cached_data
            raise UpdateFailed(f"기상청 업데이트 실패: {e}")
