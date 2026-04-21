import json
import logging
import asyncio
import math
import pathlib
from datetime import datetime, timedelta, timezone, date
from astral import LocationInfo
from astral.sun import (sun as astral_sun, dawn as astral_dawn, dusk as astral_dusk,
                        time_at_elevation, SunDirection,
                        elevation as sun_elevation)
from astral.moon import phase as moon_phase, moonrise, moonset
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from .api_kma import KMAWeatherAPI
from .const import DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY, convert_grid

_LOGGER = logging.getLogger(__name__)

# ── 중기예보 구역코드 테이블 (area.json) ────────────────────────────────────
_AREA = json.loads((pathlib.Path(__file__).parent / "area.json").read_text(encoding="utf-8"))
_TEMP_ID_COORDS: dict[str, tuple[float, float]] = {k: tuple(v) for k, v in _AREA["temp"].items()}
_EXCLUDE_FROM_NEAREST: frozenset[str] = frozenset(_AREA["exclude"])
_LAND_CODE_MAP: list[tuple[str, str]] = [tuple(x) for x in _AREA["land"]]

# ── 특보구역코드 테이블 (warn_area.json) ─────────────────────────────────────
_WARN_AREA: list[list] = json.loads(
    (pathlib.Path(__file__).parent / "warn_area.json").read_text(encoding="utf-8")
)


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return r * 2 * math.asin(math.sqrt(a))


def _land_code(temp_id: str) -> str:
    for prefix, land in sorted(_LAND_CODE_MAP, key=lambda x: len(x[0]), reverse=True):
        if temp_id.startswith(prefix):
            return land
    return "11B00000"


def _calc_reg_ids(lat: float, lon: float) -> tuple[str | None, str | None]:
    """좌표 → (reg_id_temp, reg_id_land)"""
    best_id, best_dist = None, float("inf")
    for tid, (tlat, tlon) in _TEMP_ID_COORDS.items():
        if tid in _EXCLUDE_FROM_NEAREST:
            continue
        d = _haversine(lat, lon, tlat, tlon)
        if d < best_dist:
            best_dist, best_id = d, tid
    return (best_id, _land_code(best_id)) if best_id else (None, None)


def _calc_warn_area_code(lat: float, lon: float) -> str | None:
    """좌표 → 특보구역코드"""
    best_code, best_dist = None, float("inf")
    for row in _WARN_AREA:
        d = _haversine(lat, lon, row[0], row[1])
        if d < best_dist:
            best_dist, best_code = d, row[2]
    return best_code


def _is_valid_korean_coord(lat: float, lon: float) -> bool:
    if math.isnan(lat) or math.isnan(lon):
        return False
    return 32.0 <= lat <= 42.5 and 124.0 <= lon <= 132.5


class KMAWeatherUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(hours=1))
        self.entry = entry
        self.api = KMAWeatherAPI(
            session=async_get_clientsession(hass),
            api_key=entry.data.get(CONF_API_KEY),
            hass=hass,
        )
        self._last_lat = self._last_lon = None
        self._daily_date = self._daily_max_temp = self._daily_min_temp = None
        self._wf_am_today = self._wf_pm_today = None
        self._cached_data = None
        self._update_lock = asyncio.Lock()

        # ── 위치 기반 구역코드 통합 캐시 (2km 이내 이동 시 재사용) ──────────
        self._cached_area_lat: float | None = None
        self._cached_area_lon: float | None = None
        self._cached_nx: int | None = None
        self._cached_ny: int | None = None
        self._cached_reg_id_temp: str | None = None
        self._cached_reg_id_land: str | None = None
        self._cached_warn_area_code: str | None = None

        # ── 천문 시각 캐시 (날짜/좌표 변경 시 재계산) ───────────────────────
        self._sun_cache_date: date | None = None
        self._sun_cache_lat: float | None = None
        self._sun_cache_lon: float | None = None
        self._sun_times: dict = {}  # dawn, sunrise, sunset, dusk (HH:MM 문자열)

        target_entity = entry.data.get(CONF_LOCATION_ENTITY, "default_location")
        safe_key = target_entity.replace(".", "_") if target_entity else entry.entry_id
        self._store = Store(hass, version=1, key=f"{DOMAIN}_{safe_key}_daily_temp")
        self._store_loaded = False

    # ── 위치 → 모든 구역코드 결정 (2km 캐시 통합) ───────────────────────────
    def _resolve_area_codes(self, lat: float, lon: float) -> tuple:
        """
        현재 좌표로 단기/중기/특보 구역코드를 한 번에 결정한다.
        이전 좌표에서 2km 이내 이동이면 캐시를 재사용한다.

        Returns:
            (nx, ny, reg_id_temp, reg_id_land, warn_area_code)
        """
        if (self._cached_area_lat is not None
                and _haversine(self._cached_area_lat, self._cached_area_lon, lat, lon) <= 2.0):
            return (
                self._cached_nx, self._cached_ny,
                self._cached_reg_id_temp, self._cached_reg_id_land,
                self._cached_warn_area_code,
            )

        nx, ny = convert_grid(lat, lon)
        reg_id_temp, reg_id_land = _calc_reg_ids(lat, lon)
        warn_area_code = _calc_warn_area_code(lat, lon)

        self._cached_area_lat = lat
        self._cached_area_lon = lon
        self._cached_nx = nx
        self._cached_ny = ny
        self._cached_reg_id_temp = reg_id_temp
        self._cached_reg_id_land = reg_id_land
        self._cached_warn_area_code = warn_area_code

        _LOGGER.debug(
            "구역코드 갱신: nx=%s ny=%s reg_temp=%s reg_land=%s warn=%s",
            nx, ny, reg_id_temp, reg_id_land, warn_area_code,
        )
        return nx, ny, reg_id_temp, reg_id_land, warn_area_code

    # ── 저장소 복구/저장 ────────────────────────────────────────────────────
    async def _restore_daily_temps(self):
        if self._store_loaded:
            return
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
                except Exception as e:
                    _LOGGER.debug("저장소 데이터 복구 실패 (무시): %s", e)
        self._store_loaded = True

    async def _save_daily_temps(self):
        if self._daily_date:
            await self._store.async_save({
                "date": self._daily_date.strftime("%Y%m%d"),
                "min": self._daily_min_temp,
                "max": self._daily_max_temp,
                "wf_am": self._wf_am_today,
                "wf_pm": self._wf_pm_today,
            })

    # ── 일별 기온 누적 ──────────────────────────────────────────────────────
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

    # ── forecast 동기화 ─────────────────────────────────────────────────────
    def _sync_today_forecast(self, weather: dict) -> None:
        today_t_max = self._daily_max_temp
        today_t_min = self._daily_min_temp
        wf_am_today = self._wf_am_today or weather.get("wf_am_today")
        wf_pm_today = self._wf_pm_today or weather.get("wf_pm_today")
        current_condition = weather.get("current_condition")

        tmrw_t_max = weather.get("TMX_tomorrow")
        tmrw_t_min = weather.get("TMN_tomorrow")
        wf_am_tomorrow = weather.get("wf_am_tomorrow")
        wf_pm_tomorrow = weather.get("wf_pm_tomorrow")

        for entry in weather.get("forecast_daily", []):
            idx = entry.get("_day_index")
            if idx == 0:
                if today_t_max is not None: entry["native_temperature"] = today_t_max
                if today_t_min is not None: entry["native_templow"] = today_t_min
                if current_condition is not None: entry["condition"] = current_condition
            elif idx == 1:
                if tmrw_t_max is not None: entry["native_temperature"] = tmrw_t_max
                if tmrw_t_min is not None: entry["native_templow"] = tmrw_t_min
                if wf_pm_tomorrow: entry["condition"] = self.api.kor_to_condition(wf_pm_tomorrow)

        for entry in weather.get("forecast_twice_daily", []):
            idx = entry.get("_day_index")
            is_am = entry.get("is_daytime", True)
            if idx == 0:
                if today_t_max is not None: entry["native_temperature"] = today_t_max
                if today_t_min is not None: entry["native_templow"] = today_t_min
                if is_am and wf_am_today: entry["condition"] = self.api.kor_to_condition(wf_am_today)
                elif not is_am and wf_pm_today: entry["condition"] = self.api.kor_to_condition(wf_pm_today)
            elif idx == 1:
                if tmrw_t_max is not None: entry["native_temperature"] = tmrw_t_max
                if tmrw_t_min is not None: entry["native_templow"] = tmrw_t_min
                if is_am and wf_am_tomorrow: entry["condition"] = self.api.kor_to_condition(wf_am_tomorrow)
                elif not is_am and wf_pm_tomorrow: entry["condition"] = self.api.kor_to_condition(wf_pm_tomorrow)

    # ── 메인 업데이트 ───────────────────────────────────────────────────────
    async def _async_update_data(self) -> dict:
        async with self._update_lock:
            try:
                await self._restore_daily_temps()
                curr_lat, curr_lon = self._resolve_location()
                if curr_lat is None:
                    return self._cached_data or {"weather": {}, "air": {}}

                # coordinator가 모든 구역코드를 결정해서 api에 전달
                nx, ny, reg_id_temp, reg_id_land, warn_area_code = self._resolve_area_codes(
                    curr_lat, curr_lon
                )

                new_data = await self.api.fetch_data(
                    lat=curr_lat, lon=curr_lon,
                    nx=nx, ny=ny,
                    reg_id_temp=reg_id_temp, reg_id_land=reg_id_land,
                    warn_area_code=warn_area_code,
                )
                if not new_data:
                    return self._cached_data

                weather = new_data.setdefault("weather", {})

                if "raw_forecast" in new_data:
                    temp_changed = self._update_daily_temperatures(new_data["raw_forecast"])
                    api_am = weather.get("wf_am_today")
                    api_pm = weather.get("wf_pm_today")
                    summary_changed = False
                    if api_am and self._wf_am_today != api_am:
                        self._wf_am_today, summary_changed = api_am, True
                    if api_pm and self._wf_pm_today != api_pm:
                        self._wf_pm_today, summary_changed = api_pm, True
                    if temp_changed or summary_changed:
                        await self._save_daily_temps()

                weather.update({
                    "TMX_today": self._daily_max_temp,
                    "TMN_today": self._daily_min_temp,
                    "wf_am_today": self._wf_am_today,
                    "wf_pm_today": self._wf_pm_today,
                    "last_updated": datetime.now(timezone.utc),
                    "debug_nx": nx,
                    "debug_ny": ny,
                    "debug_lat": curr_lat,
                    "debug_lon": curr_lon,
                })

                now_h = datetime.now(self.api.tz).hour
                if now_h < 12:
                    kor = self._wf_am_today or weather.get("current_condition_kor")
                else:
                    kor = self._wf_pm_today or self._wf_am_today or weather.get("current_condition_kor")

                weather["current_condition_kor"] = kor
                weather["current_condition"] = self.api.kor_to_condition(kor)

                self._sync_today_forecast(weather)

                # ── 현재 상태값이 '-'/None이면 이전 캐시 값으로 보완 ──────────
                # 기상청 발표 직후 일부 시각 슬롯이 '-'로 내려오는 경우 방어
                _REALTIME_KEYS = (
                    "TMP", "REH", "WSD", "VEC", "VEC_KOR", "POP", "apparent_temp"
                )
                if self._cached_data:
                    prev_weather = self._cached_data.get("weather", {})
                    for _key in _REALTIME_KEYS:
                        if weather.get(_key) in (None, "-", ""):
                            prev_val = prev_weather.get(_key)
                            if prev_val not in (None, "-", ""):
                                weather[_key] = prev_val

                # ── 천문 시각 주입 ──────────────────────────────────────────
                sun_times = self._calc_sun_times(curr_lat, curr_lon,
                                                 datetime.now(self.api.tz))
                weather.update(sun_times)

                # ── 천문 관측 조건 평가 ────────────────────────────────────
                weather["observation_condition"] = self._eval_observation(
                    weather, datetime.now(self.api.tz), curr_lat, curr_lon
                )

                self._cached_data = new_data
                return new_data

            except Exception as exc:
                _LOGGER.error("업데이트 중 오류: %s", exc)
                return self._cached_data

    # ── 천문 시각 계산 ──────────────────────────────────────────────────────
    def _calc_sun_times(self, lat: float, lon: float, now: datetime) -> dict:
        """
        현재 시각 이후의 다음 천문 이벤트 시각을 계산한다.
        이미 지난 이벤트는 내일 시각을 반환한다.
        좌표/날짜 변경 시 재계산, 그 외엔 캐시를 반환한다.

        반환값 키:
          dawn, sunrise, sunset, dusk  — 태양 (HH:MM)
          astro_dawn, astro_dusk       — 천문 박명 18° (HH:MM)
          moonrise, moonset            — 월출/월몰 (HH:MM)
          moon_phase                   — 달 위상 이름
          moon_illumination            — 달 조명율 (정수 %)
        """
        today = now.date()

        if (self._sun_cache_lat == lat
                and self._sun_cache_lon == lon
                and self._sun_times
                and self._sun_cache_date == today):
            return self._sun_times

        try:
            loc = LocationInfo(latitude=lat, longitude=lon,
                               timezone=str(self.api.tz))
            tz  = self.api.tz
            result = {}

            def _fmt(t: datetime) -> str:
                prefix = "오늘" if t.date() == today else "내일"
                return f"{prefix} {t.strftime('%H:%M')}"

            # ── 태양 이벤트 ────────────────────────────────────────────────
            for event in ("dawn", "sunrise", "sunset", "dusk"):
                found = None
                for offset in (0, 1):
                    d = today + timedelta(days=offset)
                    try:
                        if event == "dawn":
                            t = astral_dawn(loc.observer, date=d, tzinfo=tz)
                        elif event == "dusk":
                            t = astral_dusk(loc.observer, date=d, tzinfo=tz)
                        else:
                            t = astral_sun(loc.observer, date=d, tzinfo=tz)[event]
                        if t > now:
                            found = t
                            break
                    except Exception:
                        continue
                result[event] = _fmt(found) if found else None

            # ── 천문 박명 (18°, 별 관측 가능 시각) ────────────────────────
            for label, elev, direction in (
                ("astro_dawn", -18, SunDirection.RISING),
                ("astro_dusk", -18, SunDirection.SETTING),
            ):
                found = None
                for offset in (0, 1):
                    d = today + timedelta(days=offset)
                    try:
                        t = time_at_elevation(loc.observer, elevation=elev,
                                              date=d, direction=direction, tzinfo=tz)
                        if t > now:
                            found = t
                            break
                    except Exception:
                        continue
                result[label] = _fmt(found) if found else None

            # ── 달 위상/조명율 (오늘 날짜 기준) ───────────────────────────
            p = moon_phase(today)
            result["moon_phase"]        = self._moon_phase_name(p)
            result["moon_illumination"] = self._moon_illumination(p)

            # ── 월출/월몰 ──────────────────────────────────────────────────
            for label, func in (("moonrise", moonrise), ("moonset", moonset)):
                found = None
                for offset in (0, 1):
                    d = today + timedelta(days=offset)
                    try:
                        t = func(loc.observer, date=d, tzinfo=tz)
                        if t and t > now:
                            found = t
                            break
                    except Exception:
                        continue
                result[label] = _fmt(found) if found else None

            self._sun_cache_date = today
            self._sun_cache_lat  = lat
            self._sun_cache_lon  = lon
            self._sun_times      = result
            _LOGGER.debug("천문 시각 갱신: %s (lat=%.4f, lon=%.4f)", today, lat, lon)

        except Exception as e:
            _LOGGER.warning("천문 시각 계산 실패: %s", e)
            result = self._sun_times

        return result

    @staticmethod
    def _moon_phase_name(p: float) -> str:
        if   p <  1.85: return "삭"
        elif p <  7.38: return "초승달"
        elif p < 11.07: return "상현달"
        elif p < 14.77: return "준상현달"
        elif p < 18.46: return "보름달"
        elif p < 22.15: return "준하현달"
        elif p < 25.84: return "하현달"
        elif p < 29.53: return "그믐달"
        return "삭"

    @staticmethod
    def _moon_illumination(p: float) -> int:
        angle = p / 29.53 * 360
        return round((1 - abs(180 - angle % 360) / 180) * 100)

    def _eval_observation(self, weather: dict, now: datetime,
                          lat: float, lon: float) -> str:
        """
        현재 날씨 + 태양 고도 + 달 조명율을 종합해 천문 관측 조건을 평가한다.
        반환: "최우수" / "우수" / "보통" / "불량 (달빛)" /
              "관측불가 (강수)" / "관측불가 (흐림)" / "관측불가 (낮/박명)"
        """
        # 1. 날씨
        condition = weather.get("current_condition", "")
        if condition in {"rainy", "pouring", "snowy", "snowy-rainy",
                         "lightning", "lightning-rainy"}:
            return "관측불가"
        if condition == "cloudy":
            return "관측불가"

        # 2. 태양 고도 직접 계산 (-18° 이하이면 천문 박명 종료, 관측 가능)
        try:
            from astral import LocationInfo as _Loc
            _loc = _Loc(latitude=lat, longitude=lon, timezone=str(self.api.tz))
            elev = sun_elevation(_loc.observer, dateandtime=now)
            if elev > -18:
                return "관측불가"
        except Exception:
            return "관측불가"

        # 3. 달 조명율
        illum = weather.get("moon_illumination", 100)
        try:
            illum = int(illum)
        except (TypeError, ValueError):
            illum = 100

        if illum <= 25:   return "최우수"
        elif illum <= 50: return "우수"
        elif illum <= 75: return "보통"
        else:             return "불량"

    # ── 위치 결정 ───────────────────────────────────────────────────────────
    def _resolve_location(self) -> tuple:
        entity_id = self.entry.data.get(CONF_LOCATION_ENTITY, "")
        state = self.hass.states.get(entity_id) if entity_id else None
        if state:
            lat_attr = state.attributes.get("latitude")
            lon_attr = state.attributes.get("longitude")
            if lat_attr is not None and lon_attr is not None:
                try:
                    lat, lon = float(lat_attr), float(lon_attr)
                    if _is_valid_korean_coord(lat, lon):
                        self._last_lat, self._last_lon = lat, lon  # ← 성공 시 캐시 갱신
                        return lat, lon
                except Exception:
                    pass
        if self._last_lat is not None:
            return self._last_lat, self._last_lon
        try:
            lat, lon = float(self.hass.config.latitude), float(self.hass.config.longitude)
            if _is_valid_korean_coord(lat, lon):
                return lat, lon
        except Exception:
            pass
        return None, None
