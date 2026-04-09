import logging
import asyncio
import math
from datetime import datetime, timedelta, timezone
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from .api_kma import KMAWeatherAPI
from .const import DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY, convert_grid

_LOGGER = logging.getLogger(__name__)

# --- 중기예보 구역코드 좌표 테이블 ---
_TEMP_ID_COORDS: dict[str, tuple[float, float]] = {
    "11A00101": (37.96, 124.71), "11B10101": (37.56, 126.98), "11B10102": (37.43, 126.99),
    "11B10103": (37.48, 126.87), "11B20101": (37.74, 126.49), "11B20102": (37.61, 126.71),
    "11B20201": (37.46, 126.70), "11B20202": (37.38, 126.80), "11B20203": (37.32, 126.83),
    "11B20204": (37.50, 126.78), "11B20301": (37.74, 127.03), "11B20302": (37.66, 126.83),
    "11B20304": (37.78, 127.04), "11B20305": (37.76, 126.78), "11B20401": (37.90, 127.06),
    "11B20402": (38.09, 127.07), "11B20403": (37.90, 127.20), "11B20404": (37.83, 127.51),
    "11B20501": (37.60, 127.13), "11B20502": (37.64, 127.22), "11B20503": (37.49, 127.49),
    "11B20504": (37.54, 127.21), "11B20601": (37.26, 127.02), "11B20602": (37.39, 126.96),
    "11B20603": (37.15, 127.07), "11B20604": (37.19, 126.83), "11B20605": (37.45, 127.14),
    "11B20606": (36.99, 127.11), "11B20609": (37.34, 126.97), "11B20610": (37.36, 126.93),
    "11B20611": (37.01, 127.27), "11B20612": (37.24, 127.18), "11B20701": (37.27, 127.44),
    "11B20702": (37.43, 127.26), "11B20703": (37.30, 127.64), "11C10101": (36.98, 127.93),
    "11C10102": (36.86, 127.44), "11C10103": (36.94, 127.69), "11C10201": (37.13, 128.19),
    "11C10202": (36.98, 128.37), "11C10301": (36.64, 127.49), "11C10302": (36.49, 127.73),
    "11C10303": (36.82, 127.79), "11C10304": (36.79, 127.58), "11C10401": (36.22, 128.02),
    "11C10402": (36.17, 127.78), "11C10403": (36.30, 127.57), "11C20101": (36.78, 126.45),
    "11C20102": (36.74, 126.30), "11C20103": (36.89, 126.63), "11C20104": (36.60, 126.66),
    "11C20201": (36.33, 126.61), "11C20202": (36.08, 126.69), "11C20301": (36.81, 127.15),
    "11C20302": (36.79, 127.00), "11C20303": (36.68, 126.85), "11C20401": (36.35, 127.38),
    "11C20402": (36.44, 127.11), "11C20403": (36.27, 127.25), "11C20404": (36.48, 127.29),
    "11C20501": (36.27, 126.91), "11C20502": (36.45, 126.80), "11C20601": (36.11, 127.49),
    "11C20602": (36.19, 127.10), "11D10101": (38.15, 127.31), "11D10102": (38.11, 127.71),
    "11D10201": (38.07, 128.17), "11D10202": (38.10, 127.99), "11D10301": (37.88, 127.73),
    "11D10302": (37.70, 127.89), "11D10401": (37.34, 127.92), "11D10402": (37.49, 128.00),
    "11D10501": (37.18, 128.46), "11D10502": (37.38, 128.66), "11D10503": (37.37, 128.39),
    "11D20201": (37.68, 128.72), "11D20301": (37.16, 128.99), "11D20401": (38.21, 128.59),
    "11D20402": (38.38, 128.47), "11D20403": (38.08, 128.63), "11D20501": (37.75, 128.88),
    "11D20601": (37.52, 129.11), "11D20602": (37.45, 129.17), "11E00101": (37.49, 130.86),
    "11E00102": (37.24, 131.86), "11F10201": (35.82, 127.15), "11F10202": (35.95, 126.96),
    "11F10203": (35.57, 126.85), "11F10204": (35.90, 127.13), "11F10301": (35.65, 127.52),
    "11F10302": (36.01, 127.66), "11F10303": (35.79, 127.43), "11F10401": (35.41, 127.39),
    "11F10402": (35.61, 127.29), "11F10403": (35.37, 127.14), "11F20301": (34.31, 126.76),
    "11F20302": (34.57, 126.60), "11F20303": (34.64, 126.77), "11F20304": (34.69, 126.91),
    "11F20401": (34.76, 127.66), "11F20402": (34.94, 127.70), "11F20403": (34.60, 127.28),
    "11F20404": (34.77, 127.07), "11F20405": (34.95, 127.48),
    "11F20501": (35.15, 126.85), "11F20502": (35.30, 126.78), "11F20503": (35.02, 126.71),
    "11F20504": (35.32, 126.99), "11F20505": (35.06, 126.99), "11F20601": (35.20, 127.46),
    "11F20602": (35.28, 127.29), "11F20603": (34.94, 127.69),
    "11F20701": (34.69, 125.44), "11G00101": (33.38, 126.88), "11G00201": (33.51, 126.52),
    "11G00302": (33.36, 126.53), "11G00401": (33.25, 126.56), "11G00501": (33.29, 126.16),
    "11G00601": (32.12, 125.18), "11G00800": (33.96, 126.29), "11G00901": (33.43, 126.53),
    "11G01001": (33.36, 126.67), "11H10101": (37.04, 129.40), "11H10102": (36.53, 129.37),
    "11H10201": (36.02, 129.34), "11H10202": (35.84, 129.22), "11H10301": (36.59, 128.19),
    "11H10302": (36.41, 128.16), "11H10303": (36.65, 128.45), "11H10401": (36.87, 128.60),
    "11H10402": (36.89, 128.73), "11H10403": (36.67, 129.11), "11H10501": (36.57, 128.73),
    "11H10502": (36.35, 128.70), "11H10503": (36.44, 129.06), "11H10601": (36.12, 128.11),
    "11H10602": (36.12, 128.35), "11H10604": (35.73, 128.26), "11H10605": (35.92, 128.28),
    "11H10701": (35.87, 128.60), "11H10702": (35.97, 128.94), "11H10703": (35.82, 128.74),
    "11H10704": (35.65, 128.73), "11H10705": (35.99, 128.40), "11H10707": (36.24, 128.57),
    "11H20101": (35.54, 129.31), "11H20102": (35.34, 129.03), "11H20201": (35.10, 129.03),
    "11H20301": (35.23, 128.68), "11H20304": (35.23, 128.89), "11H20401": (34.85, 128.43),
    "11H20402": (35.00, 128.06), "11H20403": (34.88, 128.62), "11H20404": (34.97, 128.36),
    "11H20405": (34.84, 127.89), "11H20501": (35.52, 127.73), "11H20502": (35.69, 127.91),
    "11H20503": (35.57, 128.17), "11H20601": (35.50, 128.74), "11H20602": (35.32, 128.26),
    "11H20603": (35.27, 128.41), "11H20604": (35.55, 128.49), "11H20701": (35.18, 128.11),
    "11H20703": (35.41, 127.87), "11H20704": (35.07, 127.75), "21F10501": (35.97, 126.74),
    "21F10502": (35.80, 126.89), "21F10601": (35.44, 126.70), "21F10602": (35.73, 126.73),
    "21F20101": (35.07, 126.52), "21F20102": (35.28, 126.51), "21F20201": (34.49, 126.26),
    "21F20801": (34.81, 126.39), "21F20802": (34.80, 126.70), "21F20803": (34.83, 126.10),
    "21F20804": (34.99, 126.46),
}

