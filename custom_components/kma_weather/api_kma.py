import logging
import asyncio
import math
import aiohttp
import hashlib
from datetime import datetime, timedelta
from urllib.parse import unquote
from zoneinfo import ZoneInfo

_LOGGER = logging.getLogger(__name__)

def _safe_float(v):
    try:
        if v == "" or v is None or v == "-": return None
        return float(v)
    except (TypeError, ValueError): return None

KOR_TO_CONDITION: dict[str, str] = {
    "맑음": "sunny", "구름많음": "partlycloudy", "흐림": "cloudy",
    "비": "rainy", "비/눈": "rainy", "소나기": "rainy", "눈": "snowy",
}

class KMAWeatherAPI:
    def __init__(self, session, api_key, reg_id_temp, reg_id_land, hass=None):
        self.session = session
        self.api_key = unquote(api_key)
        self.reg_id_temp = reg_id_temp
        self.reg_id_land = reg_id_land
        self.hass = hass
        self.tz = ZoneInfo("Asia/Seoul")
        self.lat = self.lon = self.nx = self.ny = None
        self._cached_station = self._cached_lat_lon = self._station_cache_time = None
        self._nominatim_user_agent = self._build_nominatim_user_agent()

    def _build_nominatim_user_agent(self):
        base = "HomeAssistant-KMA-Weather"
        # UUID 로직 복구 (테스트 실패 원인)
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

    async def _fetch(self, url, params, headers=None, timeout=15):
        try:
            async with self.session.get(url, params=params, headers=headers, timeout=timeout) as response:
                response.raise_for_status()
                return await response.json(content_type=None)
        except Exception as err:
            _LOGGER.error("API 호출 실패 (%s): %s", url, err)
        return None

    async def fetch_data(self, lat, lon, nx, ny):
        self.lat, self.lon, self.nx, self.ny = lat, lon, nx, ny
        now = datetime.now(self.tz)
        tasks = [
            self._get_short_term(now), self._get_mid_term(now),
            self._get_air_quality(), self._get_address(lat, lon)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return self._merge_all(now, *[r if not isinstance(r, Exception) else None for r in results])

    async def _get_address(self, lat, lon):
        try:
            url = "https://nominatim.openstreetmap.org/reverse"
            d = await self._fetch(url, params={"format": "json", "lat": lat, "lon": lon, "zoom": 16}, headers={"User-Agent": self._nominatim_user_agent, "Accept-Language": "ko"}, timeout=5)
            if d:
                a = d.get("address", {})
                parts = [a.get("city", a.get("province", "")), a.get("borough", a.get("county", "")), a.get("suburb", a.get("village", ""))]
                return " ".join([p for p in parts if p]).strip()
        except: pass
        return f"{lat:.4f}, {lon:.4f}"

    async def _get_air_quality(self):
        try:
            sn = self._cached_station
            if not sn:
                tm_x, tm_y = self._wgs84_to_tm(self.lat, self.lon)
                st_json = await self._fetch("https://apis.data.go.kr/B552584/MsrstnInfoInqireSvc/getNearbyMsrstnList", {"serviceKey": self.api_key, "returnType": "json", "tmX": f"{tm_x:.2f}", "tmY": f"{tm_y:.2f}"})
                items = st_json.get("response", {}).get("body", {}).get("items", []) if st_json else []
                if not items: return {}
                sn = self._cached_station = items[0].get("stationName")

            air_json = await self._fetch("https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty", {"serviceKey": self.api_key, "returnType": "json", "stationName": sn, "dataTerm": "daily", "ver": "1.3"})
            ai_list = air_json.get("response", {}).get("body", {}).get("items", []) if air_json else []
            if not ai_list: return {"station": sn}

            ai = ai_list[0]
            return {
                "pm10Value": ai.get("pm10Value"),
                "pm10Grade": self._translate_grade(ai.get("pm10Grade") or ai.get("pm10Grade1h")),
                "pm25Value": ai.get("pm25Value"),
                "pm25Grade": self._translate_grade(ai.get("pm25Grade") or ai.get("pm25Grade1h")),
                "station": sn,
            }
        except Exception as e:
            _LOGGER.error(f"Air quality fetch error: {e}")
            return {}

    def _translate_grade(self, g):
        return {"1": "좋음", "2": "보통", "3": "나쁨", "4": "매우나쁨"}.get(str(g), "정보없음")

    async def _get_short_term(self, now):
        adj = now - timedelta(minutes=10)
        hour = adj.hour
        valid_hours = [h for h in [2, 5, 8, 11, 14, 17, 20, 23] if h <= hour]
        base_h = max(valid_hours) if valid_hours else 23
        base_d = adj.strftime("%Y%m%d") if valid_hours else (adj - timedelta(days=1)).strftime("%Y%m%d")
        return await self._fetch("https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst", {"serviceKey": self.api_key, "dataType": "JSON", "base_date": base_d, "base_time": f"{base_h:02d}00", "nx": self.nx, "ny": self.ny, "numOfRows": 1500})

    async def _get_mid_term(self, now):
        base = (now if now.hour >= 18 else (now if now.hour >= 6 else now - timedelta(days=1))).strftime("%Y%m%d") + ("0600" if 6 <= now.hour < 18 else "1800")
        return await asyncio.gather(
            self._fetch("https://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa", {"serviceKey": self.api_key, "dataType": "JSON", "regId": self.reg_id_temp, "tmFc": base}),
            self._fetch("https://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst", {"serviceKey": self.api_key, "dataType": "JSON", "regId": self.reg_id_land, "tmFc": base})
        )

    def _calculate_apparent_temp(self, temp, reh, wsd):
        t, rh, v = _safe_float(temp), _safe_float(reh), _safe_float(wsd)
        if t is None: return temp
        v_kmh = v * 3.6 if v is not None else 0
        if t <= 10 and v_kmh >= 4.8: return round(13.12 + 0.6215 * t - 11.37 * (v_kmh ** 0.16) + 0.3965 * t * (v_kmh ** 0.16), 1)
        if t >= 25 and rh is not None and rh >= 40: return round(0.5 * (t + 61.0 + ((t - 68.0) * 1.2) + (rh * 0.094)), 1)
        return t

    @staticmethod
    def kor_to_condition(kor: str | None) -> str | None:
        if kor is None: return None
        return KOR_TO_CONDITION.get(kor)

    def _merge_all(self, now, short_res, mid_res, air_data, address=None):
        weather_data = {
            "TMP": None, "REH": None, "WSD": None, "VEC": None, "POP": None,
            "TMX_today": None, "TMN_today": None, "TMX_tomorrow": None, "TMN_tomorrow": None,
            "rain_start_time": "강수없음", "forecast_daily": [], "forecast_twice_daily": [], "address": address,
        }

        forecast_map = {}
        if short_res and "response" in short_res:
            for it in short_res.get("response", {}).get("body", {}).get("items", {}).get("item", []):
                forecast_map.setdefault(it["fcstDate"], {}).setdefault(it["fcstTime"], {})[it["category"]] = it["fcstValue"]

            today_str, curr_h = now.strftime("%Y%m%d"), f"{now.hour:02d}00"
            if today_str in forecast_map:
                times = sorted(forecast_map[today_str].keys())
                best_t = next((t for t in times if t >= curr_h), times[-1] if times else None)
                if best_t: weather_data.update(forecast_map[today_str][best_t])

            for d_str in sorted(forecast_map.keys()):
                rain_times = [t_str for t_str in sorted(forecast_map[d_str].keys()) if _safe_float(forecast_map[d_str][t_str].get("PTY", "0")) > 0]
                if rain_times:
                    t = rain_times[0]
                    weather_data["rain_start_time"] = f"{t[:2]}:{t[2:]}"
                    break

        mid_ta = mid_res[0].get("response",{}).get("body",{}).get("items",{}).get("item",[{}])[0] if mid_res and mid_res[0] else {}
        mid_land = mid_res[1].get("response",{}).get("body",{}).get("items",{}).get("item",[{}])[0] if mid_res and mid_res[1] else {}

        twice_daily, daily_forecast = [], []

        include_daytime_today = now.hour < 12
        today_str = now.strftime("%Y%m%d")

        # 오늘 최고/최저 기온 계산 (센서 동기화)
        if today_str in forecast_map:
            tmps_today = [
                _safe_float(v.get("TMP"))
                for v in forecast_map[today_str].values()
                if _safe_float(v.get("TMP")) is not None
            ]
            if tmps_today:
                weather_data["TMX_today"] = max(tmps_today)
                weather_data["TMN_today"] = min(tmps_today)

        for i in range(10):
            target_date = now + timedelta(days=i)
            d_str = target_date.strftime("%Y%m%d")

            t_max = t_min = None
            wf_am = wf_pm = None

            # ---------------------------
            # 0일차: 오늘
            # ---------------------------
            if i == 0:
                t_max = weather_data.get("TMX_today")
                t_min = weather_data.get("TMN_today")

                wf_am = self._get_sky_kor(
                    forecast_map.get(d_str, {}).get("0900", {}).get("SKY"),
                    forecast_map.get(d_str, {}).get("0900", {}).get("PTY"),
                )
                wf_pm = self._get_sky_kor(
                    forecast_map.get(d_str, {}).get("1500", {}).get("SKY"),
                    forecast_map.get(d_str, {}).get("1500", {}).get("PTY"),
                )

                weather_data["wf_am_today"] = wf_am
                weather_data["wf_pm_today"] = wf_pm

            # ---------------------------
            # 1일차: 내일
            # ---------------------------
            elif i == 1:
                t_max = _safe_float(mid_ta.get("taMax1"))
                t_min = _safe_float(mid_ta.get("taMin1"))

                wf_am = self._get_sky_kor(
                    forecast_map.get(d_str, {}).get("0900", {}).get("SKY"),
                    forecast_map.get(d_str, {}).get("0900", {}).get("PTY"),
                )
                wf_pm = self._get_sky_kor(
                    forecast_map.get(d_str, {}).get("1500", {}).get("SKY"),
                    forecast_map.get(d_str, {}).get("1500", {}).get("PTY"),
                )

                weather_data.update({
                    "TMX_tomorrow": t_max,
                    "TMN_tomorrow": t_min,
                    "wf_am_tomorrow": wf_am,
                    "wf_pm_tomorrow": wf_pm,
                })

            # ---------------------------
            # 2~3일차: 단기예보
            # ---------------------------
            elif i in (2, 3):
                tmps = [
                    _safe_float(v.get("TMP"))
                    for v in forecast_map.get(d_str, {}).values()
                    if _safe_float(v.get("TMP")) is not None
                ]
                if tmps:
                    t_max = max(tmps)
                    t_min = min(tmps)

                wf_am = self._get_sky_kor(
                    forecast_map.get(d_str, {}).get("0900", {}).get("SKY"),
                    forecast_map.get(d_str, {}).get("0900", {}).get("PTY"),
                )
                wf_pm = self._get_sky_kor(
                    forecast_map.get(d_str, {}).get("1500", {}).get("SKY"),
                    forecast_map.get(d_str, {}).get("1500", {}).get("PTY"),
                )

            # ---------------------------
            # 4~5일차: 단기 + 중기 비교
            # ---------------------------
            elif i in (4, 5):
                tmps = [
                    _safe_float(v.get("TMP"))
                    for v in forecast_map.get(d_str, {}).values()
                    if _safe_float(v.get("TMP")) is not None
                ]

                short_max = max(tmps) if tmps else None
                short_min = min(tmps) if tmps else None

                mid_max = _safe_float(mid_ta.get(f"taMax{i}"))
                mid_min = _safe_float(mid_ta.get(f"taMin{i}"))

                t_max = short_max if short_max is not None else mid_max
                t_min = short_min if short_min is not None else mid_min

                wf_am = self._get_sky_kor(
                    forecast_map.get(d_str, {}).get("0900", {}).get("SKY"),
                    forecast_map.get(d_str, {}).get("0900", {}).get("PTY"),
                ) or self._translate_mid_condition_kor(mid_land.get(f"wf{i}Am"))

                wf_pm = self._get_sky_kor(
                    forecast_map.get(d_str, {}).get("1500", {}).get("SKY"),
                    forecast_map.get(d_str, {}).get("1500", {}).get("PTY"),
                ) or self._translate_mid_condition_kor(mid_land.get(f"wf{i}Pm"))

            # ---------------------------
            # 6~9일차: 중기예보
            # ---------------------------
            else:
                t_max = _safe_float(mid_ta.get(f"taMax{i}"))
                t_min = _safe_float(mid_ta.get(f"taMin{i}"))
                wf_am = self._translate_mid_condition_kor(
                    mid_land.get(f"wf{i}Am") or mid_land.get(f"wf{i}")
                )
                wf_pm = self._translate_mid_condition_kor(
                    mid_land.get(f"wf{i}Pm") or mid_land.get(f"wf{i}")
                )

            # ---------------------------
            # Twice Daily Forecast
            # ---------------------------
            include_daytime = True
            if i == 0:
                include_daytime = include_daytime_today

            if include_daytime and wf_am is not None:
                twice_daily.append({
                    "datetime": target_date.replace(
                        hour=9, minute=0, second=0, microsecond=0
                    ).isoformat(),
                    "is_daytime": True,
                    "native_temperature": t_max,
                    "native_templow": t_min,
                    "condition": self.kor_to_condition(wf_am),
                })

            if wf_pm is not None:
                twice_daily.append({
                    "datetime": target_date.replace(
                        hour=21, minute=0, second=0, microsecond=0
                    ).isoformat(),
                    "is_daytime": False,
                    "native_temperature": t_max,
                    "native_templow": t_min,
                    "condition": self.kor_to_condition(wf_pm),
                })

            # ---------------------------
            # Daily Forecast
            # ---------------------------
            if t_max is not None:
                daily_forecast.append({
                    "datetime": target_date.replace(
                        hour=12, minute=0, second=0, microsecond=0
                    ).isoformat(),
                    "native_temperature": t_max,
                    "native_templow": t_min,
                    "condition": self.kor_to_condition(wf_pm),
                })

        weather_data.update({"forecast_twice_daily": twice_daily, "forecast_daily": daily_forecast})

        # 풍속을 소수점 첫째 자리까지 반올림
        wsd = _safe_float(weather_data.get("WSD"))
        if wsd is not None:
            weather_data["WSD"] = round(wsd, 1)
            
        kor_now = self._get_sky_kor(weather_data.get("SKY"), weather_data.get("PTY"))
        weather_data.update({
            "current_condition_kor": kor_now, "current_condition": self.kor_to_condition(kor_now),
            "apparent_temp": self._calculate_apparent_temp(weather_data.get("TMP"), weather_data.get("REH"), weather_data.get("WSD")),
        })
        if weather_data.get("VEC"): weather_data["VEC_KOR"] = self._get_vec_kor(weather_data["VEC"])
        return {"weather": weather_data, "air": air_data or {}, "raw_forecast": forecast_map}

    def _translate_mid_condition_kor(self, wf: str) -> str:
        wf = str(wf or "맑음")
        if "비" in wf: return "비"
        if "눈" in wf: return "눈"
        if "구름많음" in wf: return "구름많음"
        if "흐림" in wf: return "흐림"
        return "맑음"

    def _get_sky_kor(self, sky, pty):
        p, s = str(pty or "0"), str(sky or "1")
        if p in ["1", "2", "3", "4"]: return {"1":"비","2":"비/눈","3":"눈","4":"소나기"}.get(p, "비")
        return "맑음" if s == "1" else ("구름많음" if s == "3" else "흐림")

    def _get_vec_kor(self, vec):
        v = _safe_float(vec)
        if v is None: return None
        if 22.5 <= v < 67.5: return "북동"
        elif 67.5 <= v < 112.5: return "동"
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
        e2 = 2*f - f**2
        lat0, lon0 = math.radians(38.0), math.radians(127.0)
        phi, lam = math.radians(lat), math.radians(lon)
        N = a / math.sqrt(1 - e2 * math.sin(phi)**2)
        T, C, A = math.tan(phi)**2, e2 / (1 - e2) * math.cos(phi)**2, math.cos(phi) * (lam - lon0)
        def M(p): return a * ((1 - e2/4 - 3*e2**2/64 - 5*e2**3/256) * p - (3*e2/8 + 3*e2**2/32 + 45*e2**3/1024) * math.sin(2*p) + (15*e2**2/256 + 45*e2**3/1024) * math.sin(4*p) - (35*e2**3/3072) * math.sin(6*p))
        return 200000.0 + 1.0 * N * (A + (1-T+C)*A**3/6 + (5-18*T+T**2+72*C-58*(e2/(1-e2)))*A**5/120), 500000.0 + 1.0 * (M(phi) - M(lat0) + N*math.tan(phi)*(A**2/2 + (5-T+9*C+4*C**2)*A**4/24 + (61-58*T+T**2+600*C-330*(e2/(1-e2)))*A**6/720))
