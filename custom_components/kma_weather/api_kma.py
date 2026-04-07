import logging
import asyncio
import aiohttp
import math
import json
from datetime import datetime, timedelta
from urllib.parse import quote
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
        self.api_key = api_key
        self.air_key = api_key
        self.reg_id_temp = reg_id_temp
        self.reg_id_land = reg_id_land
        # Python 3.14 호환 표준 방식 (Blocking Call 방지)
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
        
        if not short_res or not isinstance(short_res, dict) or "response" not in short_res:
            _LOGGER.warning("기상청 단기예보 데이터 수집 실패 (네트워크 연결 혹은 API 키 상태를 확인하세요)")
            return None

        return self._merge_all(now, short_res, mid_res, air_data, address)

    async def _get_air_quality(self):
        try:
            tm_x, tm_y = self._wgs84_to_tm(self.lat, self.lon)
            # 공공데이터포털은 다시 http로 원복 (안정성 우선)
            url_st = "http://apis.data.go.kr/B552584/MsrstnInfoInqireSvc/getNearbyMsrstnList"
            params_st = {"serviceKey": self.air_key, "returnType": "json", "tmX": f"{tm_x:.2f}", "tmY": f"{tm_y:.2f}"}
            
            async with self.session.get(url_st, params=params_st, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp_text = await resp.text()
                try:
                    st_json = json.loads(resp_text)
                except json.JSONDecodeError:
                    _LOGGER.debug("에어코리아(측정소) 비정상 응답: %s", resp_text[:100])
                    return {}
            
            items = st_json.get("response", {}).get("body", {}).get("items", [])
            if not items: return {}
            sn = items[0]["stationName"]
            
            url_data = "http://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty"
            params_data = {"serviceKey": self.air_key, "returnType": "json", "stationName": sn, "dataTerm": "daily", "ver": "1.3"}
            
            async with self.session.get(url_data, params=params_data, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp_text = await resp.text()
                try:
                    air_json = json.loads(resp_text)
                except json.JSONDecodeError:
                    return {}
            
            ai = air_json.get("response", {}).get("body", {}).get("items", [])
            if not ai: return {}
            return {"pm10Value": ai[0].get("pm10Value"), "pm10Grade": self._translate_grade(ai[0].get("pm10Grade")), "pm25Value": ai[0].get("pm25Value"), "pm25Grade": self._translate_grade(ai[0].get("pm25Grade")), "station": sn}
        except Exception as e:
            _LOGGER.warning("대기질 정보 조회 중 예외 발생: %s", e)
            return {}

    def _translate_grade(self, g):
        return {"1": "좋음", "2": "보통", "3": "나쁨", "4": "매우나쁨"}.get(str(g), "정보없음")

    async def _get_address(self, lat, lon):
        try:
            # Nominatim은 https 필수
            url = "https://nominatim.openstreetmap.org/reverse"
            params = {"format": "json", "lat": lat, "lon": lon, "zoom": 16}
            async with self.session.get(url, params=params, headers={"User-Agent": "HA-KMA"}, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    d = await resp.json()
                    a = d.get("address", {})
                    parts = [a.get("province", a.get("city", "")), a.get("borough", a.get("county", "")), a.get("suburb", "")]
                    return " ".join([i for i in parts if i]).strip()
                return f"{lat:.4f}, {lon:.4f}"
        except Exception: return f"{lat:.4f}, {lon:.4f}"

    async def _get_short_term(self, now):
        adj = now - timedelta(minutes=10)
        base_d, base_h = adj.strftime("%Y%m%d"), max([h for h in [2, 5, 8, 11, 14, 17, 20, 23] if h <= adj.hour], default=None)
        if base_h is None: 
            adj_p = adj - timedelta(days=1); base_d, base_h = adj_p.strftime("%Y%m%d"), 23
        
        # 기상청 주소 http로 원복
        url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        params = {"serviceKey": self.api_key, "dataType": "JSON", "base_date": base_d, "base_time": f"{base_h:02d}00", "nx": self.nx, "ny": self.ny, "numOfRows": 1000}
        try:
            async with self.session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                resp_text = await r.text()
                if r.status != 200: return None
                try:
                    return json.loads(resp_text)
                except json.JSONDecodeError:
                    _LOGGER.debug("기상청 단기예보 비정상 응답: %s", resp_text[:100])
                    return None
        except Exception as e:
            _LOGGER.warning("단기예보 API 호출 실패: %s", e)
            return None

    async def _get_mid_term(self, now):
        if 6 <= now.hour < 18: base = now.strftime("%Y%m%d") + "0600"
        elif now.hour < 6: base = (now - timedelta(days=1)).strftime("%Y%m%d") + "1800"
        else: base = now.strftime("%Y%m%d") + "1800"

        async def fetch(url_base, p):
            try:
                async with self.session.get(url_base, params=p, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    resp_text = await r.text()
                    try:
                        return json.loads(resp_text) if r.status == 200 else None
                    except json.JSONDecodeError:
                        return None
            except Exception: return None
        
        # 중기예보 주소 http로 원복
        url_ta = "http://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa"
        url_land = "http://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst"
        params_ta = {"serviceKey": self.api_key, "dataType": "JSON", "regId": self.reg_id_temp, "tmFc": base}
        params_land = {"serviceKey": self.api_key, "dataType": "JSON", "regId": self.reg_id_land, "tmFc": base}
        
        return await asyncio.gather(fetch(url_ta, params_ta), fetch(url_land, params_land))

    def _merge_all(self, now, short_res, mid_res, air_data, address=None):
        weather_data = {"forecast_daily": [], "forecast_twice_daily": []}
        if address: weather_data["address"] = address
        forecast_map, rain_start, last_past = {}, "강수없음", None
        weekday_ko = ["월", "화", "수", "목", "금", "토", "일"]

        items = short_res.get("response", {}).get("body", {}).get("items", {}).get("item", [])
        if not items: return {"weather": weather_data, "air": air_data or {}}

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
                        h_str = f"{f_dt.hour}시" if f_dt.minute == 0 else f"{f_dt.hour}시 {f_dt.minute}분"
                        rain_start = f"{f_dt.month}월 {f_dt.day}일 {weekday_ko[f_dt.weekday()]}요일 {h_str}"

        if last_past:
            weather_data.update(last_past)
            if "VEC" in last_past: weather_data["VEC_KOR"] = self._get_vec_kor(last_past["VEC"])

        v_days = [d for d in sorted(forecast_map.keys()) if d >= now.strftime("%Y%m%d")]
        for d_str in v_days[:3]:
            base_dt = datetime.strptime(d_str, "%Y%m%d").replace(hour=12, tzinfo=self.tz)
            day_items = forecast_map[d_str]
            tmps = [float(v["TMP"]) for v in day_items.values() if "TMP" in v]
            t_max, t_min = (max(tmps) if tmps else 20.0), (min(tmps) if tmps else 10.0)
            rep = day_items.get("1200") or day_items.get("1500") or next(iter(day_items.values()), {})
            weather_data["forecast_daily"].append({"datetime": base_dt.isoformat(), "native_temperature": t_max, "native_templow": t_min, "condition": self._get_condition(rep.get("SKY"), rep.get("PTY"))})
            for h, is_day in [(9, True), (21, False)]:
                t_k = f"{h:02d}00"
                if t_k in day_items:
                    weather_data["forecast_twice_daily"].append({
                        "datetime": base_dt.replace(hour=h).isoformat(), 
                        "is_daytime": is_day, 
                        "native_temperature": t_max, 
                        "native_templow": t_min, 
                        "condition": self._get_condition(day_items[t_k].get("SKY"), day_items[t_k].get("PTY"))
                    })

        mid_t_raw, mid_l_raw = mid_res if mid_res else (None, None)
        if isinstance(mid_t_raw, dict) and isinstance(mid_l_raw, dict):
            try:
                mt = mid_t_raw["response"]["body"]["items"]["item"][0]
                ml = mid_l_raw["response"]["body"]["items"]["item"][0]
                for i in range(3, 11):
                    target_dt = (now + timedelta(days=i)).replace(hour=12, minute=0, second=0, microsecond=0)
                    tmin_v, tmax_v = _safe_float(mt.get(f"taMin{i}")), _safe_float(mt.get(f"taMax{i}"))
                    t_min, t_max = (tmin_v if tmin_v is not None else 15.0), (tmax_v if tmax_v is not None else 25.0)
                    weather_data["forecast_daily"].append({"datetime": target_dt.isoformat(), "native_temperature": t_max, "native_templow": t_min, "condition": self._get_mid_condition(ml.get(f"wf{i}"))})
                    for h, is_day, sfx in [(9, True, "Am"), (21, False, "Pm")]:
                        wf = ml.get(f"wf{i}{sfx}") if i <= 7 else ml.get(f"wf{i}")
                        weather_data["forecast_twice_daily"].append({
                            "datetime": target_dt.replace(hour=h).isoformat(), 
                            "is_daytime": is_day, 
                            "native_temperature": t_max, 
                            "native_templow": t_min, 
                            "condition": self._get_mid_condition(wf)
                        })
            except Exception as e: _LOGGER.warning("중기예보 데이터 파싱 실패: %s", e)

        today_str, tom_str = now.strftime("%Y%m%d"), (now + timedelta(days=1)).strftime("%Y%m%d")
        for d_str, prefix in [(today_str, "today"), (tom_str, "tomorrow")]:
            if d_str in forecast_map:
                day = forecast_map[d_str]
                tmx, tmn = next((day[t].get("TMX") for t in day if "TMX" in day[t]), None), next((day[t].get("TMN") for t in day if "TMN" in day[t]), None)
                all_tmps = [float(day[t]["TMP"]) for t in day if "TMP" in day[t]]
                tmx_f, tmn_f = _safe_float(tmx), _safe_float(tmn)
                weather_data[f"TMX_{prefix}"] = int(tmx_f) if tmx_f is not None else (int(max(all_tmps)) if all_tmps else None)
                weather_data[f"TMN_{prefix}"] = int(tmn_f) if tmn_f is not None else (int(min(all_tmps)) if all_tmps else None)
                am, pm = day.get("0900", {}), day.get("1500", {})
                weather_data[f"weather_am_{prefix}"] = self._get_sky_kor(am.get("SKY"), am.get("PTY"))
                weather_data[f"weather_pm_{prefix}"] = self._get_sky_kor(pm.get("SKY"), pm.get("PTY"))

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
        return "partlycloudy" if "구름많음" in wf else ("cloudy" if "흐림" in wf else "sunny")

    def _get_sky_kor(self, sky, pty):
        p, s = str(pty or "0"), str(sky or "1")
        if p in ["1", "2", "3", "4"]: return {"1":"비","2":"비/눈","3":"눈","4":"소나기"}[p]
        return "맑음" if s == "1" else ("구름많음" if s == "3" else "흐림")

    def _get_vec_kor(self, vec):
        v = _safe_float(vec)
        if v is None: return "북"
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
            if t <= 10 and v >= 4.68: return 13.12 + 0.6215*t - 11.37*(v**0.16) + 0.3965*t*(v**0.16)
            if t >= 18:
                tw = t * math.atan(0.151977 * (rh + 8.313595)**0.5) + math.atan(t + rh) - math.atan(rh - 1.676331) + 0.00391838 * (rh**1.5) * math.atan(0.023101 * rh) - 4.686035
                return -0.25 + 1.04 * tw + 0.65
            return t
        except: return temp
