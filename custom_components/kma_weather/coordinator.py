import logging
import asyncio
from datetime import datetime, timedelta, timezone
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
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
            reg_id_land="11B00000",
        )
        # 마지막 성공 좌표 저장용 변수
        self._last_lat = None
        self._last_lon = None
        self._cached_data = None
        self._update_lock = asyncio.Lock()

    def _get_kma_reg_ids(self, lat, lon):
        """좌표 기반으로 중기 예보 구역 코드를 동적으로 산출합니다."""
        temp_id = "11B10101" # 기본값 서울
        if lat > 37.5:
            if lon < 127.5: temp_id = "11B10101"
            elif lon < 128.5: temp_id = "11D10301"
            else: temp_id = "11D20501"
        elif lat > 36.3:
            if lon < 127.2: temp_id = "11C20401"
            elif lon < 128.0: temp_id = "11C10301"
            else: temp_id = "11H10701"
        elif lat > 35.3:
            if lon < 127.5: temp_id = "11F10201"
            else: temp_id = "11H10701"
        elif lat > 34.0:
            if lon < 127.5: temp_id = "11F20501"
            else: temp_id = "11H20201"
        else: temp_id = "11G00201"

        def get_land_code(t_id):
            if t_id == "11A00101": return "11A00101"
            if t_id.startswith("11B"): return "11B00000"
            if t_id.startswith("11C1"): return "11C10000"
            if t_id.startswith("11C2"): return "11C20000"
            if t_id == "11E00101": return "11E00101"
            if t_id == "11E00102": return "11E00102"
            if t_id.startswith("11G"): return "11G00000"
            if t_id.startswith("11H1"): return "11H10000"
            if t_id.startswith("11H2"): return "11H20000"
            if t_id.startswith("11F1"): return "11F10000"
            if t_id.startswith("11F2"): return "11F20000"
            if t_id.startswith("11D1"): return "11D10000"
            if t_id.startswith("11D2"): return "11D20000"
            return "11B00000"

        return get_land_code(temp_id), temp_id

    async def _async_update_data(self):
        async with self._update_lock:
            try:
                entity_id = self.entry.data.get(CONF_LOCATION_ENTITY, "")
                state = self.hass.states.get(entity_id)
                
                curr_lat = None
                curr_lon = None

                # 1. 실시간 좌표 시도
                if state and state.attributes.get("latitude") and state.attributes.get("longitude"):
                    curr_lat = float(state.attributes["latitude"])
                    curr_lon = float(state.attributes["longitude"])
                
                # 2. 실시간 실패 시 마지막 성공 좌표(이전 값) 유지
                elif self._last_lat is not None and self._last_lon is not None:
                    curr_lat = self._last_lat
                    curr_lon = self._last_lon
                    _LOGGER.debug("위치 엔티티 응답 없음. 이전 좌표(%s, %s)를 유지합니다.", curr_lat, curr_lon)

                # 3. 최초 기동 시에만 HA 기본 좌표 사용
                else:
                    curr_lat = self.hass.config.latitude
                    curr_lon = self.hass.config.longitude

                if curr_lat is None or curr_lon is None:
                    return self._cached_data or {"weather": {}, "air": {}}

                # 좌표 확정 후 저장 (다음 업데이트 시 fallback용)
                self._last_lat, self._last_lon = curr_lat, curr_lon

                # 동적 구역 코드 매핑 및 API 업데이트
                reg_land, reg_temp = self._get_kma_reg_ids(curr_lat, curr_lon)
                self.api.reg_id_land = reg_land
                self.api.reg_id_temp = reg_temp
                nx, ny = convert_grid(curr_lat, curr_lon)

                new_data = await self.api.fetch_data(curr_lat, curr_lon, nx, ny)
                
                # API 실패 시에도 이전 성공 데이터(캐시) 반환
                if new_data is None: 
                    return self._cached_data or {"weather": {}, "air": {}}

                weather = new_data.setdefault("weather", {})
                weather.update({
                    "last_updated": datetime.now(timezone.utc),
                    "debug_nx": nx, "debug_ny": ny,
                    "debug_lat": round(curr_lat, 5), "debug_lon": round(curr_lon, 5),
                    "debug_reg_id_temp": reg_temp,
                    "debug_reg_id_land": reg_land,
                    "debug_station": new_data.get("air", {}).get("station", "정보없음")
                })
                self._cached_data = new_data
                return new_data

            except Exception as e:
                _LOGGER.warning("업데이트 실패(이전 데이터 유지): %s", e)
                return self._cached_data or {"weather": {}, "air": {}}
