import logging
import asyncio
import aiohttp
import math
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

_LOGGER = logging.getLogger(__name__)

class KMAWeatherAPI:
    def __init__(self, session, api_key, reg_id_temp, reg_id_land):
        self.session = session
        # 공공데이터포털(data.go.kr) 인증키 하나로 통합 사용
        self.api_key = api_key
        self.air_key = api_key
        
        self.reg_id_temp = reg_id_temp
        self.reg_id_land = reg_id_land
        self.tz = ZoneInfo("Asia/Seoul")
        self.lat = self.lon = self.nx = self.ny = None

    def _wgs84_to_tm(self, lat, lon):
        """EPSG:5181 정밀 가우스-크뤼거 변환"""
        a, f = 6378137.0, 1 / 298.257222101
        e2 = 2*f - f**2
        lat0, lon0 = math.radians(38.0), math.radians(127.0)
        phi, lam = math.radians(lat), math.radians(lon)
        N = a / math.sqrt(1 - e2 * math.sin(phi)**2)
        T, C, A = math.tan(phi)**2, e2 / (1 - e2) * math.cos(phi)**2, math.cos(phi) * (lam - lon0)
        def M(p):
            return a * ((1 - e2/4 - 3*e2**2/64 - 5*e2**3/256) * p
                        - (3*e2/8 + 3*e2**2/32 + 45*e2**3/1024) * math.sin(2*p)
                        + (15*e2**2/256 + 45*e2**3/1024) * math.sin(4*p)
                        - (35*e2**3/3072) * math.sin(6*p))
        tm_x = 200000.0 + 1.0 * N * (
            A + (1-T+C)*A**3/6 + (5-18*T+T**2+72*C-58*(e2/(1-e2)))*A**5/120
        )
        tm_y = 500000.0 + 1.0 * (
            M(phi) - M(lat0) + N*math.tan(phi)*(
                A**2/2 + (5-T+9*C+4*C**2)*A**4/24 + (61-58*T+T**2+600*C-330*(e2/(1-e2)))*A**6/720
            )
        )
        return tm_x, tm_y

    async def fetch_data(self, lat, lon, nx, ny):
        self.lat, self.lon, self.nx, self.ny = lat, lon, nx, ny
        now = datetime.now(self.tz)
        tasks = [self._get_short_term(now), self._get_mid_term(now), self._get_air_quality(), self._get_address(lat, lon)]
        short_res, mid_res, air_data, address = await asyncio.gather(*tasks)
        
        if not short_res or "response" not in short_res:
            raise ValueError("기상청 단기예보 데이터를 불러오지 못했습니다. (네트워크 지연 또는 응답 오류)")

        return self._merge_all(now, short_res, mid_res, air_data, address)

    async def _get_air_quality(self):
        try:
            tm_x, tm_y = self._wgs84_to_tm(self.lat, self.lon)
            timeout = aiohttp.ClientTimeout(total=15)

            st_url = (
                f"http://apis.data.go.kr/B552584/MsrstnInfoInqireSvc/getNearbyMsrstnList"
                f"?serviceKey={self.air_key}&returnType=json&tmX={tm_x:.2f}&tmY={tm_y:.2f}"
            )
            async with self.session.get(st_url, timeout=timeout) as resp:
                if resp.status != 200: return {}
                st_json = await resp.json(content_type=None)

            items = st_json.get("response", {}).get("body", {}).get("items", [])
            if not items: return {}
            station_name = items[0]["stationName"]

            data_url = (
                f"http://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty"
                f"?serviceKey={self.air_key}&returnType=json&stationName={quote(station_name)}&dataTerm=daily&ver=1.3"
            )
            async with self.session.get(data_url, timeout=timeout) as resp:
                if resp.status != 200: return {}
                air_json = await resp.json(content_type=None)

            air_items = air_json.get("response", {}).get("body", {}).get("items", [])
            if not air_items: return {}
            item = air_items[0]
            
            return {
                "pm10Value": item.get("pm10Value"),
                "pm10Grade": self._translate_grade(item.get("pm10Grade")),
                "pm25Value": item.get("pm25Value"),
                "pm25Grade": self._translate_grade(item.get("pm25Grade")),
                "station": station_name,
            }
        except Exception as e:
            _LOGGER.warning("대기질 조회 실패: %s", e)
            return {}

    def _translate_grade(self, g):
        return {"1": "좋음", "2": "보통", "3": "나쁨", "4": "매우나쁨"}.get(str(g), "정보없음")

    async def _get_address(self, lat, lon):
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=16"
        headers = {"User-Agent": "HA-KMA", "Accept-Language": "ko-KR"}
        try:
            async with self.session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                d = await resp.json(content_type=None)
                a = d.get("address", {})
                parts = [a.get("province", a.get("city", "")), a.get("borough", a.get("county", "")), a.get("suburb", "")]
                return " ".join([i for i in parts if i]).strip()
        except Exception:
            return f"{lat:.4f}, {lon:.4f}"

    async def _get_short_term(self, now):
        adj = now - timedelta(minutes=10)
        base_d = adj.strftime("%Y%m%d")
        base_h = max([h for h in [2, 5, 8, 11, 14, 17, 20, 23] if h <= adj.hour], default=None)
        if base_h is None:
            adj_p = adj - timedelta(days=1)
            base_d, base_h = adj_p.strftime("%Y%m%d"), 23
        
        url = (
            f"http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
            f"?serviceKey={self.api_key}&pageNo=1&numOfRows=1500&dataType=JSON&base_date={base_d}&base_time={base_h:02d}00"
            f"&nx={self.nx}&ny={self.ny}"
        )
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200: return None
                return await r.json(content_type=None)
        except Exception:
            return None

    async def _get_mid_term(self, now):
        if now.hour < 7:
            base = (now - timedelta(days=1)).strftime("%Y%m%d") + "1800"
        elif now.hour < 19:
            base = now.strftime("%Y%m%d") + "0600"
        else:
            base = now.strftime("%Y%m%d") + "1800"

        async def fetch(u):
            try:
                async with self.session.get(u, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    return await r.json(content_type=None) if r.status == 200 else None
            except Exception:
                return None

        urls = [
            f"http://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa?serviceKey={self.api_key}&pageNo=1&numOfRows=10&dataType=JSON&regId={self.reg_id_temp}&tmFc={base}",
            f"http://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst?serviceKey={self.api_key}&pageNo=1&numOfRows=10&dataType=JSON&regId={self.reg_id_land}&tmFc={base}",
        ]
        return await asyncio.gather(*(fetch(u) for u in urls))

    def _merge_all(self, now, short_res, mid_res, air_data, address=None):
        weather_data = {"forecast_daily": [], "forecast_twice_daily": []}
        if address: weather_data["address"] = address
        forecast_map, rain_start, last_past = {}, "강수없음", None

        if short_res and "response" in short_res:
            try:
                items = short_res["response"]["body"]["items"]["item"]
                for it in items:
                    d, t, cat, val = it["fcstDate"], it["fcstTime"], it["category"], it["fcstValue"]
                    if d not in forecast_map: forecast_map[d] = {}
                    if t not in forecast_map[d]: forecast_map[d][t] = {}
                    forecast_map[d][t][cat] = val
            except (KeyError, TypeError):
                pass

            for d in sorted(forecast_map.keys()):
                for t in sorted(forecast_map[d].keys()):
                    f_dt = datetime.strptime(f"{d}{t}", "%Y%m%d%H%M").replace(tzinfo=self.tz)
                    if f_dt <= now:
                        last_past = forecast_map[d][t]
                    if rain_start == "강수없음" and forecast_map[d][t].get("PTY") in ["1", "2", "3", "4", "7"]:
                        if f_dt >= now:
                            rain_start = f"{t[:2]}:{t[2:]}" if d == now.strftime("%Y%m%d") else f"{int(d[6:8])}일 {t[:2]}:{t[2:]}"

            if not last_past and forecast_map:
                first_d = sorted(forecast_map.keys())[0]
                first_t = sorted(forecast_map[first_d].keys())[0]
                last_past = forecast_map[first_d][first_t]

            if last_past:
                for cat, val in last_past.items():
                    weather_data[cat] = val
                if "VEC" in last_past:
                    weather_data["VEC_KOR"] = self._get_vec_kor(last_past["VEC"])

        v_days = [d for d in sorted(forecast_map.keys()) if d >= now.strftime("%Y%m%d")]
        for d_str in v_days[:3]:
            day_items = forecast_map[d_str]
            tmps = [float(v["TMP"]) for v in day_items.values() if "TMP" in v]
            t_max = max(tmps) if tmps else 20.0
            t_min = min(tmps) if tmps else 10.0
            base_dt = datetime.strptime(d_str, "%Y%m%d").replace(tzinfo=self.tz)
            rep = day_items.get("1200") or day_items.get("1500") or day_items.get("1800") or next(iter(day_items.values()), {})
            
            # ★ 일일 예보: 최고/최저 정상 작동 유지
            weather_data["forecast_daily"].append({
                "datetime": base_dt.isoformat(),
                "native_temperature": t_max,
                "native_templow": t_min,
                "condition": self._get_condition(rep.get("SKY"), rep.get("PTY")),
            })
            
            # ★ 단기예보(매일 2회): 주간(is_day)에는 최저기온(t_min), 야간(not is_day)에는 최고기온(t_max)
            for h, is_day in [(9, True), (21, False)]:
                t_k = f"{h:02d}00"
                if t_k in day_items:
                    weather_data["forecast_twice_daily"].append({
                        "datetime": base_dt.replace(hour=h).isoformat(),
                        "is_daytime": is_day,
                        "native_temperature": t_min if is_day else t_max,
                        "condition": self._get_condition(day_items[t_k].get("SKY"), day_items[t_k].get("PTY")),
                    })

        mid_t, mid_l = mid_res if mid_res else (None, None)
        if mid_t and mid_l:
            try:
                mt = mid_t["response"]["body"]["items"]["item"][0]
                ml = mid_l["response"]["body"]["items"]["item"][0]
                for i in range(3, 11):
                    target_dt = (now + timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
                    t_min = float(mt.get(f"taMin{i}", 15.0))
                    t_max = float(mt.get(f"taMax{i}", 25.0))
                    
                    # ★ 일일 예보: 최고/최저 정상 작동 유지
                    weather_data["forecast_daily"].append({
                        "datetime": target_dt.isoformat(),
                        "native_temperature": t_max,
                        "native_templow": t_min,
                        "condition": self._get_mid_condition(ml.get(f"wf{i}")),
                    })
                    
                    am_wf = ml.get(f"wf{i}Am") if i <= 7 else ml.get(f"wf{i}")
                    pm_wf = ml.get(f"wf{i}Pm") if i <= 7 else ml.get(f"wf{i}")
                    
                    # ★ 중기예보(매일 2회): 주간에는 최저기온(t_min), 야간에는 최고기온(t_max)
                    weather_data["forecast_twice_daily"].append({
                        "datetime": target_dt.replace(hour=9).isoformat(), 
                        "is_daytime": True,
                        "native_temperature": t_min, 
                        "condition": self._get_mid_condition(am_wf),
                    })
                    weather_data["forecast_twice_daily"].append({
                        "datetime": target_dt.replace(hour=21).isoformat(), 
                        "is_daytime": False,
                        "native_temperature": t_max, 
                        "condition": self._get_mid_condition(pm_wf),
                    })
            except Exception as e:
                _LOGGER.warning("중기예보 파싱 에러: %s", e)

        weather_data["rain_start_time"] = rain_start
        weather_data["current_condition_kor"] = self._get_sky_kor(weather_data.get("SKY"), weather_data.get("PTY"))
        weather_data["current_condition"] = self._get_condition(weather_data.get("SKY"), weather_data.get("PTY"))

        today_str = now.strftime("%Y%m%d")
        tom_str = (now + timedelta(days=1)).strftime("%Y%m%d")

        for d_str, prefix in [(today_str, "today"), (tom_str, "tomorrow")]:
            if d_str in forecast_map:
                day = forecast_map[d_str]
                tmx = next((day[t]["TMX"] for t in day if "TMX" in day[t]), None)
                tmn = next((day[t]["TMN"] for t in day if "TMN" in day[t]), None)
                all_tmps = [float(day[t]["TMP"]) for t in day if "TMP" in day[t]]
                weather_data[f"TMX_{prefix}"] = int(float(tmx)) if tmx is not None else (int(max(all_tmps)) if all_tmps else None)
                weather_data[f"TMN_{prefix}"] = int(float(tmn)) if tmn is not None else (int(min(all_tmps)) if all_tmps else None)
                am = day.get("0900", {})
                pm = day.get("1500", {})
                weather_data[f"weather_am_{prefix}"] = self._get_sky_kor(am.get("SKY"), am.get("PTY"))
                weather_data[f"weather_pm_{prefix}"] = self._get_sky_kor(pm.get("SKY"), pm.get("PTY"))

        apparent = self._calculate_apparent_temp(weather_data.get("TMP"), weather_data.get("REH"), weather_data.get("WSD"))
        weather_data["apparent_temp"] = int(float(apparent)) if apparent is not None else None

        return {"weather": weather_data, "air": air_data or {}}

    def _get_condition(self, s, p):
        p, s = str(p or "0"), str(s or "1")
        if p in ["1", "2", "4", "5", "6"]: return "rainy"
        if p in ["3", "7"]: return "snowy"
        return {"1": "sunny", "3": "partlycloudy", "4": "cloudy"}.get(s, "sunny")

    def _get_mid_condition(self, wf):
        if not wf: return "sunny"
        if any(x in wf for x in ["비", "소나기"]): return "rainy"
        if "눈" in wf: return "snowy"
        if "구름많음" in wf: return "partlycloudy"
        return "cloudy" if "흐림" in wf else "sunny"

    def _get_sky_kor(self, sky, pty):
        p, s = str(pty or "0"), str(sky or "1")
        if p == "1": return "비"
        if p == "2": return "비/눈"
        if p == "3": return "눈"
        if p == "4": return "소나기"
        if s == "1": return "맑음"
        if s == "3": return "구름많음"
        return "흐림"

    def _get_vec_kor(self, vec):
        v = float(vec or 0)
        if 22.5 <= v < 67.5: return "북동"
        if 67.5 <= v < 112.5: return "동"
        if 112.5 <= v < 157.5: return "남동"
        if 157.5 <= v < 202.5: return "남"
        if 202.5 <= v < 247.5: return "남서"
        if 247.5 <= v < 292.5: return "서"
        if 292.5 <= v < 337.5: return "북서"
        return "북"

    def _calculate_apparent_temp(self, temp, reh, wsd):
        try:
            t, rh = float(temp), float(reh)
            v = float(wsd) * 3.6
            if t <= 10 and v >= 4.68:  
                return 13.12 + 0.6215 * t - 11.37 * (v ** 0.16) + 0.3965 * t * (v ** 0.16)
            if t >= 18:
                tw = (t * math.atan(0.151977 * (rh + 8.313595) ** 0.5) + math.atan(t + rh) - math.atan(rh - 1.676331) + 0.00391838 * (rh ** 1.5) * math.atan(0.023101 * rh) - 4.686035)
                return -0.25 + 1.04 * tw + 0.65
            return t
        except Exception:
            return temp
