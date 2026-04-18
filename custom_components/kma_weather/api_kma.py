import logging
import asyncio
import math
import hashlib
from datetime import datetime, timedelta
from urllib.parse import unquote
from zoneinfo import ZoneInfo

_LOGGER = logging.getLogger(__name__)

# ── 특보 코드 → 한글 변환 ────────────────────────────────────────────────────
_WARN_TYPE_MAP: dict[str, tuple[str, str]] = {
    "1":  ("강풍주의보",     "강풍경보"),
    "2":  ("호우주의보",     "호우경보"),
    "3":  ("한파주의보",     "한파경보"),
    "4":  ("건조주의보",     "건조경보"),
    "5":  ("폭풍해일주의보", "폭풍해일경보"),
    "6":  ("풍랑주의보",     "풍랑경보"),
    "7":  ("태풍주의보",     "태풍경보"),
    "8":  ("대설주의보",     "대설경보"),
    "9":  ("황사주의보",     "황사경보"),
    "10": ("안개주의보",     "안개경보"),
    "11": ("지진해일주의보", "지진해일경보"),
    "12": ("폭염주의보",     "폭염경보"),
}

# ── API 서비스 정보 (미신청 감지용) ──────────────────────────────────────────
_API_SERVICES = {
    "short":   ("기상청 단기예보",       "https://www.data.go.kr/data/15084084/openapi.do"),
    "mid":     ("기상청 중기예보",       "https://www.data.go.kr/data/15059468/openapi.do"),
    "air":     ("에어코리아 대기오염정보", "https://www.data.go.kr/data/15073861/openapi.do"),
    "station": ("에어코리아 측정소정보",  "https://www.data.go.kr/data/15073877/openapi.do"),
    "warning": ("기상특보 조회서비스",   "https://www.data.go.kr/data/15000415/openapi.do"),
}

# 미신청으로 판단하는 resultCode 목록
_UNSUBSCRIBED_CODES = {"20", "22", "30", "31", "33"}


def _safe_float(v):
    try:
        if v == "" or v is None or v == "-":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


KOR_TO_CONDITION: dict[str, str] = {
    "맑음": "sunny",
    "구름많음": "partlycloudy",
    "흐림": "cloudy",
    "비": "rainy",
    "비/눈": "snowy-rainy",
    "눈": "snowy",
    "소나기": "pouring",
    "빗방울": "rainy",
    "빗방울/눈날림": "snowy-rainy",
    "눈날림": "snowy",
    "구름많고 비": "rainy",
    "구름많고 눈": "snowy",
    "구름많고 비/눈": "snowy-rainy",
    "구름많고 소나기": "pouring",
    "흐리고 비": "rainy",
    "흐리고 눈": "snowy",
    "흐리고 비/눈": "snowy-rainy",
    "흐리고 소나기": "pouring",
}


