import logging
import asyncio
import aiohttp
import math
from datetime import datetime, timedelta
from urllib.parse import quote
import pytz

_LOGGER = logging.getLogger(__name__)

class KMAWeatherAPI:
    """기상청 및 에어코리아 API 통합 관리 클래스 (최종 안정화 버전)"""

    def __init__(self, session, api_key, air_key, nx, ny, reg_id_temp, reg_id_land, lat, lon):
        self.session = session
        self.api_key = api_key
        self.air_key = air_key
        self.nx = nx
        self.ny = ny
        self.reg_id_temp = reg_id_temp
        self.reg_id_land = reg_id_land
        self.lat = lat
        self.lon = lon
        self.tz = pytz.timezone("Asia/Seoul")

    def _wgs84_to_tm(self, lat, lon):
        """EPSG:5181 (GRS80 중부원점) 정밀 가우스-크뤼거 변환 (타원체 보정 적용)"""
        a, f = 6378137.0, 1 / 298.257222101
        e2 = 2*f - f**2
        lat0, lon0 = math.radians(38.0), math.radians(127.0)
        k0, x0, y0 = 1.0, 200000.0, 500000.0
        phi, lam = math.radians(lat), math.radians(lon)
        
        N = a / math.sqrt(1 - e2 * math.sin(phi)**2)
        T, C, A = math.tan(phi)**2, e2 / (1 - e2) * math.cos(phi)**2, math.cos(phi) * (lam - lon0)
        
        def M(p):
            return a * ((1 - e2/4 - 3*e2**2/64 - 5*e2**3/256) * p - 
                        (3*e2/8 + 3*e2**2/32 + 45*e2**3/1024) * math.sin(2*p) + 
                        (15*e2**2/256 + 45*e2**3/1024) * math.sin(4*p) - 
                        (35*e2**3/3072) * math.sin(6*p))
        
        tm_x = x0 + k0 * N * (A + (1 - T + C) * A**3 / 6 + (5 - 18 * T + T**2 + 72 * C - 58 * (e2 / (1 - e2))) * A**5 / 120)
        tm_y = y0 + k0 * (M(phi) - M(lat0) + N * math.tan(phi) * (A**2 / 2 + (5 - T + 9 * C + 4 * C**2) * A**4 / 24 + (61 - 58 * T + T**2 + 600 * C - 330 * (e2 / (1 - e2))) * A**6 / 720))
        return tm_x, tm_y

    async def fetch_data(self):
        """메인 데이터 수집 메서드"""
        now = datetime.now(self.tz)
        tasks = [self._get_short_term(now), self._get_mid_term(now), self._get_air_quality()]
        short_res, mid_res, air_data = await asyncio.gather(*tasks)
        return self._merge_all(now, short_res, mid_res, air_data)

    async def _get_air_quality(self):
        """위치 기반 대기질 수집 (측정소 근접 조회 -> 데이터 조회)"""
        if not self.air_key: return {}
        try:
            tm_x, tm_y = self._wgs84_to_tm(self.lat, self.lon)
            timeout = aiohttp.ClientTimeout(total=15)
            
            st_url = f"http://apis.data.go.kr/B552584/MsrstnInfoInqireSvc/getNearbyMsrstnList?serviceKey={self.air_key}&returnType=json&tmX={tm_x}&tmY={tm_y}&ver=1.0"
            async with self.session.get(st_url, timeout=timeout) as resp:
                if resp.status != 200: return {}
                st_json = await resp.json()
                station_name = st_json['response']['body']['items'][0]['stationName']

            data_url = f"http://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty?serviceKey={self.air_key}&returnType=json&stationName={quote(station_name)}&dataTerm=daily&ver=1.0"
            async with self.session.get(data_url, timeout=timeout) as resp:
                if resp.status != 200: return {}
                item = (await resp.json())['response']['body']['items'][0]
                return {
                    "pm10Value": item.get("pm10Value"),
                    "pm10Grade": self._translate_grade(item.get("pm10Grade")),
                    "pm25Value": item.get("pm25Value"),
                    "pm25Grade": self._translate_grade(item.get("pm25Grade")),
                    "station": station_name
                }
        except Exception as e:
            _LOGGER.warning("에어코리아 수집 실패: %s", e)
        return {}

    def _translate_grade(self, g):
        return {"1": "좋음", "2": "보통", "3": "나쁨", "4": "매우나쁨"}.get(str(g), "정보없음")

    async def _get_short_term(self, now):
        """단기 예보 API 호출 (자정 경계 처리 포함)"""
        adj = now - timedelta(minutes=10)
        base_d = adj.strftime("%Y%m%d")
        
        valid_hours = [h for h in [2, 5, 8, 11, 14, 17, 20, 23] if h <= adj.hour]
        if valid_hours:
            base_h = max(valid_hours)
        else:
            adj_prev = adj - timedelta(days=1)
            base_d = adj_prev.strftime("%Y%m%d")
            base_h = 23
            
        url = f"https://apihub.kma.go.kr/api/typ02/openApi/VilageFcstInfoService_2.0/getVilageFcst?dataType=JSON&base_date={base_d}&base_time={base_h:02d}00&nx={self.nx}&ny={self.ny}&numOfRows=1500&authKey={self.api_key}"
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                return await r.json() if r.status == 200 else None
        except Exception as e:
            _LOGGER.error("단기예보 API 에러: %s", e)
            return None

    async def _get_mid_term(self, now):
        """중기 예보 병렬 호출"""
        if now.hour < 6:
            base_tm = (now - timedelta(days=1)).strftime("%Y%m%d") + "1800"
        elif now.hour < 18:
            base_tm = now.strftime("%Y%m%d") + "0600"
        else:
            base_tm = now.strftime("%Y%m%d") + "1800"

        async def fetch(u):
            try:
                async with self.session.get(u, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    return await r.json() if r.status == 200 else None
            except Exception as e:
                _LOGGER.warning("중기 API 호출 실패: %s", e)
                return None

        urls = [
            f"https://apihub.kma.go.kr/api/typ02/openApi/MidFcstInfoService/getMidTa?dataType=JSON&regId={self.reg_id_temp}&tmFc={base_tm}&authKey={self.api_key}",
            f"https://apihub.kma.go.kr/api/typ02/openApi/MidFcstInfoService/getMidLandFcst?dataType=JSON&regId={self.reg_id_land}&tmFc={base_tm}&authKey={self.api_key}"
        ]
        return await asyncio.gather(*(fetch(u) for u in urls))

    def _merge_all(self, now, short_res, mid_res, air_data):
        """데이터 병합 및 HA 규격 최적화"""
        weather_data = {"forecast_daily": [], "forecast_twice_daily": []}
        forecast_map, rain_start, last_past_slot = {}, "강수없음", None
        
        if short_res and 'response' in short_res:
            items = short_res['response']['body']['items']['item']
            for it in items:
                d, t, cat, val = it['fcstDate'], it['fcstTime'], it['category'], it['fcstValue']
                if d not in forecast_map: forecast_map[d] = {}
                if t not in forecast_map[d]: forecast_map[d][t] = {}
                forecast_map[d][t][cat] = val

            # 현재 상태 추출 및 강수 감지 (눈 포함)
            for d in sorted(forecast_map.keys()):
                for t in sorted(forecast_map[d].keys()):
                    f_dt = datetime.strptime(f"{d}{t}", "%Y%m%d%H%M").replace(tzinfo=self.tz)
                    if f_dt <= now: last_past_slot = forecast_map[d][t]
                    if rain_start == "강수없음" and forecast_map[d][t].get("PTY") in ["1", "2", "3", "4", "7"]:
                        if f_dt >= now:
                            rain_start = f"{t[:2]}:{t[2:]}" if d == now.strftime("%Y%m%d") else f"{int(d[6:8])}일 {t[:2]}:{t[2:]}"
            
            if last_past_slot:
                for cat, val in last_past_slot.items(): weather_data[cat] = val

        # 예보 리스트 생성 (오늘 이후 날짜 필터링)
        today_str = now.strftime("%Y%m%d")
        valid_days = [d for d in sorted(forecast_map.keys()) if d >= today_str]
        
        for d_str in valid_days[:3]:
            day_items = forecast_map[d_str]
            tmps = [float(v["TMP"]) for v in day_items.values() if "TMP" in v]
            base_dt = datetime.strptime(d_str, "%Y%m%d").replace(tzinfo=self.tz)
            
            # Condition Fallback (12시 -> 15시 -> 18시 -> 첫 번째 슬롯)
            rep_slot = day_items.get("1200") or day_items.get("1500") or day_items.get("1800") or next(iter(day_items.values()), {})
            
            weather_data["forecast_daily"].append({
                "datetime": base_dt.isoformat(),
                "native_temperature": max(tmps) if tmps else 20.0,
                "native_templow": min(tmps) if tmps else 10.0,
                "condition": self._get_condition(rep_slot.get("SKY"), rep_slot.get("PTY"))
            })
            
            for h, is_day in [(9, True), (21, False)]:
                t_key = f"{h:02d}00"
                if t_key in day_items:
                    weather_data["forecast_twice_daily"].append({
                        "datetime": base_dt.replace(hour=h).isoformat(),
                        "daytime": is_day,
                        "native_temperature": float(day_items[t_key].get("TMP", 20.0)),
                        "condition": self._get_condition(day_items[t_key].get("SKY"), day_items[t_key].get("PTY"))
                    })

        # 중기 예보 병합 (4~10일)
        mid_t, mid_l = mid_res if mid_res else (None, None)
        if mid_t and mid_l:
            try:
                mt, ml = mid_t['response']['body']['items']['item'][0], mid_l['response']['body']['items']['item'][0]
                for i in range(4, 11):
                    target_dt = (now + timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
                    t_min, t_max = float(mt.get(f"taMin{i}", 15.0)), float(mt.get(f"taMax{i}", 25.0))
                    
                    weather_data["forecast_daily"].append({
                        "datetime": target_dt.isoformat(),
                        "native_temperature": t_max, "native_templow": t_min,
                        "condition": self._get_mid_condition(ml.get(f"wf{i}"))
                    })
                    
                    # Am/Pm 필드 매핑 (3~7일차 구분 및 8~10일차 통합 대응)
                    am_wf, pm_wf = (ml.get(f"wf{i}Am"), ml.get(f"wf{i}Pm")) if i <= 7 else (ml.get(f"wf{i}"), ml.get(f"wf{i}"))
                    
                    weather_data["forecast_twice_daily"].append({
                        "datetime": target_dt.replace(hour=9).isoformat(),
                        "daytime": True, "native_temperature": t_min,
                        "condition": self._get_mid_condition(am_wf)
                    })
                    weather_data["forecast_twice_daily"].append({
                        "datetime": target_dt.replace(hour=21).isoformat(),
                        "daytime": False, "native_temperature": t_max,
                        "condition": self._get_mid_condition(pm_wf)
                    })
            except Exception as e:
                _LOGGER.warning("중기 예보 병합 오류: %s", e)

        weather_data["rain_start_time"] = rain_start
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
