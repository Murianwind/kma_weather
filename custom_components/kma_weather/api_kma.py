import logging
import asyncio
import math
import json
from datetime import datetime, timedelta
from urllib.parse import unquote
from zoneinfo import ZoneInfo

_LOGGER = logging.getLogger(__name__)

def _safe_float(v):
    try:
        if v == "" or v is None: return None
        return float(v)
    except (TypeError, ValueError):
        return None

class KMAWeatherAPI:
    def __init__(self, session, api_key, reg_id_temp, reg_id_land):
        self.session = session
        self.api_key = unquote(api_key)
        self.reg_id_temp = reg_id_temp
        self.reg_id_land = reg_id_land
        self.tz = ZoneInfo("Asia/Seoul")
        self.lat = self.lon = self.nx = self.ny = None

    async def fetch_data(self, lat, lon, nx, ny):
        self.lat, self.lon, self.nx, self.ny = lat, lon, nx, ny
        now = datetime.now(self.tz)
        tasks = [
            self._get_short_term(now),
            self._get_mid_term(now),
            self._get_air_quality(),
            self._get_address(lat, lon)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        short_res, mid_res, air_data, address = [r if not isinstance(r, Exception) else None for r in results]
        return self._merge_all(now, short_res, mid_res, air_data, address)

    async def _get_address(self, lat, lon):
        try:
            url = "https://nominatim.openstreetmap.org/reverse"
            params = {"format": "json", "lat": lat, "lon": lon, "zoom": 16}
            async with self.session.get(url, params=params, headers={"User-Agent": "HA-KMA-Weather"}, timeout=5) as resp:
                d = await resp.json()
                a = d.get("address", {})
                parts = [a.get('city', a.get('province','')), a.get('borough', a.get('county','')), a.get('suburb', a.get('village', ''))]
                return " ".join([p for p in parts if p]).strip()
        except: return f"{lat:.4f}, {lon:.4f}"

    async def _get_air_quality(self):
        try:
            tm_x, tm_y = self._wgs84_to_tm(self.lat, self.lon)
            url_st = "https://apis.data.go.kr/B552584/MsrstnInfoInqireSvc/getNearbyMsrstnList"
            params_st = {"serviceKey": self.api_key, "returnType": "json", "tmX": f"{tm_x:.2f}", "tmY": f"{tm_y:.2f}"}
            async with self.session.get(url_st, params=params_st, timeout=10) as resp:
                st_json = json.loads(await resp.text())
            items = st_json.get("response", {}).get("body", {}).get("items", [])
            if not items: return {}
            sn = items[0]["stationName"]
            url_data = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty"
            params_data = {"serviceKey": self.api_key, "returnType": "json", "stationName": sn, "dataTerm": "daily", "ver": "1.3"}
            async with self.session.get(url_data, params=params_data, timeout=10) as resp:
                air_json = json.loads(await resp.text())
            ai = air_json.get("response", {}).get("body", {}).get("items", [])[0]
            return {"pm10Value": ai.get("pm10Value"), "pm10Grade": self._translate_grade(ai.get("pm10Grade")), "pm25Value": ai.get("pm25Value"), "pm25Grade": self._translate_grade(ai.get("pm25Grade")), "station": sn}
        except: return {}

    def _translate_grade(self, g):
        return {"1": "좋음", "2": "보통", "3": "나쁨", "4": "매우나쁨"}.get(str(g), "정보없음")

    async def _get_short_term(self, now):
        adj = now - timedelta(minutes=10)
        base_d, base_h = adj.strftime("%Y%m%d"), max([h for h in [2, 5, 8, 11, 14, 17, 20, 23] if h <= adj.hour], default=23)
        if adj.hour < 2: base_d = (adj - timedelta(days=1)).strftime("%Y%m%d")
        url = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        params = {"serviceKey": self.api_key, "dataType": "JSON", "base_date": base_d, "base_time": f"{base_h:02d}00", "nx": self.nx, "ny": self.ny, "numOfRows": 1000}
        try:
            async with self.session.get(url, params=params, timeout=15) as r: return json.loads(await r.text())
        except: return None

    async def _get_mid_term(self, now):
        base = (now if now.hour >= 18 else (now if now.hour >= 6 else now - timedelta(days=1))).strftime("%Y%m%d") + ("0600" if 6 <= now.hour < 18 else "1800")
        async def fetch(u, p):
            try:
                async with self.session.get(u, params=p, timeout=15) as r: return json.loads(await r.text())
            except: return None
        url_ta = "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa"
        url_land = "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst"
        return await asyncio.gather(fetch(url_ta, {"serviceKey": self.api_key, "dataType": "JSON", "regId": self.reg_id_temp, "tmFc": base}), fetch(url_land, {"serviceKey": self.api_key, "dataType": "JSON", "regId": self.reg_id_land, "tmFc": base}))

    def _merge_all(self, now, short_res, mid_res, air_data, address=None):
        weather_data = {
            "TMP": None, "REH": None, "WSD": None, "VEC": None, "POP": None, "PTY": None, "SKY": None,
            "TMX_today": None, "TMN_today": None, "TMX_tomorrow": None, "TMN_tomorrow": None,
            "wf_am_today": None, "wf_pm_today": None, "wf_am_tomorrow": None, "wf_pm_tomorrow": None,
            "rain_start_time": "강수없음", "address": address,
            "forecast_twice_daily": [],
            "debug_nx": self.nx, "debug_ny": self.ny, "debug_lat": self.lat, "debug_lon": self.lon,
            "debug_reg_id_temp": self.reg_id_temp, "debug_reg_id_land": self.reg_id_land
        }
        
        forecast_map = {}
        if short_res and "response" in short_res:
            items = short_res.get("response", {}).get("body", {}).get("items", {}).get("item", [])
            for it in items:
                d, t, cat, val = it["fcstDate"], it["fcstTime"], it["category"], it["fcstValue"]
                forecast_map.setdefault(d, {}).setdefault(t, {})[cat] = val
            
            today_str = now.strftime("%Y%m%d")
            tomorrow_str = (now + timedelta(days=1)).strftime("%Y%m%d")

            # 1. 오늘/내일 최고/최저 기온 및 날씨 정보 추출
            for target_date, prefix in [(today_str, "today"), (tomorrow_str, "tomorrow")]:
                if target_date in forecast_map:
                    tmps = [_safe_float(v.get("TMP")) for v in forecast_map[target_date].values() if "TMP" in v]
                    if tmps:
                        weather_data[f"TMX_{prefix}"] = max(tmps)
                        weather_data[f"TMN_{prefix}"] = min(tmps)
                    
                    # 오전(09시)/오후(15시) 날씨 추출
                    am_val = forecast_map[target_date].get("0900", {})
                    pm_val = forecast_map[target_date].get("1500", {})
                    weather_data[f"wf_am_{prefix}"] = self._get_sky_kor(am_val.get("SKY"), am_val.get("PTY"))
                    weather_data[f"wf_pm_{prefix}"] = self._get_sky_kor(pm_val.get("SKY"), pm_val.get("PTY"))

            # 2. [수정] 현재 시각 기상 상태 업데이트 (없을 경우 가장 가까운 시간 탐색)
            curr_h = f"{now.hour:02d}00"
            if today_str in forecast_map:
                available_times = sorted(forecast_map[today_str].keys())
                # 현재 시간과 일치하거나, 가장 가까운 과거/미래 시간 데이터 선택
                best_time = curr_h if curr_h in available_times else (available_times[0] if available_times else None)
                if best_time:
                    weather_data.update(forecast_map[today_str][best_time])

            # 3. 비 시작 시간 포맷팅
            days_ko = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
            found_rain = False
            for d_str in sorted(forecast_map.keys()):
                for t_str in sorted(forecast_map[d_str].keys()):
                    if d_str == today_str and t_str <= curr_h: continue
                    if forecast_map[d_str][t_str].get("PTY", "0") != "0" and not found_rain:
                        dt = datetime.strptime(d_str + t_str, "%Y%m%d%H%M")
                        time_label = f"{dt.hour}시" + (f" {dt.minute}분" if dt.minute > 0 else "")
                        weather_data["rain_start_time"] = dt.strftime(f"%m월 %d일 {days_ko[dt.weekday()]} {time_label}").replace(" 0", " ")
                        found_rain = True
                        break
                if found_rain: break

        # 4. 10일치 하이브리드 예보 병합
        twice_daily = []
        mid_ta = mid_res[0].get("response",{}).get("body",{}).get("items",{}).get("item",[{}])[0] if mid_res and mid_res[0] else {}
        mid_land = mid_res[1].get("response",{}).get("body",{}).get("items",{}).get("item",[{}])[0] if mid_res and mid_res[1] else {}
        
        for i in range(10):
            target_date = now + timedelta(days=i)
            d_str = target_date.strftime("%Y%m%d")
            for is_am in [True, False]:
                hour = 9 if is_am else 15
                dt_iso = target_date.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()
                short_hour = forecast_map.get(d_str, {}).get(f"{hour:02d}00", {})
                
                if short_hour:
                    tmps = [_safe_float(v.get("TMP")) for v in forecast_map.get(d_str, {}).values() if "TMP" in v]
                    twice_daily.append({
                        "datetime": dt_iso, "is_daytime": is_am,
                        "native_temperature": max(tmps) if tmps else None,
                        "native_templow": min(tmps) if tmps else None,
                        "native_precipitation_probability": _safe_float(short_hour.get("POP")),
                        "condition": self._get_condition(short_hour.get("SKY"), short_hour.get("PTY"))
                    })
                elif i >= 2:
                    wf = mid_land.get(f"wf{i}Am" if is_am else f"wf{i}Pm") or mid_land.get(f"wf{i}")
                    if wf:
                        twice_daily.append({
                            "datetime": dt_iso, "is_daytime": is_am,
                            "native_temperature": _safe_float(mid_ta.get(f"taMax{i}")),
                            "native_templow": _safe_float(mid_ta.get(f"taMin{i}")),
                            "native_precipitation_probability": _safe_float(mid_land.get(f"rnSt{i}Am" if is_am else f"rnSt{i}Pm")),
                            "condition": self._translate_mid_condition(wf)
                        })
        weather_data["forecast_twice_daily"] = twice_daily

        if weather_data.get("VEC"):
            weather_data["VEC_KOR"] = self._get_vec_kor(weather_data["VEC"])
            
        weather_data["current_condition_kor"] = self._get_sky_kor(weather_data.get("SKY"), weather_data.get("PTY"))
        weather_data["current_condition"] = self._get_condition(weather_data.get("SKY"), weather_data.get("PTY"))
        return {"weather": weather_data, "air": air_data or {}}

    def _translate_mid_condition(self, wf):
        wf = str(wf)
        if "비" in wf: return "rainy"
        if "구름많음" in wf: return "partlycloudy"
        if "흐림" in wf: return "cloudy"
        return "sunny"

    def _get_condition(self, s, p):
        p, s = str(p or "0"), str(s or "1")
        if p in ["1", "2", "4", "5", "6"]: return "rainy"
        if p in ["3", "7"]: return "snowy"
        return {"1": "sunny", "3": "partlycloudy", "4": "cloudy"}.get(s, "sunny")

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