class KMAWeatherAPI:
    def __init__(self, session, api_key, hass=None):
        self.session = session
        self.api_key = unquote(api_key)
        self.hass = hass
        self.tz = ZoneInfo("Asia/Seoul")
        self.lat = self.lon = self.nx = self.ny = None

        # 에어코리아 측정소 캐시 (coordinator의 2km 캐시와 별도로 측정소명만 보관)
        self._cached_station: str | None = None
        self._cached_station_lat: float | None = None
        self._cached_station_lon: float | None = None

        self._nominatim_user_agent = self._build_nominatim_user_agent()

        self._cache_forecast_map: dict = {}
        self._cache_mid_ta: dict = {}
        self._cache_mid_land: dict = {}
        self._cache_mid_tm_fc_dt: datetime | None = None

        # API 미신청 알림 중복 방지 (서비스 키 → 알림 발송 여부)
        self._notified_unsubscribed: set[str] = set()

    def _build_nominatim_user_agent(self):
        base = "HomeAssistant-KMA-Weather"
        if self.hass:
            try:
                uuid = getattr(self.hass, "installation_uuid", None)
                if uuid:
                    return f"{base}/{uuid.replace('-', '')[:12]}"
            except Exception:
                pass
        try:
            hashed = hashlib.sha1(self.api_key.encode()).hexdigest()[:12]
            return f"{base}/{hashed}"
        except Exception:
            return base

    # ── API 미신청 감지 및 알림 ─────────────────────────────────────────────
    def _check_unsubscribed(self, service_key: str, result_code: str) -> bool:
        """
        resultCode가 미신청/접근거부 코드이면 HA 알림을 발송하고 True를 반환한다.
        같은 서비스에 대해 중복 알림은 발송하지 않는다.
        """
        if result_code not in _UNSUBSCRIBED_CODES:
            return False
        if service_key in self._notified_unsubscribed:
            return True

        self._notified_unsubscribed.add(service_key)
        name, url = _API_SERVICES.get(service_key, (service_key, ""))

        msg = (
            f"**기상청 스마트 날씨 — API 미신청 감지**\n\n"
            f"**{name}** 서비스가 활용신청되지 않았거나 접근이 거부되었습니다 "
            f"(오류코드: {result_code}).\n\n"
            f"아래 링크에서 활용신청 후 승인을 기다려 주세요:\n"
            f"[{name} 신청하기]({url})\n\n"
            f"신청 후 HA를 재시작하거나 수동 업데이트를 누르면 정상 작동합니다."
        )
        _LOGGER.warning("API 미신청 감지 [%s]: resultCode=%s → %s", service_key, result_code, url)

        if self.hass:
            try:
                self.hass.components.persistent_notification.async_create(
                    message=msg,
                    title="기상청 스마트 날씨: API 신청 필요",
                    notification_id=f"kma_weather_unsubscribed_{service_key}",
                )
            except Exception as e:
                _LOGGER.debug("persistent_notification 발송 실패: %s", e)

        return True

    async def _fetch(self, url, params, headers=None, timeout=15):
        try:
            async with self.session.get(
                url, params=params, headers=headers, timeout=timeout
            ) as response:
                response.raise_for_status()
                return await response.json(content_type=None)
        except Exception as err:
            _LOGGER.error("API 호출 실패 (%s): %s", url, err)
        return None

    def _extract_result_code(self, data: dict | None) -> str | None:
        """응답에서 resultCode를 추출한다."""
        if not data:
            return None
        return (
            data.get("response", {})
                .get("header", {})
                .get("resultCode")
        )

    # ── fetch_data: coordinator로부터 모든 구역코드를 전달받음 ───────────────
    async def fetch_data(
        self,
        lat: float, lon: float,
        nx: int, ny: int,
        reg_id_temp: str, reg_id_land: str,
        warn_area_code: str | None,
    ) -> dict | None:
        self.lat, self.lon, self.nx, self.ny = lat, lon, nx, ny
        now = datetime.now(self.tz)
        tasks = [
            self._get_short_term(now),
            self._get_mid_term(now, reg_id_temp, reg_id_land),
            self._get_air_quality(lat, lon),
            self._get_address(lat, lon),
            self._get_warning(warn_area_code),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        short_res, mid_res, air_data, address, warning = [
            r if not isinstance(r, Exception) else None for r in results
        ]
        if warning is None:
            warning = "없음"
        return self._merge_all(now, short_res, mid_res, air_data, address, warning)

    # ── 주소 (Nominatim) ────────────────────────────────────────────────────
    async def _get_address(self, lat: float, lon: float) -> str:
        try:
            url = "https://nominatim.openstreetmap.org/reverse"
            d = await self._fetch(
                url,
                params={"format": "json", "lat": lat, "lon": lon, "zoom": 16},
                headers={"User-Agent": self._nominatim_user_agent, "Accept-Language": "ko"},
                timeout=5,
            )
            if d:
                a = d.get("address", {})
                parts = [
                    a.get("city", a.get("province", "")),
                    a.get("borough", a.get("county", "")),
                    a.get("suburb", a.get("village", "")),
                ]
                return " ".join([p for p in parts if p]).strip()
        except:
            pass
        return f"{lat:.4f}, {lon:.4f}"

    # ── 에어코리아 (측정소 캐시는 API 내부에서 좌표 기준 관리) ─────────────
    async def _get_air_quality(self, lat: float, lon: float) -> dict:
        try:
            # 2km 이상 이동 시 측정소 캐시 무효화
            if (self._cached_station
                    and self._cached_station_lat is not None
                    and self._haversine_simple(
                        self._cached_station_lat, self._cached_station_lon, lat, lon
                    ) > 2.0):
                _LOGGER.debug(
                    "위치 이동 감지 → 에어코리아 측정소 캐시 무효화 (%s → 재계산)",
                    self._cached_station,
                )
                self._cached_station = None
                self._cached_station_lat = None
                self._cached_station_lon = None

            sn = self._cached_station
            if not sn:
                tm_x, tm_y = self._wgs84_to_tm(lat, lon)
                st_json = await self._fetch(
                    "https://apis.data.go.kr/B552584/MsrstnInfoInqireSvc/getNearbyMsrstnList",
                    {"serviceKey": self.api_key, "returnType": "json",
                     "tmX": f"{tm_x:.2f}", "tmY": f"{tm_y:.2f}"},
                )
                code = self._extract_result_code(st_json)
                if code and self._check_unsubscribed("station", code):
                    return {}
                items = (st_json.get("response", {}).get("body", {}).get("items", [])
                         if st_json else [])
                if not items:
                    return {}
                sn = items[0].get("stationName")
                self._cached_station = sn
                self._cached_station_lat = lat
                self._cached_station_lon = lon

            air_json = await self._fetch(
                "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty",
                {"serviceKey": self.api_key, "returnType": "json",
                 "stationName": sn, "dataTerm": "daily", "ver": "1.3"},
            )
            code = self._extract_result_code(air_json)
            if code and self._check_unsubscribed("air", code):
                return {"station": sn}

            ai_list = (air_json.get("response", {}).get("body", {}).get("items", [])
                       if air_json else [])
            if not ai_list:
                return {"station": sn}

            ai = ai_list[0]
            return {
                "pm10Value": ai.get("pm10Value"),
                "pm10Grade": self._translate_grade(ai.get("pm10Grade") or ai.get("pm10Grade1h")),
                "pm25Value": ai.get("pm25Value"),
                "pm25Grade": self._translate_grade(ai.get("pm25Grade") or ai.get("pm25Grade1h")),
                "station": sn,
            }
        except Exception as e:
            _LOGGER.error("Air quality fetch error: %s", e)
            return {}

    def _translate_grade(self, g):
        return {"1": "좋음", "2": "보통", "3": "나쁨", "4": "매우나쁨"}.get(str(g), "정보없음")

    # ── 단기예보 ────────────────────────────────────────────────────────────
    async def _get_short_term(self, now: datetime) -> dict | None:
        adj = now - timedelta(minutes=10)
        hour = adj.hour
        valid_hours = [h for h in [2, 5, 8, 11, 14, 17, 20, 23] if h <= hour]
        if valid_hours:
            base_h = max(valid_hours)
            base_d = adj.strftime("%Y%m%d")
        else:
            base_h = 23
            base_d = (adj - timedelta(days=1)).strftime("%Y%m%d")

        data = await self._fetch(
            "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
            {"serviceKey": self.api_key, "dataType": "JSON",
             "base_date": base_d, "base_time": f"{base_h:02d}00",
             "nx": self.nx, "ny": self.ny, "numOfRows": 1500},
        )
        code = self._extract_result_code(data)
        if code and self._check_unsubscribed("short", code):
            return None
        return data

    # ── 중기예보 (reg_id를 파라미터로 수신) ────────────────────────────────
    def _get_mid_base_dt(self, now: datetime) -> datetime:
        effective = now - timedelta(minutes=30)
        if effective.hour < 6:
            return (effective - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
        elif effective.hour < 18:
            return effective.replace(hour=6, minute=0, second=0, microsecond=0)
        else:
            return effective.replace(hour=18, minute=0, second=0, microsecond=0)

    async def _get_mid_term(
        self, now: datetime, reg_id_temp: str, reg_id_land: str
    ) -> tuple:
        tm_fc_dt = self._get_mid_base_dt(now)
        base = tm_fc_dt.strftime("%Y%m%d%H%M")

        async def _fetch_both(b):
            return await asyncio.gather(
                self._fetch(
                    "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa",
                    {"serviceKey": self.api_key, "dataType": "JSON",
                     "regId": reg_id_temp, "tmFc": b},
                ),
                self._fetch(
                    "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst",
                    {"serviceKey": self.api_key, "dataType": "JSON",
                     "regId": reg_id_land, "tmFc": b},
                ),
                return_exceptions=True,
            )

        results = await _fetch_both(base)

        # 미신청 체크 (두 API 중 하나라도 미신청이면 알림)
        for res in results:
            if not isinstance(res, Exception):
                code = self._extract_result_code(res)
                if code and self._check_unsubscribed("mid", code):
                    return (None, None, tm_fc_dt)

        def _is_valid(res):
            if isinstance(res, Exception) or not res:
                return False
            items = res.get("response", {}).get("body", {}).get("items", {}).get("item", [])
            return len(items) > 0

        if not _is_valid(results[0]) or not _is_valid(results[1]):
            prev_dt = (
                (tm_fc_dt - timedelta(days=1)).replace(hour=18)
                if tm_fc_dt.hour == 6
                else tm_fc_dt.replace(hour=6)
            )
            _LOGGER.warning(
                "중기예보 최신(%s) 응답이 비어있습니다. 이전 시각(%s)으로 재시도합니다.",
                base, prev_dt.strftime("%Y%m%d%H%M"),
            )
            retry_results = await _fetch_both(prev_dt.strftime("%Y%m%d%H%M"))
            if _is_valid(retry_results[0]) and _is_valid(retry_results[1]):
                return (retry_results[0], retry_results[1], prev_dt)

        return (
            results[0] if not isinstance(results[0], Exception) else None,
            results[1] if not isinstance(results[1], Exception) else None,
            tm_fc_dt,
        )

    # ── 기상특보 (warn_area_code를 파라미터로 수신) ─────────────────────────
    async def _get_warning(self, warn_area_code: str | None) -> str:
        if not warn_area_code:
            return "없음"
        try:
            now = datetime.now(self.tz)
            from_tm = (now - timedelta(days=5)).strftime("%Y%m%d")
            to_tm = now.strftime("%Y%m%d")

            data = await self._fetch(
                "https://apis.data.go.kr/1360000/WthrWrnInfoService/getPwnCd",
                {"serviceKey": self.api_key, "dataType": "JSON",
                 "areaCode": warn_area_code,
                 "fromTmFc": from_tm, "toTmFc": to_tm,
                 "numOfRows": 1000, "pageNo": 1},
            )
            code = self._extract_result_code(data)
            if code and self._check_unsubscribed("warning", code):
                return "없음"
            if not data:
                return "없음"

            items = (
                data.get("response", {})
                    .get("body", {})
                    .get("items", {})
                    .get("item", [])
            )
            if not items:
                return "없음"

            active = [
                item for item in items
                if str(item.get("command", "")) in ("1", "3")
                and str(item.get("cancel", "1")) == "0"
                and str(item.get("endTime", "1")) == "0"
            ]
            if not active:
                return "없음"

            warn_names, seen = [], set()
            for item in active:
                pair = _WARN_TYPE_MAP.get(str(item.get("warnVar", "")))
                if pair:
                    name = pair[1] if str(item.get("warnStress", "0")) == "1" else pair[0]
                    if name not in seen:
                        seen.add(name)
                        warn_names.append(name)

            return ", ".join(warn_names) if warn_names else "없음"

        except Exception as e:
            _LOGGER.error("기상특보 조회 오류: %s", e)
            return "없음"

    # ── 유틸리티 ────────────────────────────────────────────────────────────
    def _haversine_simple(self, lat1, lon1, lat2, lon2) -> float:
        r = 6371.0
        dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) ** 2
             + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
             * math.sin(dlon / 2) ** 2)
        return r * 2 * math.asin(math.sqrt(a))

    def _calculate_apparent_temp(self, temp, reh, wsd):
        t, rh, v = _safe_float(temp), _safe_float(reh), _safe_float(wsd)
        if t is None:
            return temp
        v_kmh = v * 3.6 if v is not None else 0
        if t <= 10 and v_kmh >= 4.8:
            return round(13.12 + 0.6215 * t - 11.37 * (v_kmh ** 0.16) + 0.3965 * t * (v_kmh ** 0.16), 1)
        if t >= 25 and rh is not None and rh >= 40:
            return round(0.5 * (t + 61.0 + ((t - 68.0) * 1.2) + (rh * 0.094)), 1)
        return t

    @staticmethod
    def kor_to_condition(kor: str | None) -> str | None:
        if kor is None:
            return None
        return KOR_TO_CONDITION.get(kor)

    def _get_short_ampm(self, day_data: dict) -> tuple[str, str]:
        def rep_slot(hours):
            skies, ptys = [], []
            for t in hours:
                if t in day_data:
                    td = day_data[t]
                    if "SKY" in td: skies.append(td["SKY"])
                    if "PTY" in td: ptys.append(td["PTY"])
            if not skies and not ptys:
                return None
            pty_rep = max(set(ptys), key=ptys.count) if ptys else "0"
            sky_rep = max(set(skies), key=skies.count) if skies else "1"
            return self._get_sky_kor(sky_rep, pty_rep)

        am_hours = [f"{h:02d}00" for h in range(6, 12)]
        pm_hours = [f"{h:02d}00" for h in range(12, 18)]
        wf_am = rep_slot(am_hours) or "맑음"
        wf_pm = rep_slot(pm_hours) or wf_am
        return wf_am, wf_pm

    # ── 데이터 병합 ─────────────────────────────────────────────────────────
    def _merge_all(self, now, short_res, mid_res, air_data, address=None, warning="없음"):
        weather_data = {
            "TMP": None, "REH": None, "WSD": None, "VEC": None, "POP": None,
            "TMX_today": None, "TMN_today": None, "TMX_tomorrow": None, "TMN_tomorrow": None,
            "rain_start_time": "강수없음", "forecast_daily": [], "forecast_twice_daily": [],
            "address": address, "warning": warning,
        }

        new_forecast_map = {}
        if short_res and "response" in short_res:
            for it in (short_res.get("response", {}).get("body", {})
                       .get("items", {}).get("item", [])):
                new_forecast_map.setdefault(
                    it["fcstDate"], {}
                ).setdefault(it["fcstTime"], {})[it["category"]] = it["fcstValue"]

        if new_forecast_map:
            self._cache_forecast_map = new_forecast_map
            _LOGGER.debug("단기예보 캐시 갱신: %d일치", len(new_forecast_map))
        else:
            _LOGGER.warning(
                "단기예보 수신 실패 또는 빈 응답 → 캐시 재사용 (날짜 수: %d)",
                len(self._cache_forecast_map),
            )

        forecast_map = self._cache_forecast_map

        if mid_res and isinstance(mid_res, tuple) and len(mid_res) == 3:
            mid_ta_res, mid_land_res, new_tm_fc_dt = mid_res
        else:
            mid_ta_res = mid_res[0] if mid_res else None
            mid_land_res = mid_res[1] if mid_res and len(mid_res) > 1 else None
            new_tm_fc_dt = self._get_mid_base_dt(now)

        new_mid_ta = (mid_ta_res.get("response", {}).get("body", {}).get("items", {}).get("item", [{}])[0]
                      if mid_ta_res else None)
        new_mid_land = (mid_land_res.get("response", {}).get("body", {}).get("items", {}).get("item", [{}])[0]
                        if mid_land_res else None)

        if new_mid_ta and new_mid_land:
            self._cache_mid_ta = new_mid_ta
            self._cache_mid_land = new_mid_land
            self._cache_mid_tm_fc_dt = new_tm_fc_dt
            _LOGGER.debug("중기예보 캐시 갱신: tmFc=%s", new_tm_fc_dt.strftime("%Y%m%d%H%M"))
        else:
            _LOGGER.warning(
                "중기예보 수신 실패 또는 빈 응답 → 캐시 재사용 (tmFc=%s)",
                self._cache_mid_tm_fc_dt.strftime("%Y%m%d%H%M")
                if self._cache_mid_tm_fc_dt else "없음",
            )

        mid_ta = self._cache_mid_ta
        mid_land = self._cache_mid_land
        tm_fc_dt = self._cache_mid_tm_fc_dt if self._cache_mid_tm_fc_dt else new_tm_fc_dt

        today_str, curr_h = now.strftime("%Y%m%d"), f"{now.hour:02d}00"
        if today_str in forecast_map:
            times = sorted(forecast_map[today_str].keys())
            best_t = next((t for t in times if t >= curr_h), times[-1] if times else None)
            if best_t:
                weather_data.update(forecast_map[today_str][best_t])
        else:
            past_dates = sorted(d for d in forecast_map if d < today_str)
            if past_dates:
                last_date = past_dates[-1]
                times = sorted(forecast_map[last_date].keys())
                if times:
                    weather_data.update(forecast_map[last_date][times[-1]])
                    _LOGGER.debug(
                        "오늘(%s) 날짜 데이터 없음 → 직전(%s) 마지막 슬롯(%s) 사용",
                        today_str, last_date, times[-1],
                    )

        for d_str in sorted(forecast_map.keys()):
            rain_times = [
                t_str for t_str in sorted(forecast_map[d_str].keys())
                if _safe_float(forecast_map[d_str][t_str].get("PTY", "0")) > 0
            ]
            if rain_times:
                t = rain_times[0]
                month, day = int(d_str[4:6]), int(d_str[6:8])
                hour, minute = int(t[:2]), int(t[2:])
                if minute > 0:
                    weather_data["rain_start_time"] = f"{month}월 {day}일 {hour}시 {minute}분"
                else:
                    weather_data["rain_start_time"] = f"{month}월 {day}일 {hour}시"
                break

        twice_daily, daily_forecast = [], []

        for i in range(10):
            target_date = now + timedelta(days=i)
            d_str = target_date.strftime("%Y%m%d")
            t_max = t_min = wf_am = wf_pm = None

            if i <= 3:
                if d_str in forecast_map:
                    short_temps = [
                        _safe_float(v.get("TMP"))
                        for v in forecast_map[d_str].values() if "TMP" in v
                    ]
                    valid_temps = [t for t in short_temps if t is not None]
                    t_max = max(valid_temps) if valid_temps else None
                    t_min = min(valid_temps) if valid_temps else None

                    am_slot = "0900" if "0900" in forecast_map[d_str] else next(
                        (t for t in sorted(forecast_map[d_str].keys()) if t < "1200"), None)
                    pm_slot = next(
                        (t for t in ["1500", "1200", "1800"] if t in forecast_map[d_str]), None)

                    if am_slot:
                        wf_am = self._get_sky_kor(
                            forecast_map[d_str][am_slot].get("SKY"),
                            forecast_map[d_str][am_slot].get("PTY"))
                    if pm_slot:
                        wf_pm = self._get_sky_kor(
                            forecast_map[d_str][pm_slot].get("SKY"),
                            forecast_map[d_str][pm_slot].get("PTY"))
                    else:
                        wf_pm = wf_am

                _LOGGER.debug("단기예보 i=%d date=%s t_max=%s t_min=%s", i, d_str, t_max, t_min)

            else:
                mid_day_idx = (target_date.date() - tm_fc_dt.date()).days
                t_max_mid = _safe_float(mid_ta.get(f"taMax{mid_day_idx}")) if mid_ta else None
                t_min_mid = _safe_float(mid_ta.get(f"taMin{mid_day_idx}")) if mid_ta else None
                wf_am_mid = self._translate_mid_condition_kor(
                    mid_land.get(f"wf{mid_day_idx}Am") or mid_land.get(f"wf{mid_day_idx}")
                ) if mid_land else None
                wf_pm_mid = self._translate_mid_condition_kor(
                    mid_land.get(f"wf{mid_day_idx}Pm") or mid_land.get(f"wf{mid_day_idx}")
                ) if mid_land else None

                if t_max_mid is not None and t_min_mid is not None:
                    t_max, t_min = t_max_mid, t_min_mid
                    wf_am = wf_am_mid or "맑음"
                    wf_pm = wf_pm_mid or "맑음"
                elif i <= 5 and d_str in forecast_map:
                    short_temps = [
                        _safe_float(v.get("TMP"))
                        for v in forecast_map[d_str].values() if "TMP" in v
                    ]
                    valid_temps = [t for t in short_temps if t is not None]
                    t_max = max(valid_temps) if valid_temps else None
                    t_min = min(valid_temps) if valid_temps else None
                    wf_am, wf_pm = self._get_short_ampm(forecast_map[d_str])

            if i == 0:
                weather_data["wf_am_today"] = wf_am
                weather_data["wf_pm_today"] = wf_pm
                weather_data["_raw_today_max"] = t_max
                weather_data["_raw_today_min"] = t_min
            elif i == 1:
                weather_data.update({
                    "TMX_tomorrow": t_max, "TMN_tomorrow": t_min,
                    "wf_am_tomorrow": wf_am, "wf_pm_tomorrow": wf_pm,
                })

            for is_am in [True, False]:
                if i == 0 and is_am and now.hour >= 12:
                    continue
                twice_daily.append({
                    "datetime": target_date.replace(
                        hour=9 if is_am else 21, minute=0, second=0, microsecond=0
                    ).isoformat(),
                    "is_daytime": is_am,
                    "native_temperature": t_max,
                    "native_templow": t_min,
                    "condition": self.kor_to_condition(wf_am if is_am else wf_pm),
                    "_day_index": i,
                })

            daily_forecast.append({
                "datetime": target_date.replace(
                    hour=12, minute=0, second=0, microsecond=0
                ).isoformat(),
                "native_temperature": t_max,
                "native_templow": t_min,
                "condition": self.kor_to_condition(wf_pm),
                "_day_index": i,
            })

        weather_data.update({"forecast_twice_daily": twice_daily, "forecast_daily": daily_forecast})
        kor_now = self._get_sky_kor(weather_data.get("SKY"), weather_data.get("PTY"))
        weather_data.update({
            "current_condition_kor": kor_now,
            "current_condition": self.kor_to_condition(kor_now),
            "apparent_temp": self._calculate_apparent_temp(
                weather_data.get("TMP"), weather_data.get("REH"), weather_data.get("WSD")),
        })
        if weather_data.get("VEC"):
            weather_data["VEC_KOR"] = self._get_vec_kor(weather_data["VEC"])
        return {"weather": weather_data, "air": air_data or {}, "raw_forecast": forecast_map}

    def _translate_mid_condition_kor(self, wf: str) -> str:
        wf = str(wf or "맑음")
        if wf in KOR_TO_CONDITION: return wf
        if "비/눈" in wf: return "비/눈"
        if "소나기" in wf: return "소나기"
        if "비" in wf: return "비"
        if "눈" in wf: return "눈"
        if "흐리" in wf or "흐림" in wf: return "흐림"
        if "구름" in wf: return "구름많음"
        return "맑음"

    def _get_sky_kor(self, sky, pty):
        p, s = str(pty or "0"), str(sky or "1")
        if p in ["1", "2", "3", "4", "5", "6", "7"]:
            return {"1": "비", "2": "비/눈", "3": "눈", "4": "소나기",
                    "5": "빗방울", "6": "빗방울/눈날림", "7": "눈날림"}.get(p, "비")
        return "맑음" if s == "1" else ("구름많음" if s == "3" else "흐림")

    def _get_vec_kor(self, vec):
        v = _safe_float(vec)
        if v is None: return None
        if 22.5 <= v < 67.5:   return "북동"
        elif 67.5 <= v < 112.5:  return "동"
        elif 112.5 <= v < 157.5: return "남동"
        elif 157.5 <= v < 202.5: return "남"
        elif 202.5 <= v < 247.5: return "남서"
        elif 247.5 <= v < 292.5: return "서"
        elif 292.5 <= v < 337.5: return "북서"
        return "북"

    def _translate_mid_condition(self, wf): return self.kor_to_condition(self._translate_mid_condition_kor(wf))
    def _get_condition(self, s, p): return self.kor_to_condition(self._get_sky_kor(s, p))

    def _wgs84_to_tm(self, lat, lon):
        a, f = 6378137.0, 1 / 298.257222101
        e2 = 2 * f - f ** 2
        lat0, lon0 = math.radians(38.0), math.radians(127.0)
        phi, lam = math.radians(lat), math.radians(lon)
        N = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
        T = math.tan(phi) ** 2
        C = e2 / (1 - e2) * math.cos(phi) ** 2
        A = math.cos(phi) * (lam - lon0)

        def M(p):
            return a * ((1 - e2/4 - 3*e2**2/64 - 5*e2**3/256) * p
                        - (3*e2/8 + 3*e2**2/32 + 45*e2**3/1024) * math.sin(2*p)
                        + (15*e2**2/256 + 45*e2**3/1024) * math.sin(4*p)
                        - (35*e2**3/3072) * math.sin(6*p))

        return (
            200000.0 + N * (A + (1-T+C)*A**3/6 + (5-18*T+T**2+72*C-58*(e2/(1-e2)))*A**5/120),
            500000.0 + (M(phi) - M(lat0) + N*math.tan(phi)*(
                A**2/2 + (5-T+9*C+4*C**2)*A**4/24 + (61-58*T+T**2+600*C-330*(e2/(1-e2)))*A**6/720)),
        )