_EXCLUDE_FROM_NEAREST: frozenset[str] = frozenset({"11G00601", "11E00102"})

_LAND_CODE_MAP: list[tuple[str, str]] = [
    ("11A", "11A00101"), ("11B", "11B00000"), ("11C1", "11C10000"), ("11C2", "11C20000"),
    ("11D1", "11D10000"), ("11D2", "11D20000"), ("11E", "11E00101"), ("11F1", "11F10000"),
    ("11F2", "11F20000"), ("11G", "11G00000"), ("11H1", "11H10000"), ("11H2", "11H20000"),
    ("21F1", "21F10000"), ("21F2", "21F20000"),
]

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return r * 2 * math.asin(math.sqrt(a))

def _land_code(temp_id: str) -> str:
    for prefix, land in sorted(_LAND_CODE_MAP, key=lambda x: len(x[0]), reverse=True):
        if temp_id.startswith(prefix): return land
    return "11B00000"

def _get_kma_reg_ids(lat: float, lon: float) -> tuple:
    best_id, best_dist = None, float("inf")
    for tid, (tlat, tlon) in _TEMP_ID_COORDS.items():
        if tid in _EXCLUDE_FROM_NEAREST: continue
        d = _haversine(lat, lon, tlat, tlon)
        if d < best_dist: best_dist, best_id = d, tid
    return (best_id, _land_code(best_id)) if best_id else (None, None)

def _is_valid_korean_coord(lat: float, lon: float) -> bool:
    if math.isnan(lat) or math.isnan(lon): return False
    return 32.0 <= lat <= 42.5 and 124.0 <= lon <= 132.5

class KMAWeatherUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(hours=1))
        self.entry = entry
        self.api = KMAWeatherAPI(
            session=async_get_clientsession(hass), api_key=entry.data.get(CONF_API_KEY),
            reg_id_temp=None, reg_id_land=None, hass=hass,
        )
        self._last_lat = self._last_lon = None
        self._daily_date = self._daily_max_temp = self._daily_min_temp = None
        self._wf_am_today = self._wf_pm_today = None
        self._cached_data = None
        self._update_lock = asyncio.Lock()

        target_entity = entry.data.get(CONF_LOCATION_ENTITY, "default_location")
        safe_key = target_entity.replace(".", "_") if target_entity else entry.entry_id
        self._store = Store(hass, version=1, key=f"{DOMAIN}_{safe_key}_daily_temp")
        self._store_loaded = False

    async def _restore_daily_temps(self):
        if self._store_loaded: return
        stored = await self._store.async_load()
        if stored:
            tz = getattr(self.api, "tz", timezone(timedelta(hours=9)))
            now = datetime.now(tz)
            if stored.get("date") == now.strftime("%Y%m%d"):
                try:
                    self._daily_date = now.date()
                    self._daily_max_temp = float(stored.get("max"))
                    self._daily_min_temp = float(stored.get("min"))
                    self._wf_am_today = stored.get("wf_am")
                    self._wf_pm_today = stored.get("wf_pm")
                    _LOGGER.info("✅ 저장소 데이터 복구 성공")
                except: pass
        self._store_loaded = True

    async def _save_daily_temps(self):
        if self._daily_date:
            await self._store.async_save({
                "date": self._daily_date.strftime("%Y%m%d"), "min": self._daily_min_temp,
                "max": self._daily_max_temp, "wf_am": self._wf_am_today, "wf_pm": self._wf_pm_today,
            })

    def _update_daily_temperatures(self, forecast_map: dict) -> bool:
        now = datetime.now(self.api.tz)
        today_str, today_date = now.strftime("%Y%m%d"), now.date()
        changed = False

        if self._daily_date != today_date:
            self._daily_date, self._daily_max_temp, self._daily_min_temp = today_date, None, None
            self._wf_am_today = self._wf_pm_today = None
            changed = True

        temps = [float(s["TMP"]) for s in forecast_map.get(today_str, {}).values() if s.get("TMP")]
        if temps:
            n_min, n_max = min(temps), max(temps)
            if self._daily_min_temp is None or n_min < self._daily_min_temp:
                self._daily_min_temp, changed = n_min, True
            if self._daily_max_temp is None or n_max > self._daily_max_temp:
                self._daily_max_temp, changed = n_max, True
        return changed

    async def _async_update_data(self) -> dict:
        async with self._update_lock:
            try:
                await self._restore_daily_temps()
                curr_lat, curr_lon = self._resolve_location()
                if curr_lat is None: return self._cached_data or {"weather": {}, "air": {}}

                reg_temp, reg_land = _get_kma_reg_ids(curr_lat, curr_lon)
                self.api.reg_id_temp, self.api.reg_id_land = reg_temp, reg_land

                nx, ny = convert_grid(curr_lat, curr_lon)
                new_data = await self.api.fetch_data(curr_lat, curr_lon, nx, ny)
                if not new_data: return self._cached_data

                weather = new_data.setdefault("weather", {})

                if "raw_forecast" in new_data:
                    temp_changed = self._update_daily_temperatures(new_data["raw_forecast"])
                    api_am, api_pm = weather.get("wf_am_today"), weather.get("wf_pm_today")
                    summary_changed = False
                    if api_am and self._wf_am_today != api_am: self._wf_am_today, summary_changed = api_am, True
                    if api_pm and self._wf_pm_today != api_pm: self._wf_pm_today, summary_changed = api_pm, True
                    if temp_changed or summary_changed: await self._save_daily_temps()

                weather.update({
                    "TMX_today": self._daily_max_temp,
                    "TMN_today": self._daily_min_temp,
                    "wf_am_today": self._wf_am_today,
                    "wf_pm_today": self._wf_pm_today,
                    "today_max": self._daily_max_temp,
                    "today_min": self._daily_min_temp,
                    "last_updated": datetime.now(timezone.utc),
                    "debug_nx": nx, "debug_ny": ny,
                })

                # ── [수정] 시간대별 current_condition_kor 갱신 후
                #          current_condition 을 항상 함께 동기화 ────────────
                now_h = datetime.now(self.api.tz).hour
                if now_h < 12:
                    kor = self._wf_am_today or weather.get("current_condition_kor")
                else:
                    kor = self._wf_pm_today or self._wf_am_today or weather.get("current_condition_kor")

                weather["current_condition_kor"] = kor
                weather["current_condition"] = self.api.kor_to_condition(kor)

                self._cached_data = new_data
                return new_data

            except Exception as exc:
                _LOGGER.error("업데이트 중 오류: %s", exc)
                return self._cached_data

    def _resolve_location(self) -> tuple:
        entity_id = self.entry.data.get(CONF_LOCATION_ENTITY, "")
        state = self.hass.states.get(entity_id) if entity_id else None
        if state:
            lat_attr, lon_attr = state.attributes.get("latitude"), state.attributes.get("longitude")
            if lat_attr is not None and lon_attr is not None:
                try:
                    lat, lon = float(lat_attr), float(lon_attr)
                    if _is_valid_korean_coord(lat, lon): return lat, lon
                except: pass
        if self._last_lat is not None: return self._last_lat, self._last_lon
        try:
            lat, lon = float(self.hass.config.latitude), float(self.hass.config.longitude)
            if _is_valid_korean_coord(lat, lon): return lat, lon
        except: pass
        return None, None
