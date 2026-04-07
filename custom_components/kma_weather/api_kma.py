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
    """데이터를 안전하게 float로 변환하며, 0도 등을 보존합니다."""
    try:
        if v == "" or v is None: return None
        return float(v)
    except (TypeError, ValueError):
        return None

class KMAWeatherAPI:
    """기상청 및 에어코리아 API 연동 클래스."""

    def __init__(self, session, api_key, reg_id_temp, reg_id_land):
        self.session = session
        self.api_key = unquote(api_key)  # 이중 인코딩 방지
        self.air_key = self.api_key
        self.reg_id_temp = reg_id_temp
        self.reg_id_land = reg_id_land
        self.tz = ZoneInfo("Asia/Seoul")
        self.lat = self.lon = self.nx = self.ny = None

    async def fetch_data(self, lat, lon, nx, ny):
        """모든 날씨 및 대기질 데이터를 병렬로 가져옵니다."""
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
        """Nominatim을 통해 좌표를 주소로 변환합니다."""
        try:
            url = "https://nominatim.openstreetmap.org/reverse"
            params = {"format": "json", "lat": lat, "lon": lon, "zoom": 16}
            async with self.session.get(url, params=params, headers={"User-Agent": "HA-KMA-Weather"}, timeout=5) as resp:
                d = await resp.json()
                a = d.get("address", {})
                parts = [a.get('city', a.get('province','')), a.get('borough', a.get('county','')), a.get('suburb', a.get('village', ''))]
                return " ".join([p for p in parts if p]).strip()
        except:
            return f"{lat:.4f}, {lon:.4f}"

    async def _get_air_quality(self):
        """에어코리아 측정소 탐색 및 실시간 대기질 조회."""
        try:
            tm_x, tm_y = self._wgs84_to_tm(self.lat, self.lon)
            # 1. 근접 측정소 탐색
            url_st = "https://apis.data.go.kr/B552584/MsrstnInfoInqireSvc/getNearbyMsrstnList"
            params_st = {"serviceKey": self.air_key, "returnType": "json", "tmX": f"{tm_x:.2f}", "tmY": f"{tm_y:.2f}"}
            async with self.session.get(url_st, params=params_st, timeout=10) as resp:
                st_json = json.loads(await resp.text())
            
            items = st_json.get("response", {}).get("body", {}).get("items", [])
            if not items: return {}
            sn = items[0]["stationName"]
            
            # 2. 대기질 데이터 조회
            url_data = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty"
            params_data = {"serviceKey": self.air_key, "returnType": "json", "stationName": sn, "dataTerm": "daily", "ver": "1.3"}
            async with self.session.get(url_data, params=params_data, timeout=10) as resp:
                air_json = json.loads(await resp.text())
            
            ai = air_json.get("response", {}).get("body", {}).get("items", [])[0]
            return {
                "pm10Value": ai.get("pm10Value"), "pm10Grade": self._translate_grade(ai.get("pm10Grade")),
                "pm25Value": ai.get("pm25Value"), "pm25Grade": self._translate_grade(ai.get("pm25Grade")),
                "station": sn
            }
        except: return {}

    def _translate_grade(self, g):
        return {"1": "좋음", "2": "보통", "3": "나쁨", "4": "매우나쁨"}.get(str(g), "정보없음")

    async def _get_short_term(self, now):
        """단기예보 API 호출."""
        adj = now - timedelta(minutes=10)
        base_d, base_h = adj.strftime("%Y%m%d"), max([h for h in [2, 5, 8, 11, 14, 17, 20, 23] if h <= adj.hour], default=23)
        if adj.hour < 2: base_d = (adj - timedelta(days=1)).strftime("%Y%m%d")
        
        url = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        params = {"serviceKey": self.api_key, "dataType": "JSON", "base_date": base_d, "base_time": f"{base_h:02d}00", "nx": self.nx, "ny": self.ny, "numOfRows": 1000}
        try:
            async with self.session.get(url, params=params, timeout=15) as r:
                return json.loads(await r.text())
        except: return None

    async def _get_mid_term(self, now):
        """중기예보(육상/기온) API 호출."""
        base = (now if now.hour >= 18 else (now if now.hour >= 6 else now - timedelta(days=1))).strftime("%Y%m%d") + ("0600" if 6 <= now.hour < 18 else "1800")
        async def fetch(u, p):
            try:
                async with self.session.get(u, params=p, timeout=15) as r: return json.loads(await r.text())
            except: return None
        
        url_ta = "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa"
        url_land = "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst"
        return await asyncio.gather(
            fetch(url_ta, {"serviceKey": self.api_key, "dataType": "JSON", "regId": self.reg_id_temp, "tmFc": base}),
            fetch(url_land, {"serviceKey": self.api_key, "dataType": "JSON", "regId": self.reg_id_land, "tmFc": base})
        )

    def _merge_all(self, now, short_res, mid_res, air_data, address=None):
        """모든 응답을 병합하여 최종 딕셔너리 생성 (디버그 속성 포함)."""
        weather_data = {
            "TMP": None, "REH": None, "WSD": None, "VEC": None, "POP": None, "PTY": None, "SKY": None,
            "TMX_today": None, "TMN_today": None, "weather_am_today": None, "weather_pm_today": None,
            "rain_start_time": "강수없음", "address": address,
            "debug_lat": self.lat, "debug_lon": self.lon, "debug_nx": self.nx, "debug_ny": self.ny,
            "debug_reg_id_temp": self.reg_id_temp, "debug_reg_id_land": self.reg_id_land,
            "station": air_data.get("station") if air_data else "정보없음"
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
                day_data = forecast_map[today_str]
                tmps = [_safe_float(v.get("TMP")) for v in day_data.values() if "TMP" in v]
                if tmps: weather_data["TMX_today"], weather_data["TMN_today"] = max(tmps), min(tmps)
                
                am_sky = day_data.get("0900", {}).get("SKY")
                am_pty = day_data.get("0900", {}).get("PTY")
                pm_sky = day_data.get("1500", {}).get("SKY")
                pm_pty = day_data.get("1500", {}).get("PTY")
                weather_am = self._get_sky_kor(am_sky, am_pty)
                weather_pm = self._get_sky_kor(pm_sky, pm_pty)
                weather_data.update({"weather_am_today": weather_am, "weather_pm_today": weather_pm})

        # 풍향 변환, 체감온도 계산 로직 포함
        if weather_data.get("VEC"): weather_data["VEC_KOR"] = self._get_vec_kor(weather_data["VEC"])
        weather_data["current_condition_kor"] = self._get_sky_kor(weather_data.get("SKY"), weather_data.get("PTY"))
        weather_data["current_condition"] = self._get_condition(weather_data.get("SKY"), weather_data.get("PTY"))
        weather_data["apparent_temp"] = self._calculate_apparent_temp(weather_data.get("TMP"), weather_data.get("REH"), weather_data.get("WSD"))
        
        return {"weather": weather_data, "air": air_data or {}}

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

    def _calculate_apparent_temp(self, temp, reh, wsd):
        try:
            t, rh, v = _safe_float(temp), _safe_float(reh), _safe_float(wsd)
            if t is None: return None
            v_kmh = v * 3.6
            if t <= 10 and v_kmh >= 4.8:
                return round(13.12 + 0.6215*t - 11.37*(v_kmh**0.16) + 0.3965*t*(v_kmh**0.16), 1)
            return t
        except: return temp

    def _wgs84_to_tm(self, lat, lon):
        """WGS84 좌표를 TM 좌표로 변환 (에어코리아 API용)."""
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
