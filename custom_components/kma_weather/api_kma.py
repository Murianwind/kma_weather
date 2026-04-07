import logging
import asyncio
import aiohttp
import math
import json
from datetime import datetime, timedelta
from urllib.parse import unquote
from zoneinfo import ZoneInfo

_LOGGER = logging.getLogger(__name__)

class KMAWeatherAPI:
    def __init__(self, session, api_key, reg_id_temp, reg_id_land):
        self.session = session
        self.api_key = unquote(api_key)
        self.air_key = self.api_key
        self.reg_id_temp = reg_id_temp
        self.reg_id_land = reg_id_land
        self.tz = ZoneInfo("Asia/Seoul")
        self.lat = self.lon = self.nx = self.ny = None

    async def fetch_data(self, lat, lon, nx, ny):
        self.lat, self.lon, self.nx, self.ny = lat, lon, nx, ny
        now = datetime.now(self.tz)
        tasks = [self._get_short_term(now), self._get_mid_term(now), self._get_air_quality(), self._get_address(lat, lon)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        short_res, mid_res, air_data, address = [r if not isinstance(r, Exception) else None for r in results]
        return self._merge_all(now, short_res, mid_res, air_data, address)

    async def _get_air_quality(self):
        try:
            tm_x, tm_y = self._wgs84_to_tm(self.lat, self.lon)
            url_st = "https://apis.data.go.kr/B552584/MsrstnInfoInqireSvc/getNearbyMsrstnList"
            params_st = {"serviceKey": self.air_key, "returnType": "json", "tmX": f"{tm_x:.2f}", "tmY": f"{tm_y:.2f}"}
            async with self.session.get(url_st, params=params_st, timeout=10) as resp:
                st_json = json.loads(await resp.text())
            sn = st_json["response"]["body"]["items"][0]["stationName"]
            
            url_data = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty"
            params_data = {"serviceKey": self.air_key, "returnType": "json", "stationName": sn, "dataTerm": "daily", "ver": "1.3"}
            async with self.session.get(url_data, params=params_data, timeout=10) as resp:
                air_json = json.loads(await resp.text())
            ai = air_json["response"]["body"]["items"][0]
            return {
                "pm10Value": ai.get("pm10Value"), "pm10Grade": self._translate_grade(ai.get("pm10Grade")),
                "pm25Value": ai.get("pm25Value"), "pm25Grade": self._translate_grade(ai.get("pm25Grade")),
                "station": sn # 측정소 이름 보존
            }
        except: return {}

    def _translate_grade(self, g):
        return {"1": "좋음", "2": "보통", "3": "나쁨", "4": "매우나쁨"}.get(str(g), "정보없음")

    async def _get_address(self, lat, lon):
        try:
            url = "https://nominatim.openstreetmap.org/reverse"
            params = {"format": "json", "lat": lat, "lon": lon, "zoom": 16}
            async with self.session.get(url, params=params, headers={"User-Agent": "HA-KMA"}, timeout=5) as resp:
                d = await resp.json()
                a = d.get("address", {})
                return f"{a.get('city', a.get('province',''))} {a.get('borough', a.get('county',''))} {a.get('suburb', '')}".strip()
        except: return f"{lat:.4f}, {lon:.4f}"

    async def _get_short_term(self, now):
        adj = now - timedelta(minutes=10)
        base_d, base_h = adj.strftime("%Y%m%d"), max([h for h in [2, 5, 8, 11, 14, 17, 20, 23] if h <= adj.hour], default=None)
        if base_h is None: adj_p = adj - timedelta(days=1); base_d, base_h = adj_p.strftime("%Y%m%d"), 23
        url = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        params = {"serviceKey": self.api_key, "dataType": "JSON", "base_date": base_d, "base_time": f"{base_h:02d}00", "nx": self.nx, "ny": self.ny, "numOfRows": 1000}
        try:
            async with self.session.get(url, params=params, timeout=15) as r:
                return json.loads(await r.text())
        except: return None

    async def _get_mid_term(self, now):
        if 6 <= now.hour < 18: base = now.strftime("%Y%m%d") + "0600"
        else: base = (now if now.hour >= 18 else now - timedelta(days=1)).strftime("%Y%m%d") + "1800"
        async def fetch(u, p):
            try:
                async with self.session.get(u, params=p, timeout=15) as r:
                    return json.loads(await r.text())
            except: return None
        url_ta = "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa"
        url_land = "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst"
        return await asyncio.gather(
            fetch(url_ta, {"serviceKey": self.api_key, "dataType": "JSON", "regId": self.reg_id_temp, "tmFc": base}), 
            fetch(url_land, {"serviceKey": self.api_key, "dataType": "JSON", "regId": self.reg_id_land, "tmFc": base})
        )

    def _merge_all(self, now, short_res, mid_res, air_data, address=None):
        weather_data = {
            "TMP": None, "REH": None, "WSD": None, "VEC": None, "POP": None, "PTY": None, "SKY": None,
            "TMX_today": None, "TMN_today": None, "weather_am_today": None, "weather_pm_today": None,
            "rain_start_time": "강수없음", "address": address,
            "debug_lat": self.lat, "debug_lon": self.lon, "debug_nx": self.nx, "debug_ny": self.ny,
            "debug_reg_id_temp": self.reg_id_temp, "debug_reg_id_land": self.reg_id_land,
            "station": air_data.get("station") if air_data else None
        }
        
        if short_res and "response" in short_res:
            items = short_res.get("response", {}).get("body", {}).get("items", {}).get("item", [])
            forecast_map = {}
            for it in items:
                d, t, cat, val = it["fcstDate"], it["fcstTime"], it["category"], it["fcstValue"]
                forecast_map.setdefault(d, {}).setdefault(t, {})[cat] = val
            today_str = now.strftime("%Y%m%d")
            curr_h = f"{now.hour:02d}00"
            if today_str in forecast_map and curr_h in forecast_map[today_str]:
                weather_data.update(forecast_map[today_str][curr_h])
            if today_str in forecast_map:
                day = forecast_map[today_str]
                tmps = [float(day[t]["TMP"]) for t in day if "TMP" in day[t]]
                if tmps: weather_data["TMX_today"], weather_data["TMN_today"] = max(tmps), min(tmps)
                weather_data["weather_am_today"] = self._get_sky_kor(day.get("0900",{}).get("SKY"), day.get("0900",{}).get("PTY"))
                weather_data["weather_pm_today"] = self._get_sky_kor(day.get("1500",{}).get("SKY"), day.get("1500",{}).get("PTY"))

        weather_data["current_condition_kor"] = self._get_sky_kor(weather_data.get("SKY"), weather_data.get("PTY"))
        weather_data["current_condition"] = self._get_condition(weather_data.get("SKY"), weather_data.get("PTY"))
        
        return {"weather": weather_data, "air": air_data or {}}

    def _get_condition(self, s, p):
        p, s = str(p or "0"), str(s or "1")
        if p in ["1", "2", "4", "5", "6"]: return "rainy"
        if p in ["3", "7"]: return "snowy"
        return {"1": "sunny", "3": "partlycloudy", "4": "cloudy"}.get(s, "sunny")

    def _get_sky_kor(self, sky, pty):
        p, s = str(pty or "0"), str(sky or "1")
        if p in ["1", "2", "3", "4"]: return "비"
        return "맑음" if s == "1" else "흐림"

    def _wgs84_to_tm(self, lat, lon):
        a, f = 6378137.0, 1 / 298.257222101
        e2 = 2*f - f**2
        lat0, lon0 = math.radians(38.0), math.radians(127.0)
        phi, lam = math.radians(lat), math.radians(lon)
        N = a / math.sqrt(1 - e2 * math.sin(phi)**2)
        T, C, A = math.tan(phi)**2, e2 / (1 - e2) * math.cos(phi)**2, math.cos(phi) * (lam - lon0)
        def M(p): return a * ((1 - e2/4 - 3*e2**2/64 - 5*e2**3/256) * p - (3*e2/8 + 3*e2**2/32 + 45*e2**3/1024) * math.sin(2*p) + (15*e2**2/256 + 45*e2**3/1024) * math.sin(4*p) - (35*e2**3/3072) * math.sin(6*p))
        tm_x = 200000.0 + 1.0 * N * (A + (1-T+C)*A**3/6 + (5-18*T+T**2+72*C-58*(e2/(1-e2)))*A**5/120)
        tm_y = 500000.0 + 1.0 * (M(phi) - M(lat0) + N*math.tan(phi)*(A**2/2 + (5-T+9*C+4*C**2)*A**4/24 + (61-58*T+T**2+600*C-330*(e2/(1-e2)))*A**6/720))
        return tm_x, tm_y
