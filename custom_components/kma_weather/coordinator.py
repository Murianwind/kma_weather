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
        self.api = KMAWeatherAPI(session=async_get_clientsession(hass), api_key=entry.data.get(CONF_API_KEY), reg_id_temp="11B10101", reg_id_land="11B00000")
        self._last_lat = self._last_lon = None
        self._cached_data = None
        self._update_lock = asyncio.Lock()

    def _get_kma_reg_ids(self, lat, lon):
        """좌표 기반 중기예보 구역 매핑 (정밀화 버전)."""
        # 1. 기온 구역(temp_id) 세부 매핑
        if lat > 37.5: # 수도권 / 강원 북부
            if lon < 127.5: t_id = "11B10101" # 서울/인천/경기
            elif lon < 128.5: t_id = "11D10301" # 강원영서
            else: t_id = "11D20501" # 강원영동
        elif lat > 36.3: # 충청 / 강원 남부
            if lon < 127.2: t_id = "11C20401" # 대전/세종/충남
            elif lon < 128.0: t_id = "11C10301" # 충북
            else: t_id = "11H10701" # 경북북부
        elif lat > 35.3: # 전북 / 경북 남부
            if lon < 127.5: t_id = "11F10201" # 전북
            else: t_id = "11H10501" # 대구/경북남부
        elif lat > 33.7: # 전남 / 경남 / 남해안
            if lon < 127.5: t_id = "11F20501" # 광주/전남
            else: t_id = "11H20201" # 부산/울산/경남
        else: # 제주도 (위도 33.7 미만)
            t_id = "11G00201"

        def get_land_code(temp_id):
            if temp_id.startswith("11B"): return "11B00000" # 수도권
            if temp_id.startswith("11D1"): return "11D10000" # 강원영서
            if temp_id.startswith("11D2"): return "11D20000" # 강원영동
            if temp_id.startswith("11C1"): return "11C10000" # 충북
            if temp_id.startswith("11C2"): return "11C20000" # 충남
            if temp_id.startswith("11F1"): return "11F10000" # 전북
            if temp_id.startswith("11F2"): return "11F20000" # 전남
            if temp_id.startswith("11H1"): return "11H10000" # 경북
            if temp_id.startswith("11H2"): return "11H20000" # 경남
            if temp_id.startswith("11G"): return "11G00000" # 제주
            return "11B00000"

        # (temp_id, land_id) 순서로 명확히 반환
        return t_id, get_land_code(t_id)

    async def _async_update_data(self):
        async with self._update_lock:
            try:
                entity_id = self.entry.data.get(CONF_LOCATION_ENTITY, "")
                state = self.hass.states.get(entity_id)
                lat = float(state.attributes.get("latitude")) if (state and state.attributes.get("latitude")) else self.hass.config.latitude
                lon = float(state.attributes.get("longitude")) if (state and state.attributes.get("longitude")) else self.hass.config.longitude

                if lat is None or lon is None:
                    return self._cached_data or {"weather": {}, "air": {}}

                # 순서 보정: temp, land 순
                reg_temp, reg_land = self._get_kma_reg_ids(lat, lon)
                self.api.reg_id_temp = reg_temp
                self.api.reg_id_land = reg_land
                nx, ny = convert_grid(lat, lon)

                new_data = await self.api.fetch_data(lat, lon, nx, ny)
                if new_data is None: return self._cached_data or {"weather": {}, "air": {}}

                weather = new_data.setdefault("weather", {})
                weather.update({
                    "last_updated": datetime.now(timezone.utc),
                    "debug_nx": nx, "debug_ny": ny,
                    "debug_lat": round(lat, 5), "debug_lon": round(lon, 5),
                    "debug_reg_id_temp": reg_temp,
                    "debug_reg_id_land": reg_land,
                })
                self._cached_data = new_data
                return new_data
            except Exception as e:
                _LOGGER.warning("KMA 업데이트 실패: %s", e)
                return self._cached_data or {"weather": {}, "air": {}}
