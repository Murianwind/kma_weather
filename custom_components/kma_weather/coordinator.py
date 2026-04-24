import json
import logging
import asyncio
import pathlib
from datetime import datetime, timedelta, timezone, date
try:
    from skyfield.api import Loader as _SkyLoader, wgs84 as _wgs84
    from skyfield import almanac as _almanac
    _SKYFIELD_OK = True
except ImportError:
    _SKYFIELD_OK = False
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from .api_kma import KMAWeatherAPI
from .const import DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY, convert_grid, haversine, is_korean_coord_loose

_LOGGER = logging.getLogger(__name__)

# ── 중기예보 구역코드 테이블 (area.json) ────────────────────────────────────
_AREA = json.loads((pathlib.Path(__file__).parent / "area.json").read_text(encoding="utf-8"))
_TEMP_ID_COORDS: dict[str, tuple[float, float]] = {k: tuple(v) for k, v in _AREA["temp"].items()}
_EXCLUDE_FROM_NEAREST: frozenset[str] = frozenset(_AREA["exclude"])
_LAND_CODE_MAP: list[tuple[str, str]] = [tuple(x) for x in _AREA["land"]]
# prefix 길이 내림차순으로 미리 정렬 → _land_code 호출 시 매번 정렬 불필요
_LAND_CODE_MAP_SORTED: list[tuple[str, str]] = sorted(
    _LAND_CODE_MAP, key=lambda x: len(x[0]), reverse=True
)

# ── 특보구역코드 테이블 (warn_area.json) ─────────────────────────────────────
_WARN_AREA: list[list] = json.loads(
    (pathlib.Path(__file__).parent / "warn_area.json").read_text(encoding="utf-8")
)





def _land_code(temp_id: str) -> str:
    for prefix, land in _LAND_CODE_MAP_SORTED:
        if temp_id.startswith(prefix):
            return land
    return "11B00000"


def _calc_reg_ids(lat: float, lon: float) -> tuple[str | None, str | None]:
    """좌표 → (reg_id_temp, reg_id_land)"""
    best_id, best_dist = None, float("inf")
    for tid, (tlat, tlon) in _TEMP_ID_COORDS.items():
        if tid in _EXCLUDE_FROM_NEAREST:
            continue
        d = haversine(lat, lon, tlat, tlon)
        if d < best_dist:
            best_dist, best_id = d, tid
    return (best_id, _land_code(best_id)) if best_id else (None, None)


