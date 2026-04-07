import logging
import asyncio
import aiohttp
import math
import json
from datetime import datetime, timedelta
from urllib.parse import unquote
from zoneinfo import ZoneInfo

_LOGGER = logging.getLogger(__name__)

def _safe_float(v):
    try:
        if v == "" or v is None: return None
        if isinstance(v, str) and not v.strip(): return None
        return float(v)
    except (TypeError, ValueError):
        return None

class KMAWeatherAPI:
    def __init__(self, session, api_key, reg_id_temp, reg_id_land):
        self.session = session
        # 인코딩 키 인식 문제 해결
        self.api_key = unquote(api_key)
        self.air_key = self.api_key
        self.reg_id_temp = reg_id_temp
        self.reg_id_land = reg_id_land
        self.tz = ZoneInfo("Asia/Seoul")
        self.lat = self.lon = self.nx = self.ny = None

    def _wgs84_to_tm(self, lat, lon):
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
        tm_x = 200000.0 + 1.0 * N * (A + (1-T+C)*A**3/6 + (5-18*T+T**2+72*C-58*(e2/(1-e2)))*A**5/120)
        tm_y = 500000.0 + 1.0 * (M(phi) - M(lat0) + N*math.tan(phi)*(A**2/2 + (5-T+9*C+4*C**2)*A**4/24 + (61-58*T+T**2+600*C-330*(e2/(1-e2)))*A**6/720))
        return tm_x, tm_y

    async def fetch_data(self, lat, lon, nx, ny):
        self.lat, self.lon, self.nx, self.ny = lat, lon, nx, ny
        now = datetime.now(self.tz)
        tasks = [self._get_short_term(now), self._get_mid_term(now), self._get_air_quality(), self._get_address(lat, lon)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        short_res, mid_res, air_data, address = [r if not isinstance(r, Exception) else None for r in results]
        
        # 4. 센서 유실 방지: 최소한의 단기예보만 있어도 진행
        if not short_res:
            _LOGGER.warning("기상청 단기예보 응답 부재")
            return None

        return self._merge_all(now, short_res, mid_res, air_data, address)

    async def _get_air_quality(self):
        try:
            tm_x, tm_y = self._wgs84_to_tm(self.lat, self.lon)
            url_st = "https://apis.data.go.kr/B552584/MsrstnInfoInqireSvc/getNearbyMsrstnList"
            params_st = {"serviceKey": self.air_key, "returnType": "json", "tmX": f"{tm_x:.2f}", "tmY": f"{tm_y:.2f}"}
            async with self.session.get(url_st, params=params_st, timeout=10) as resp:
                st_json = json.loads(await resp.text())
            items = st_json.get("response", {}).get("body", {}).get("items", [])
            if not items: return {}
            sn = items[0]["stationName"]
            url_data = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty"
            params_data = {"serviceKey": self.air_key, "returnType": "json", "stationName": sn, "dataTerm": "daily", "ver": "1.3"}
            async with self.session.get(url_data, params=params_data, timeout=10) as resp:
                air_json = json.loads(await resp.text())
            ai = air_json.get("response", {}).get("body", {}).get("items", [])
            if not ai: return {}
            return {"pm10Value": ai[0].get("pm10Value"), "pm10Grade": self._translate_grade(ai[0].get("pm10Grade")), "pm25Value": ai[0].get("pm25Value"), "pm25Grade": self._translate_grade(ai[0].get("pm25Grade")), "station": sn}
        except Exception: return {}

    def _translate_grade(self, g):
        return {"1": "좋음", "2": "보통", "3": "나쁨", "4": "매우나쁨"}.get(str(g), "정보없음")

    async def _get_address(self, lat, lon):
        try:
            url = "https://nominatim.openstreetmap.org/reverse"
            params = {"format": "json", "lat": lat, "lon": lon, "zoom": 16}
            async with self.session.get(url, params=params, headers={"User-Agent": "HA-KMA"}, timeout=5) as resp:
                d = await resp.json()
                a = d.get("address", {})
                return f"{a.get('city', '')} {a.get('borough', '')} {a.get('suburb', '')}".strip()
        except Exception: return f"{lat:.4f}, {lon:.4f}"

    async def _get_short_term(self, now):
        adj = now - timedelta(minutes=10)
        base_d, base_h = adj.strftime("%Y%m%d"), max([h for h in [2, 5, 8, 11, 14, 17, 20, 23] if h <= adj.hour], default=None)
        if base_h is None: adj_p = adj - timedelta(days=1); base_d, base_h = adj_p.strftime("%Y%m%d"), 23
        url = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        params = {"serviceKey": self.api_key, "dataType": "JSON", "base_date": base_d, "base_time": f"{base_h:02d}00", "nx": self.nx, "ny": self.ny, "numOfRows": 1000}
        try:
            async with self.session.get(url, params=params, timeout=15) as r:
                return json.loads(await r.text())
        except Exception: return None

    async def _get_mid_term(self, now):
        if 6 <= now.hour < 18: base = now.strftime("%Y%m%d") + "0600"
        elif now.hour < 6: base = (now - timedelta(days=1)).strftime("%Y%m%d") + "1800"
        else: base = now.strftime("%Y%m%d") + "1800"
        async def fetch(u, p):
            try:
                async with self.session.get(u, params=p, timeout=15) as r:
                    return json.loads(await r.text())
            except Exception: return None
        url_ta = "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa"
        url_land = "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst"
        return await asyncio.gather(fetch(url_ta, {"serviceKey": self.api_key, "dataType": "JSON", "regId": self.reg_id_temp, "tmFc": base}), fetch(url_land, {"serviceKey": self.api_key, "dataType": "JSON", "regId": self.reg_id_land, "tmFc": base}))

    def _merge_all(self, now, short_res, mid_res, air_data, address=None):
        weather_data = {"forecast_daily": [], "forecast_twice_daily": []}
        if address: weather_data["address"] = address
        forecast_map, rain_start, last_past = {}, "강수없음", None
        
        # 데이터 유무와 관계없이 기본 구조 유지 (4. 센서 사라짐 방지)
        if not short_res or "response" not in short_res:
            return {"weather": weather_data, "air": air_data or {}}

        items = short_res.get("response", {}).get("body", {}).get("items", {}).get("item", [])
        for it in items:
            d, t, cat, val = it["fcstDate"], it["fcstTime"], it["category"], it["fcstValue"]
            forecast_map.setdefault(d, {}).setdefault(t, {})[cat] = val

        for d in sorted(forecast_map.keys()):
            for t in sorted(forecast_map[d].keys()):
                f_dt = datetime.strptime(f"{d}{t}", "%Y%m%d%H%M").replace(tzinfo=self.tz)
                if f_dt <= now: last_past = forecast_map[d][t]
                pty = str(forecast_map[d][t].get("PTY") or "0")
                if rain_start == "강수없음" and pty in ["1", "2", "3", "4", "7"]:
                    if f_dt >= now:
                        rain_start = f_dt.strftime("%m월 %d일 %H시 %M분")

        if last_past: weather_data.update(last_past)
        
        # 단기예보 3일
        v_days = [d for d in sorted(forecast_map.keys()) if d >= now.strftime("%Y%m%d")]
        for d_str in v_days[:3]:
            base_dt = datetime.strptime(d_str, "%Y%m%d").replace(hour=12, tzinfo=self.tz)
            day_items = forecast_map[d_str]
            tmps = [float(v["TMP"]) for v in day_items.values() if "TMP" in v]
            t_max, t_min = (max(tmps) if tmps else 20.0), (min(tmps) if tmps else 10.0)
            rep = day_items.get("1200") or day_items.get("1500") or next(iter(day_items.values()), {})
            weather_data["forecast_daily"].append({"datetime": base_dt.isoformat(), "native_temperature": t_max, "native_templow": t_min, "condition": self._get_condition(rep.get("SKY"), rep.get("PTY"))})

        # 중기예보 8일
        mid_t, mid_l = mid_res if mid_res else (None, None)
        if isinstance(mid_t, dict) and isinstance(mid_l, dict):
            try:
                mt, ml = mid_t["response"]["body"]["items"]["item"][0], mid_l["response"]["body"]["items"]["item"][0]
                for i in range(3, 11):
                    target_dt = (now + timedelta(days=i)).replace(hour=12, minute=0, second=0)
                    weather_data["forecast_daily"].append({"datetime": target_dt.isoformat(), "native_temperature": float(mt.get(f"taMax{i}", 25)), "native_templow": float(mt.get(f"taMin{i}", 15)), "condition": self._get_mid_condition(ml.get(f"wf{i}"))})
            except Exception: pass

        today_str = now.strftime("%Y%m%d")
        if today_str in forecast_map:
            day = forecast_map[today_str]
            all_tmps = [float(day[t]["TMP"]) for t in day if "TMP" in day[t]]
            weather_data["TMX_today"] = max(all_tmps) if all_tmps else 20
            weather_data["TMN_today"] = min(all_tmps) if all_tmps else 10
            weather_data["weather_am_today"] = self._get_sky_kor(day.get("0900", {}).get("SKY"), day.get("0900", {}).get("PTY"))
            weather_data["weather_pm_today"] = self._get_sky_kor(day.get("1500", {}).get("SKY"), day.get("1500", {}).get("PTY"))

        weather_data["rain_start_time"] = rain_start
        weather_data["current_condition_kor"] = self._get_sky_kor(weather_data.get("SKY"), weather_data.get("PTY"))
        weather_data["current_condition"] = self._get_condition(weather_data.get("SKY"), weather_data.get("PTY"))
        weather_data["apparent_temp"] = self._calculate_apparent_temp(weather_data.get("TMP"), weather_data.get("REH"), weather_data.get("WSD"))
        
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
        return "partlycloudy" if "구름많음" in wf else "sunny"

    def _get_sky_kor(self, sky, pty):
        p, s = str(pty or "0"), str(sky or "1")
        if p in ["1", "2", "3", "4"]: return "비"
        return "맑음" if s == "1" else "흐림"

    def _calculate_apparent_temp(self, temp, reh, wsd):
        try:
            t, rh, v = float(temp), float(reh), float(wsd) * 3.6
            if t <= 10 and v >= 4.68: return 13.12 + 0.6215*t - 11.37*(v**0.16) + 0.3965*t*(v**0.16)
            return t
        except: return temp
