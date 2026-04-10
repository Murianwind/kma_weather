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
        if v == "" or v is None or v == "-":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# ── 한글 → HA 표준 영문 매핑 ──────────────────────────────────
KOR_TO_CONDITION: dict[str, str] = {
    "맑음":    "sunny",
    "구름많음": "partlycloudy",
    "흐림":    "cloudy",
    "비":      "rainy",
    "비/눈":   "rainy",
    "소나기":  "rainy",
    "눈":      "snowy",
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
        self._cached_station = None
        self._cached_lat_lon = None
        self._station_cache_time = None
        self._nominatim_user_agent = self._build_nominatim_user_agent()

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

    async def _fetch(self, url, params, headers=None, timeout=15):
        try:
            async with self.session.get(
                url, params=params, headers=headers, timeout=timeout
            ) as response:
                response.raise_for_status()
                return await response.json(content_type=None)
        except Exception as err:
            _LOGGER.error("API 호출 오류 (%s): %s", url, err)
        return None

    async def fetch_data(self, lat, lon, nx, ny):
        self.lat, self.lon, self.nx, self.ny = lat, lon, nx, ny
        now = datetime.now(self.tz)
        tasks = [
            self._get_short_term(now),
            self._get_mid_term(now),
            self._get_air_quality(),
            self._get_address(lat, lon),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        short_res, mid_res, air_data, address = [
            r if not isinstance(r, Exception) else None for r in results
        ]
        return self._merge_all(now, short_res, mid_res, air_data, address)

    async def _get_address(self, lat, lon):
        try:
            url = "https://nominatim.openstreetmap.org/reverse"
            params = {"format": "json", "lat": lat, "lon": lon, "zoom": 16}
            headers = {"User-Agent": self._nominatim_user_agent, "Accept-Language": "ko"}
            d = await self._fetch(url, params=params, headers=headers, timeout=5)
            if d:
                a = d.get("address", {})
                parts = [a.get("city", a.get("province", "")), a.get("borough", a.get("county", "")), a.get("suburb", a.get("village", ""))]
                return " ".join([p for p in parts if p]).strip()
            return f"{lat:.4f}, {lon:.4f}"
        except Exception:
            return f"{lat:.4f}, {lon:.4f}"

    async def _get_air_quality(self):
        """에어코리아 미세먼지 데이터 수집"""
        try:
            now = datetime.now(self.tz)
            sn = self._cached_station
            if not sn:
                tm_x, tm_y = self._wgs84_to_tm(self.lat, self.lon)
                st_json = await self._fetch("https://apis.data.go.kr/B552584/MsrstnInfoInqireSvc/getNearbyMsrstnList", 
                                            {"serviceKey": self.api_key, "returnType": "json", "tmX": f"{tm_x:.2f}", "tmY": f"{tm_y:.2f}"})
                items = st_json.get("response", {}).get("body", {}).get("items", []) if st_json else []
                if items:
                    sn = items[0].get("stationName")
                    self._cached_station = sn

            air_json = await self._fetch("https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty",
                                         {"serviceKey": self.api_key, "returnType": "json", "stationName": sn, "dataTerm": "daily", "ver": "1.3"})
            
            ai_list = air_json.get("response", {}).get("body", {}).get("items", []) if air_json else []
            if not ai_list:
                return {"station": sn}

            ai = ai_list[0]
            # Problem 2 해결: pm25Grade가 비어있을 경우 pm25Grade1h 등 다른 필드 참조 시도
            p10_g = ai.get("pm10Grade") or ai.get("pm10Grade1h")
            p25_g = ai.get("pm25Grade") or ai.get("pm25Grade1h")

            return {
                "pm10Value": ai.get("pm10Value"),
                "pm10Grade": self._translate_grade(p10_g),
                "pm25Value": ai.get("pm25Value"),
                "pm25Grade": self._translate_grade(p25_g),
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
        base_hours = [2, 5, 8, 11, 14, 17, 20, 23]
        valid_hours = [h for h in base_hours if h <= hour]
        base_h = max(valid_hours) if valid_hours else 23
        base_d = adj.strftime("%Y%m%d") if valid_hours else (adj - timedelta(days=1)).strftime("%Y%m%d")
        
        url = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        params = {"serviceKey": self.api_key, "dataType": "JSON", "base_date": base_d, "base_time": f"{base_h:02d}00", "nx": self.nx, "ny": self.ny, "numOfRows": 1500}
        return await self._fetch(url, params=params)

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
        if t <= 10 and v_kmh >= 4.8:
            return round(13.12 + 0.6215 * t - 11.37 * (v_kmh ** 0.16) + 0.3965 * t * (v_kmh ** 0.16), 1)
        if t >= 25 and rh is not None and rh >= 40:
            return round(0.5 * (t + 61.0 + ((t - 68.0) * 1.2) + (rh * 0.094)), 1)
        return t

    @staticmethod
    def kor_to_condition(kor: str | None) -> str | None:
        if kor is None: return None
        return KOR_TO_CONDITION.get(kor)

    def _merge_all(self, now, short_res, mid_res, air_data, address=None):
        weather_data = {
            "TMP": None, "REH": None, "WSD": None, "VEC": None, "POP": None,
            "TMX_today": None, "TMN_today": None, "TMX_tomorrow": None, "TMN_tomorrow": None,
            "forecast_daily": [], "forecast_twice_daily": [], "address": address,
        }

        forecast_map = {}
        if short_res and "response" in short_res:
            items = short_res.get("response", {}).get("body", {}).get("items", {}).get("item", [])
            for it in items:
                forecast_map.setdefault(it["fcstDate"], {}).setdefault(it["fcstTime"], {})[it["category"]] = it["fcstValue"]

            today_str = now.strftime("%Y%m%d")
            # 현재 시각 데이터 반영
            curr_h = f"{now.hour:02d}00"
            if today_str in forecast_map:
                times = sorted(forecast_map[today_str].keys())
                best_t = next((t for t in times if t >= curr_h), times[-1] if times else None)
                if best_t: weather_data.update(forecast_map[today_str][best_t])

        mid_ta = mid_res[0].get("response",{}).get("body",{}).get("items",{}).get("item",[{}])[0] if mid_res and mid_res[0] else {}
        mid_land = mid_res[1].get("response",{}).get("body",{}).get("items",{}).get("item",[{}])[0] if mid_res and mid_res[1] else {}

        # Problem 1 해결: 10일치 forecast_daily 및 forecast_twice_daily 생성
        twice_daily = []
        daily_forecast = []

        for i in range(10):
            target_date = now + timedelta(days=i)
            d_str = target_date.strftime("%Y%m%d")
            
            # 하루의 최고/최저 기온 수집용
            tmps = [_safe_float(v.get("TMP")) for v in forecast_map.get(d_str, {}).values() if "TMP" in v]
            t_max = max(tmps) if tmps else _safe_float(mid_ta.get(f"taMax{i}"))
            t_min = min(tmps) if tmps else _safe_float(mid_ta.get(f"taMin{i}"))

            # 오전/오후 데이터 처리 (twice_daily)
            for is_am in [True, False]:
                hour = 9 if is_am else 21
                dt_iso = target_date.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()
                short_hour_data = forecast_map.get(d_str, {}).get(f"{hour:02d}00", {})
                
                if short_hour_data or i >= 2:
                    wf = self._get_sky_kor(short_hour_data.get("SKY"), short_hour_data.get("PTY")) if short_hour_data else self._translate_mid_condition_kor(mid_land.get(f"wf{i}Am" if is_am else f"wf{i}Pm") or mid_land.get(f"wf{i}"))
                    twice_daily.append({
                        "datetime": dt_iso, "is_daytime": is_am,
                        "native_temperature": t_max, "native_templow": t_min,
                        "condition": self.kor_to_condition(wf),
                    })

            # 일일 예보 (forecast_daily) 생성 - 카드 매일 영역용
            if t_max is not None:
                wf_daily = self._get_sky_kor(forecast_map.get(d_str, {}).get("1200", {}).get("SKY"), forecast_map.get(d_str, {}).get("1200", {}).get("PTY")) if i < 2 else self._translate_mid_condition_kor(mid_land.get(f"wf{i}") or mid_land.get(f"wf{i}Pm"))
                daily_forecast.append({
                    "datetime": target_date.replace(hour=12).isoformat(),
                    "native_temperature": t_max,
                    "native_templow": t_min,
                    "condition": self.kor_to_condition(wf_daily),
                })

        weather_data["forecast_twice_daily"] = twice_daily
        weather_data["forecast_daily"] = daily_forecast
        
        kor_now = self._get_sky_kor(weather_data.get("SKY"), weather_data.get("PTY"))
        weather_data["current_condition_kor"] = kor_now
        weather_data["current_condition"] = self.kor_to_condition(kor_now)
        weather_data["apparent_temp"] = self._calculate_apparent_temp(weather_data.get("TMP"), weather_data.get("REH"), weather_data.get("WSD"))
        
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
        if 67.5 <= v < 112.5: return "동"
        if 112.5 <= v < 157.5: return "남동"
        if 157.5 <= v < 202.5: return "남"
        if 202.5 <= v < 247.5: return "남서"
        if 247.5 <= v < 292.5: return "서"
        if 292.5 <= v < 337.5: return "북서"
        return "북"

    def _wgs84_to_tm(self, lat, lon):
        a, f = 6378137.0, 1 / 298.257222101
        e2 = 2*f - f**2
        lat0, lon0 = math.radians(38.0), math.radians(127.0)
        phi, lam = math.radians(lat), math.radians(lon)
        N = a / math.sqrt(1 - e2 * math.sin(phi)**2)
        T, C, A = math.tan(phi)**2, e2 / (1 - e2) * math.cos(phi)**2, math.cos(phi) * (lam - lon0)
        def M(p): return a * ((1 - e2/4 - 3*e2**2/64 - 5*e2**3/256) * p - (3*e2/8 + 3*e2**2/32 + 45*e2**3/1024) * math.sin(2*p) + (15*e2**2/256 + 45*e2**3/1024) * math.sin(4*p) - (35*e2**3/3072) * math.sin(6*p))
        return 200000.0 + 1.0 * N * (A + (1-T+C)*A**3/6 + (5-18*T+T**2+72*C-58*(e2/(1-e2)))*A**5/120), 500000.0 + 1.0 * (M(phi) - M(lat0) + N*math.tan(phi)*(A**2/2 + (5-T+9*C+4*C**2)*A**4/24 + (61-58*T+T**2+600*C-330*(e2/(1-e2)))*A**6/720))