def _calc_warn_area_code(lat: float, lon: float) -> str | None:
    """좌표 → 특보구역코드"""
    best_code, best_dist = None, float("inf")
    for row in _WARN_AREA:
        d = haversine(lat, lon, row[0], row[1])
        if d < best_dist:
            best_dist, best_code = d, row[2]
    return best_code





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
        self._sun_times: dict = {}

                # skyfield: 고정밀 천문 계산 (월출/월몰 포함)
        self._sf_eph = None
        self._sf_ts  = None
        if _SKYFIELD_OK:
            import os as _os, tempfile as _tf
            _sf_dir   = hass.config.config_dir + "/.skyfield"
            
            # 환경변수 또는 테스트용 캐시 경로 fallback
            _fallback = _os.environ.get(
                "SKYFIELD_BSP_DIR",
                _os.path.join(_tf.gettempdir(), "skyfield_test_cache"),
            )
            if _os.path.exists(_fallback + "/de440s.bsp"):
                _sf_dir = _fallback

            # 메인 이벤트 루프 블로킹을 방지하기 위해 파일 로드를 항상 백그라운드 태스크로 위임
            hass.async_create_task(self._async_init_skyfield(_sf_dir))


        target_entity = entry.data.get(CONF_LOCATION_ENTITY, "default_location")
        safe_key = target_entity.replace(".", "_") if target_entity else entry.entry_id
        self._store = Store(hass, version=1, key=f"{DOMAIN}_{safe_key}_daily_temp")
        self._store_loaded = False

        # ── API 호출 카운터 ──────────────────────────────────────────────────
        self._api_call_counts: dict[str, int] = {
            "단기예보": 0, "중기예보": 0,
            "에어코리아_측정소": 0, "에어코리아_대기": 0,
            "기상특보": 0, "꽃가루": 0,
        }
        self._api_call_date: str | None = None   # "YYYYMMDD" 형식
        # API 호출 카운터는 모든 기기 합산 → 공통 Store 키 사용
        self._api_call_store = Store(hass, version=1, key=f"{DOMAIN}_global_api_calls")
        self._api_call_store_loaded = False

        # api 객체에 카운터 콜백 주입 (api는 이미 위에서 생성됨)
        self._inject_counter()

    async def _async_init_skyfield(self, sf_dir: str) -> None:
        """de440s.bsp 파일을 비동기로 다운로드 후 skyfield 초기화"""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._sync_init_skyfield, sf_dir)
        except Exception as e:
            _LOGGER.warning("skyfield 비동기 초기화 실패: %s", e)

    def _sync_init_skyfield(self, sf_dir: str) -> None:
        """동기 컨텍스트에서 skyfield 로드 (executor에서 실행)"""
        if not _SKYFIELD_OK:
            return
        try:
            import os as _os
            _os.makedirs(sf_dir, exist_ok=True)
            _loader = _SkyLoader(sf_dir)
            ts  = _loader.timescale()
            eph = _loader("de440s.bsp")
            self._sf_ts  = ts
            self._sf_eph = eph
            _LOGGER.debug("skyfield de440s.bsp 백그라운드 로드 완료")
        except Exception as e:
            _LOGGER.warning("skyfield 백그라운드 초기화 실패: %s", e)

    # ── 위치 → 모든 구역코드 결정 (2km 캐시 통합) ───────────────────────────
    def _resolve_area_codes(self, lat: float, lon: float) -> tuple:
        """
        현재 좌표로 단기/중기/특보 구역코드를 한 번에 결정한다.
        이전 좌표에서 2km 이내 이동이면 캐시를 재사용한다.

        Returns:
            (nx, ny, reg_id_temp, reg_id_land, warn_area_code)
        """
        if (self._cached_area_lat is not None
                and haversine(self._cached_area_lat, self._cached_area_lon, lat, lon) <= 2.0):
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

    # ── API 카운터 초기화/콜백 주입 ─────────────────────────────────────────
    # ── 공유 카운터 접근 헬퍼 ────────────────────────────────────────────────
    @property
    def _shared_counts(self) -> dict[str, int]:
        """hass.data에 저장된 전체 기기 공유 카운터를 반환한다."""
        return self.hass.data.setdefault(f"{DOMAIN}_api_call_counts", {
            "단기예보": 0, "중기예보": 0,
            "에어코리아_측정소": 0, "에어코리아_대기": 0,
            "기상특보": 0, "꽃가루": 0,
            "date": None,
        })

    def _inject_counter(self, reason: str = "자동 업데이트") -> None:
        """
        api 객체에 카운터 콜백을 주입한다. coordinator 생성 후 반드시 호출.

        Args:
            reason: 호출 이유 레이블 (자동 업데이트 / 업데이트 액션 / 다시 읽어오기 / 액션)
        """
        def _increment(key: str) -> None:
            now_date = datetime.now(self.api.tz).strftime("%Y%m%d")
            shared = self._shared_counts
            # 자정 넘어가면 전체 초기화
            if shared.get("date") and shared["date"] != now_date:
                for k in list(shared.keys()):
                    if k not in ("date", "last_reason"):
                        shared[k] = 0
                _LOGGER.debug("API 호출 카운터 자정 초기화: %s → %s", shared["date"], now_date)
                self.hass.async_create_task(self._save_api_calls())
            shared["date"] = now_date
            shared["last_reason"] = reason  # 마지막 호출 이유 갱신
            if key in shared:
                shared[key] += 1
            # 인스턴스 카운터도 동기화 (저장/복구용)
            self._api_call_date = now_date
            if key in self._api_call_counts:
                self._api_call_counts[key] = shared[key]
            # 센서 즉시 갱신 (HA 이벤트 루프에서 예약)
            self.hass.async_create_task(self._notify_api_counter_listeners())
        self.api._call_counter_ref = _increment

    # ── API 카운터 저장소 ────────────────────────────────────────────────────
    async def _restore_api_calls(self) -> None:
        """재시작 후 오늘 날짜의 카운터를 복구한다 (공유 카운터에 반영)."""
        if self._api_call_store_loaded:
            return
        try:
            stored = await self._api_call_store.async_load()
            if stored:
                tz = getattr(self.api, "tz", timezone(timedelta(hours=9)))
                today = datetime.now(tz).strftime("%Y%m%d")
                if stored.get("date") == today:
                    shared = self._shared_counts
                    # 공유 카운터가 아직 초기화 안 된 경우에만 복구
                    # (이미 다른 coordinator가 복구했으면 덮어쓰지 않음)
                    if shared.get("date") != today:
                        for key in self._api_call_counts:
                            val = int(stored.get(key, 0))
                            self._api_call_counts[key] = val
                            shared[key] = val
                        shared["date"] = today
                        self._api_call_date = today
                        _LOGGER.debug("API 호출 카운터 복구 성공: %s", self._api_call_counts)
        except Exception as e:
            _LOGGER.debug("API 호출 카운터 복구 실패 (무시): %s", e)
        self._api_call_store_loaded = True

    async def _save_api_calls(self) -> None:
        """현재 카운터를 저장한다."""
        try:
            await self._api_call_store.async_save({
                "date": self._api_call_date or datetime.now(self.api.tz).strftime("%Y%m%d"),
                **self._api_call_counts,
            })
        except Exception as e:
            _LOGGER.debug("API 호출 카운터 저장 실패: %s", e)

    def api_call_total(self) -> int:
        """오늘 총 API 호출 횟수를 반환한다 (전체 기기 합산)."""
        shared = self._shared_counts
        return sum(v for k, v in shared.items() if k not in ("date", "last_reason"))

    async def _notify_api_counter_listeners(self) -> None:
        """api_calls_today 센서를 즉시 갱신하도록 HA에 알린다."""
        try:
            self.async_update_listeners()
        except Exception:
            pass

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
                await self._restore_api_calls()
                # 업데이트 이유를 카운터에 반영 (기본: 자동 업데이트)
                self._inject_counter(getattr(self, "_update_reason", "자동 업데이트"))
                self._update_reason = "자동 업데이트"  # 다음 업데이트를 위해 초기화
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
                        # 키가 실제로 존재하고 값이 '-'/None인 경우에만 보완
                        # 키 자체가 없는 경우(누락 데이터)는 보완하지 않음
                        if _key in weather and weather[_key] in (None, "-", ""):
                            prev_val = prev_weather.get(_key)
                            if prev_val not in (None, "-", ""):
                                weather[_key] = prev_val

                # ── 천문 시각 주입 ──────────────────────────────────────────
                sun_times = self._calc_sun_times(curr_lat, curr_lon,
                                                 datetime.now(self.api.tz))
                weather.update(sun_times)

                # ── 천문 관측 조건 평가 ────────────────────────────────────
                obs_cond, obs_reason = self._eval_observation(
                    weather, datetime.now(self.api.tz), curr_lat, curr_lon
                )
                weather["observation_condition"] = obs_cond
                weather["observation_reason"]    = obs_reason

                self._cached_data = new_data
                # 카운터 저장 (매 업데이트마다 영속화)
                await self._save_api_calls()
                return new_data

            except Exception as exc:
                _LOGGER.error("업데이트 중 오류: %s", exc)
                return self._cached_data

    # ── 천문 시각 계산 ──────────────────────────────────────────────────────
    def _calc_sun_times(self, lat: float, lon: float, now: datetime) -> dict:
        """
        skyfield를 사용해 현재 시각 이후의 다음 천문 이벤트를 계산한다.
        skyfield 미준비 시 빈 dict를 반환한다.

        반환값 키:
          dawn, sunrise, sunset, dusk  — 태양 (오늘/내일 HH:MM)
          astro_dawn, astro_dusk       — 천문 박명 (오늘/내일 HH:MM)
          moonrise, moonset            — 월출/월몰 (오늘/내일 HH:MM)
          moon_phase                   — 달 위상 이름
          moon_illumination            — 달 조명율 (정수 %)
        """
        today = now.date()

        if self._sf_eph is None or self._sf_ts is None:
            return self._sun_times or {}

        try:
            tz     = self.api.tz
            sf_loc = _wgs84.latlon(lat, lon)
            result = {}

            def _fmt(t: datetime) -> str:
                prefix = "오늘" if t.date() == today else "내일"
                return f"{prefix} {t.strftime('%H:%M')}"

            def _ts_range(dd):
                t0 = self._sf_ts.from_datetime(
                    datetime(dd.year, dd.month, dd.day, 0, 0, tzinfo=tz))
                t1 = self._sf_ts.from_datetime(
                    datetime(dd.year, dd.month, dd.day, 23, 59, tzinfo=tz))
                return t0, t1

            # ── 일출/일몰 ──────────────────────────────────────────────────
            f_ss = _almanac.sunrise_sunset(self._sf_eph, sf_loc)
            for offset in (0, 1, 2):
                t0, t1 = _ts_range(today + timedelta(days=offset))
                for t, e in zip(*_almanac.find_discrete(t0, t1, f_ss)):
                    local_t = t.astimezone(tz)
                    if local_t > now:
                        if e and "sunrise" not in result:
                            result["sunrise"] = _fmt(local_t)
                        elif not e and "sunset" not in result:
                            result["sunset"] = _fmt(local_t)
                if "sunrise" in result and "sunset" in result:
                    break

            # ── 새벽/황혼/천문박명 (dark_twilight_day) ────────────────────
            # 0=Night, 1=Astronomical, 2=Nautical, 3=Civil, 4=Day
            # (3→4)=dawn, (4→3)=dusk, (0→1)=astro_dawn, (1→0)=astro_dusk
            _TW_MAP = {(2,3):"dawn", (3,2):"dusk", (0,1):"astro_dawn", (1,0):"astro_dusk"}
            f_tw = _almanac.dark_twilight_day(self._sf_eph, sf_loc)
            for offset in (0, 1, 2):
                t0, t1 = _ts_range(today + timedelta(days=offset))
                times, events = _almanac.find_discrete(t0, t1, f_tw)
                # t0 시각의 실제 상태를 초기값으로 사용 (prev_e=None이면 첫 이벤트 skip됨)
                prev_e = int(f_tw(t0))
                for t, cur_e in zip(times, events):
                    local_t = t.astimezone(tz)
                    if local_t > now:
                        key = _TW_MAP.get((prev_e, int(cur_e)))
                        if key and key not in result:
                            result[key] = _fmt(local_t)
                    prev_e = int(cur_e)
                if all(k in result for k in ("dawn", "dusk", "astro_dawn", "astro_dusk")):
                    break

            # ── 달 위상/조명율 ─────────────────────────────────────────────
            t_now = self._sf_ts.from_datetime(now)
            phase_deg = _almanac.moon_phase(self._sf_eph, t_now).degrees
            result["moon_phase"]        = self._moon_phase_name(phase_deg)
            result["moon_illumination"] = round(
                _almanac.fraction_illuminated(self._sf_eph, "moon", t_now) * 100)

            # ── 월출/월몰 (달이 없는 날 있으므로 3일 탐색) ────────────────
            f_rs = _almanac.risings_and_settings(
                self._sf_eph, self._sf_eph["Moon"], sf_loc)
            next_rise = next_set = None
            for offset in (0, 1, 2):
                t0, t1 = _ts_range(today + timedelta(days=offset))
                for t, e in zip(*_almanac.find_discrete(t0, t1, f_rs)):
                    local_t = t.astimezone(tz)
                    if local_t > now:
                        if e and next_rise is None:
                            next_rise = local_t
                        elif not e and next_set is None:
                            next_set = local_t
                if next_rise and next_set:
                    break
            result["moonrise"] = _fmt(next_rise) if next_rise else None
            result["moonset"]  = _fmt(next_set)  if next_set  else None

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
    def _moon_phase_name(deg: float) -> str:
        """skyfield moon_phase 반환값(0~360°) 기준 8단계 위상 이름"""
        d = deg % 360
        if   d <  22.5: return "삭"
        elif d <  67.5: return "초승달"
        elif d < 112.5: return "상현달"
        elif d < 157.5: return "준상현달"
        elif d < 202.5: return "보름달"
        elif d < 247.5: return "준하현달"
        elif d < 292.5: return "하현달"
        elif d < 337.5: return "그믐달"
        return "삭"

    def _eval_observation(
        self, weather: dict, now: "datetime", lat: float, lon: float
    ) -> "tuple[str, str]":
        """
        현재 날씨·태양 고도·달 고도·달 조명율을 종합하여 관측 조건과 사유를 반환한다.

        Returns:
            (condition, reason)
            condition : "최우수" | "우수" | "보통" | "불량" | "관측불가" | "분석불가"
            reason    : 조건 사유 (속성에 표시)
                        관측 불가 — "강수" | "흐림" | "주간"
                        관측 가능 — "달 없음, 맑음" | "달이 어두움, 맑음" | "달이 어두움"
                                    | "달이 밝지 않음" | "달이 밝음" | "달이 환함"
                                    | "구름많음"
        """
        condition_eng = weather.get("current_condition", "")

        # 1. 기상 상태 체크
        if condition_eng in {"rainy", "pouring", "snowy", "snowy-rainy"}:
            return "관측불가", "강수"

        if condition_eng == "cloudy":
            return "관측불가", "흐림"

        # 2. 태양 고도 체크 (skyfield 필요)
        try:
            if self._sf_eph is None or self._sf_ts is None:
                return "분석불가", "분석불가"

            sf_loc = _wgs84.latlon(lat, lon)
            t_now = self._sf_ts.from_datetime(now)

            # 태양 고도
            sun_astr = (
                (self._sf_eph["Earth"] + sf_loc).at(t_now).observe(self._sf_eph["Sun"])
            )
            sun_alt, _, _ = sun_astr.apparent().altaz()
            if sun_alt.degrees > -18:
                return "관측불가", "주간"

            # 달 고도 (지평선 7° 초과면 달이 떠 있음)
            moon_astr = (
                (self._sf_eph["Earth"] + sf_loc).at(t_now).observe(self._sf_eph["Moon"])
            )
            moon_alt, _, _ = moon_astr.apparent().altaz()
            moon_up = moon_alt.degrees > 7.0

        except Exception:
            return "분석불가", "분석불가"

        # 3. 구름 많음 (달 유무와 무관하게 불량)
        if condition_eng == "partlycloudy":
            return "불량", "구름많음"

        # 날씨 맑음 여부
        is_clear = condition_eng in ("", "sunny")

        # 날씨 미확인 suffix (단기예보 미승인/조회 실패 시)
        weather_suffix = "" if is_clear else " (날씨 미확인)"

        # 4. 달이 떠 있지 않은 경우 → 최우수
        if not moon_up:
            reason = f"달 없음, 맑음" if is_clear else f"달 없음{weather_suffix}"
            return "최우수", reason

        # 5. 달이 떠 있음 → 달 조명율로 판단
        illum = weather.get("moon_illumination", 100)
        try:
            illum = int(illum)
        except (TypeError, ValueError):
            illum = 100

        clear_suffix = ", 맑음" if is_clear else weather_suffix
        if illum <= 25:
            return "최우수", f"달이 어두움{clear_suffix}"
        elif illum <= 50:
            return "우수", f"달이 밝지 않음{clear_suffix}"
        elif illum <= 75:
            return "보통", f"달이 밝음{clear_suffix}"
        else:
            return "불량", f"달이 환함{weather_suffix}"

    # ── 날짜 지정 천문 계산 (HA 서비스용) ──────────────────────────────────
    async def calc_astronomical_for_date(
        self, lat: float, lon: float, target_date, eval_dt: "datetime | None" = None
    ) -> dict:
        """
        특정 날짜·좌표의 천문 이벤트를 계산해 dict로 반환한다.

        입력된 좌표의 단기예보를 조회하여 관측 조건 평가에 날씨 상태를 반영한다.
        단기예보 API가 미신청이거나 호출 실패 시 달 조명율+달 고도+태양고도만으로 평가한다.

        Args:
            lat, lon    : 위경도
            target_date : 조회 날짜
            eval_dt     : 관측 조건 평가 기준 시각 (None이면 target_date 정오)
        """
        if self._sf_eph is None or self._sf_ts is None:
            return {"error": "skyfield 라이브러리가 준비되지 않았습니다"}
        try:
            tz = self.api.tz
            sf_loc = _wgs84.latlon(lat, lon)
            result: dict = {}

            def _hm(t) -> str:
                return t.astimezone(tz).strftime("%H:%M")

            t0 = self._sf_ts.from_datetime(
                datetime(target_date.year, target_date.month, target_date.day, 0, 0, tzinfo=tz))
            t1 = self._sf_ts.from_datetime(
                datetime(target_date.year, target_date.month, target_date.day, 23, 59, tzinfo=tz))

            # 일출/일몰
            f_ss = _almanac.sunrise_sunset(self._sf_eph, sf_loc)
            sunrise = sunset = None
            for t, e in zip(*_almanac.find_discrete(t0, t1, f_ss)):
                local_t = t.astimezone(tz)
                if e and sunrise is None:
                    sunrise = _hm(local_t)
                elif not e and sunset is None:
                    sunset = _hm(local_t)
            result["sunrise"] = sunrise
            result["sunset"] = sunset

            # 박명 (새벽/황혼/천문박명)
            _TW_MAP = {(2, 3): "dawn", (3, 2): "dusk", (0, 1): "astro_dawn", (1, 0): "astro_dusk"}
            f_tw = _almanac.dark_twilight_day(self._sf_eph, sf_loc)
            prev_e = int(f_tw(t0))
            for t, cur_e in zip(*_almanac.find_discrete(t0, t1, f_tw)):
                local_t = t.astimezone(tz)
                key = _TW_MAP.get((prev_e, int(cur_e)))
                if key and key not in result:
                    result[key] = _hm(local_t)
                prev_e = int(cur_e)

            # 달 위상/조명율 (정오 기준)
            t_noon = self._sf_ts.from_datetime(
                datetime(target_date.year, target_date.month, target_date.day, 12, 0, tzinfo=tz))
            phase_deg = _almanac.moon_phase(self._sf_eph, t_noon).degrees
            illum = round(_almanac.fraction_illuminated(self._sf_eph, "moon", t_noon) * 100)
            result["moon_phase"] = KMAWeatherUpdateCoordinator._moon_phase_name(phase_deg)
            result["moon_illumination"] = illum

            # 월출/월몰
            f_rs = _almanac.risings_and_settings(self._sf_eph, self._sf_eph["Moon"], sf_loc)
            moonrise = moonset = None
            for t, e in zip(*_almanac.find_discrete(t0, t1, f_rs)):
                local_t = t.astimezone(tz)
                if e and moonrise is None:
                    moonrise = _hm(local_t)
                elif not e and moonset is None:
                    moonset = _hm(local_t)
            result["moonrise"] = moonrise
            result["moonset"] = moonset

            # ── 단기예보 조회 (입력 위치 기준) ───────────────────────────────
            # 날씨 상태를 관측 조건 평가에 반영하기 위해 입력 좌표의 격자로 조회
            # ── 단기예보 조회 (입력 위치 기준) ───────────────────────────────
            # weather_source: 관측 조건 평가에 날씨가 반영됐는지 여부를 나타냄
            #   "날씨+천문": 단기예보 조회 성공 → 날씨 상태 반영
            #   "천문만":    단기예보 미승인 또는 조회 실패 → 달 조명율+달 고도+태양고도만
            weather_for_obs: dict = {"moon_illumination": illum}
            weather_source = "천문만"   # 기본값: 날씨 미반영
            weather_kor: str = "API 조회 불가"

            if "short" in self.api._approved_apis:
                try:
                    nx, ny = convert_grid(lat, lon)
                    now_kst = datetime.now(tz)
                    adj = now_kst - timedelta(minutes=10)
                    valid_hours = [h for h in [2, 5, 8, 11, 14, 17, 20, 23] if h <= adj.hour]
                    base_h = max(valid_hours) if valid_hours else 23
                    base_d = adj.strftime("%Y%m%d") if valid_hours else (adj - timedelta(days=1)).strftime("%Y%m%d")

                    short_data = await self.api._fetch(
                        "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
                        {"serviceKey": self.api.api_key, "dataType": "JSON",
                         "base_date": base_d, "base_time": f"{base_h:02d}00",
                         "nx": nx, "ny": ny, "numOfRows": 1500},
                    )
                    items = (short_data or {}).get("response", {}).get("body", {}).get("items", {}).get("item", [])
                    if items:
                        ref_dt = eval_dt or datetime(
                            target_date.year, target_date.month, target_date.day, 12, 0, tzinfo=tz)
                        ref_date_str = ref_dt.strftime("%Y%m%d")
                        ref_time_str = f"{ref_dt.hour:02d}00"

                        forecast_map: dict = {}
                        for it in items:
                            forecast_map.setdefault(
                                it["fcstDate"], {}
                            ).setdefault(it["fcstTime"], {})[it["category"]] = it["fcstValue"]

                        day_data = forecast_map.get(ref_date_str, {})
                        times = sorted(day_data.keys())
                        best_t = next((t for t in times if t >= ref_time_str), times[-1] if times else None)
                        if best_t:
                            slot = day_data[best_t]
                            sky = slot.get("SKY")
                            pty = slot.get("PTY")
                            kor = self.api._get_sky_kor(sky, pty)
                            cond_eng = self.api.kor_to_condition(kor)
                            if cond_eng:
                                weather_for_obs["current_condition"] = cond_eng
                                weather_source = "날씨+천문"
                                weather_kor = kor
                            _LOGGER.debug(
                                "천문 액션 단기예보 조회 성공: (%s, %s) → %s (%s)",
                                lat, lon, kor, ref_dt.strftime("%Y%m%d %H:%M")
                            )
                except Exception as e:
                    _LOGGER.warning("천문 액션 단기예보 조회 실패 (날씨 무시): %s", e)
            else:
                _LOGGER.debug("단기예보 API 미승인 → 달 조명율+달 고도+태양고도만으로 관측 조건 평가")

            # ── 관측 조건 평가 ────────────────────────────────────────────────
            obs_dt = eval_dt or datetime(
                target_date.year, target_date.month, target_date.day, 12, 0, tzinfo=tz)
            obs_cond, obs_reason = self._eval_observation(weather_for_obs, obs_dt, lat, lon)
            result["observation_condition"] = obs_cond
            result["observation_reason"]    = obs_reason if obs_reason else None
            result["weather_source"]        = weather_source
            result["weather_condition"]     = weather_kor
            return result

        except Exception as e:
            _LOGGER.error("날짜별 천문 계산 실패: %s", e)
            return {"error": str(e)}

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
                    if is_korean_coord_loose(lat, lon):
                        self._last_lat, self._last_lon = lat, lon  # ← 성공 시 캐시 갱신
                        return lat, lon
                except Exception:
                    pass
        if self._last_lat is not None:
            return self._last_lat, self._last_lon
        try:
            lat, lon = float(self.hass.config.latitude), float(self.hass.config.longitude)
            if is_korean_coord_loose(lat, lon):
                return lat, lon
        except Exception:
            pass
        return None, None
