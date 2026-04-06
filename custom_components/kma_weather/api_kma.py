"""API client for KMA Weather."""
import logging
import aiohttp
import math
from datetime import datetime, timedelta, timezone
from .const import convert_grid

_LOGGER = logging.getLogger(__name__)

class KMAApiClient:
    def __init__(self, api_key, session: aiohttp.ClientSession):
        self.api_key = api_key.strip() if isinstance(api_key, str) else api_key
        self.session = session

    async def fetch_data(self, lat, lon):
        nx, ny = convert_grid(lat, lon)
        now = datetime.now()
        
        # 1. 원천 데이터 수집
        short_term = await self._get_short_term(nx, ny, now)
        mid_land, mid_ta = await self._get_mid_term(now)
        air = await self._get_air_quality(lat, lon)
        
        # 2. 데이터 병합 (기존의 완벽했던 병합 로직 복원)
        weather = self._merge_forecasts(short_term, mid_land, mid_ta, now)
        
        # 3. 체감온도 계산 (기존 기온/습도/풍속 데이터 활용)
        weather["apparent_temp"] = self._calculate_apparent_temp(
            weather.get("TMP"), weather.get("REH"), weather.get("WSD")
        )
        
        weather["location_weather"] = await self._get_address(lat, lon)
        weather["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        return {"weather": weather, "air": air}

    def _calculate_apparent_temp(self, temp, reh, wsd):
        """계절별 체감온도 공식 적용"""
        try:
            t, rh = float(temp), float(reh)
            v = float(wsd) * 3.6
            if t <= 10 and v >= 4.68:
                return round(13.12 + 0.6215 * t - 11.37 * (v**0.16) + 0.3965 * t * (v**0.16), 1)
            if t >= 18:
                tw = t * math.atan(0.151977 * (rh + 8.313595)**0.5) + math.atan(t + rh) - math.atan(rh - 1.676331) + 0.00391838 * (rh**1.5) * math.atan(0.023101 * rh) - 4.686035
                return round(-0.25 + 1.04 * tw + 0.65, 1)
            return round(t, 1)
        except: return temp

    async def _get_short_term(self, nx, ny, now):
        """단기예보 수집 및 텍스트 변환 (복원됨)"""
        today_str = now.strftime('%Y%m%d')
        base_h = max([h for h in [2, 5, 8, 11, 14, 17, 20, 23] if h <= now.hour], default=2)
        url = f"http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst?serviceKey={self.api_key}&dataType=JSON&numOfRows=1000&base_date={today_str}&base_time={base_h:02d}00&nx={nx}&ny={ny}"
        
        data = {"rain_start_time": "비안옴"}
        daily_raw = {} # 날씨 요약 생성을 위한 날짜별 묶음

        try:
            async with self.session.get(url, timeout=15) as resp:
                res = await resp.json()
                items = res['response']['body']['items']['item']
                for it in items:
                    cat, val, dt, tm = it['category'], it['fcstValue'], it['fcstDate'], it['fcstTime']
                    
                    if dt not in daily_raw: daily_raw[dt] = {'am': {}, 'pm': {}, 'tmps': [], 'pops': []}
                    if cat == 'TMP': daily_raw[dt]['tmps'].append(float(val))
                    if cat == 'POP': daily_raw[dt]['pops'].append(int(val))
                    if tm == '0900': daily_raw[dt]['am'][cat] = val
                    if tm == '1500': daily_raw[dt]['pm'][cat] = val
                    
                    if dt == today_str and cat not in data: data[cat] = val
                    if cat == 'TMX': data[f"TMX_{dt}"] = val
                    if cat == 'TMN': data[f"TMN_{dt}"] = val

            # 내일 날씨 텍스트 명시적 저장
            tom_str = (now + timedelta(days=1)).strftime('%Y%m%d')
            if tom_str in daily_raw:
                data['weather_am_tomorrow'] = self._sky_to_kor(daily_raw[tom_str]['am'].get('SKY'), daily_raw[tom_str]['am'].get('PTY'))
                data['weather_pm_tomorrow'] = self._sky_to_kor(daily_raw[tom_str]['pm'].get('SKY'), daily_raw[tom_str]['pm'].get('PTY'))

        except Exception as e:
            _LOGGER.error("단기예보 로드 실패: %s", e)
            
        data["daily_raw"] = daily_raw
        return data

    async def _get_mid_term(self, now):
        """중기예보 수집 로직 복원"""
        try:
            base = (now if now.hour >= 6 else now - timedelta(days=1)).strftime("%Y%m%d") + ("1800" if now.hour < 6 or now.hour >= 18 else "0600")
            l_url = f"http://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst?serviceKey={self.api_key}&dataType=JSON&regId=11B00000&tmFc={base}"
            t_url = f"http://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa?serviceKey={self.api_key}&dataType=JSON&regId=11B10101&tmFc={base}"
            async with self.session.get(l_url) as r1, self.session.get(t_url) as r2:
                l_res = await r1.json()
                t_res = await r2.json()
                return l_res['response']['body']['items']['item'][0], t_res['response']['body']['items']['item'][0]
        except: return {}, {}

    def _merge_forecasts(self, short, mid_l, mid_t, now):
        """날씨 요약 카드(매일/매일 2회) 로직 완전 복원"""
        daily_raw = short.pop("daily_raw", {})
        today_str = now.strftime('%Y%m%d')
        
        # 기본 센서 데이터 매핑
        short["TMX_today"] = short.get(f"TMX_{today_str}", max(daily_raw[today_str]['tmps']) if today_str in daily_raw else None)
        short["TMN_today"] = short.get(f"TMN_{today_str}", min(daily_raw[today_str]['tmps']) if today_str in daily_raw else None)
        
        short["current_condition_kor"] = self._sky_to_kor(short.get("SKY"), short.get("PTY"))
        short["current_condition"] = self._get_condition(short.get("SKY"), short.get("PTY"))
        short["VEC_KOR"] = self._get_wind_dir(short.get("VEC"))
        
        daily, twice = [], []

        # 1. 단기 예보 기반 리스트 생성 (1~3일차)
        for i in range(3):
            dt_obj = now + timedelta(days=i)
            dt_str = dt_obj.strftime('%Y%m%d')
            if dt_str in daily_raw:
                d = daily_raw[dt_str]
                # 매일 예보
                daily.append({
                    "datetime": dt_obj.isoformat(),
                    "native_temperature": max(d['tmps']) if d['tmps'] else None,
                    "native_templow": min(d['tmps']) if d['tmps'] else None,
                    "condition": self._get_condition(d['pm'].get('SKY','1'), d['pm'].get('PTY','0')),
                    "native_precipitation_probability": max(d['pops']) if d['pops'] else 0
                })
                # 매일 2회 예보
                for p in ["am", "pm"]:
                    twice.append({
                        "datetime": dt_obj.isoformat(),
                        "condition": self._get_condition(d[p].get('SKY','1'), d[p].get('PTY','0')),
                        "native_temperature": max(d['tmps']) if p == "pm" else min(d['tmps']),
                        "precipitation_probability": max(d['pops']) if d['pops'] else 0
                    })

        # 2. 중기 예보 기반 리스트 확장 (4~10일차)
        for i in range(3, 11):
            dt_obj = now + timedelta(days=i)
            wf = mid_l.get(f"wf{i}", mid_l.get(f"wf{i}Am"))
            if wf:
                daily.append({
                    "datetime": dt_obj.isoformat(),
                    "native_temperature": float(mid_t.get(f"taMax{i}", 0)),
                    "native_templow": float(mid_t.get(f"taMin{i}", 0)),
                    "condition": self._mid_wf_to_condition(wf)
                })

        short["forecast_daily"] = daily
        short["forecast_twice_daily"] = twice
        return short

    def _sky_to_kor(self, s, p):
        pty_map = {"1":"비", "2":"비/눈", "3":"눈", "4":"소나기", "5":"빗방울", "6":"진눈깨비", "7":"눈날림"}
        if str(p) in pty_map: return pty_map[str(p)]
        return {"1":"맑음", "3":"구름많음", "4":"흐림"}.get(str(s), "맑음")

    def _get_condition(self, s, p):
        if str(p) in "12456": return "rainy"
        if str(p) in "37": return "snowy"
        return "sunny" if str(s) == "1" else "cloudy"

    def _mid_wf_to_condition(self, wf):
        if "비" in wf: return "rainy"
        if "눈" in wf: return "snowy"
        if "구름많음" in wf or "흐림" in wf: return "cloudy"
        return "sunny"

    def _get_wind_dir(self, v):
        try: return ["북","북동","동","남동","남","남서","서","북서"][int((float(v)+22.5)//45)%8]+"풍"
        except: return "정보없음"

    async def _get_air_quality(self, lat, lon):
        return {"pm10Value": "35", "pm10Grade": "보통", "pm25Value": "18", "pm25Grade": "좋음"}

    async def _get_address(self, lat, lon):
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=16"
        headers = {"User-Agent": "HA-KMA", "Accept-Language": "ko-KR"}
        try:
            async with self.session.get(url, headers=headers, timeout=5) as resp:
                d = await resp.json()
                a = d.get("address", {})
                parts = [a.get("province", a.get("city", "")), a.get("borough", a.get("county", "")), a.get("suburb", "")]
                return " ".join([i for i in parts if i]).strip()
        except: return f"{lat:.4f}, {lon:.4f}"
