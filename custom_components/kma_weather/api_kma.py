"""API client for KMA Weather."""
import logging
import aiohttp
import math
from datetime import datetime, timedelta, timezone
from .const import convert_grid

_LOGGER = logging.getLogger(__name__)

class KMAApiClient:
    def __init__(self, api_key, session: aiohttp.ClientSession):
        self.api_key = api_key
        self.session = session
        # 온도 관리를 위한 내부 저장소
        self._temp_store = {}

    async def fetch_data(self, lat, lon):
        nx, ny = convert_grid(lat, lon)
        now = datetime.now()
        
        # 1. 데이터 수집
        short_term = await self._get_short_term(nx, ny, now)
        mid_land, mid_ta = await self._get_mid_term(now)
        air = await self._get_air_quality(lat, lon)
        
        # 2. 데이터 병합 및 예보 생성
        weather = self._merge_forecasts(short_term, mid_land, mid_ta, now)
        
        # 3. 체감온도 계산 (기존 로직 유지)
        weather["apparent_temp"] = self._calculate_apparent_temp(
            weather.get("TMP"), weather.get("REH"), weather.get("WSD")
        )
        
        weather["location_weather"] = await self._get_address(lat, lon)
        weather["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        return {"weather": weather, "air": air}

    def _calculate_apparent_temp(self, temp, reh, wsd):
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

    async def _get_address(self, lat, lon):
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=16"
        headers = {"User-Agent": "HA-KMA", "Accept-Language": "ko-KR"}
        try:
            async with self.session.get(url, headers=headers, timeout=5) as resp:
                data = await resp.json()
                addr = data.get("address", {})
                p = [addr.get("province", addr.get("city", "")), addr.get("borough", addr.get("county", "")), addr.get("suburb", "")]
                return " ".join([i for i in p if i]).strip()
        except: return f"{lat:.4f}, {lon:.4f}"

    async def _get_short_term(self, nx, ny, now):
        today_str = now.strftime('%Y%m%d')
        base_h = max([h for h in [2, 5, 8, 11, 14, 17, 20, 23] if h <= now.hour], default=2)
        url = f"http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst?serviceKey={self.api_key}&dataType=JSON&numOfRows=1000&base_date={today_str}&base_time={base_h:02d}00&nx={nx}&ny={ny}"
        
        data = {"rain_start_time": "비안옴"}
        daily_temp = {}

        try:
            async with self.session.get(url, timeout=15) as resp:
                res = await resp.json()
                items = res['response']['body']['items']['item']
                for it in items:
                    cat, val, dt, tm = it['category'], it['fcstValue'], it['fcstDate'], it['fcstTime']
                    
                    if dt not in daily_temp: daily_temp[dt] = []
                    if cat == 'TMP': daily_temp[dt].append(float(val))
                    
                    # 현재 값 저장
                    if dt == today_str and cat not in data:
                        data[cat] = val
                    
                    # 최고/최저 기온 직접 추출 (기상청 TMX/TMN은 특정 시각에만 나옴)
                    if cat == 'TMX': data[f"TMX_{dt}"] = val
                    if cat == 'TMN': data[f"TMN_{dt}"] = val
                    
                    # 내일 날씨 정보
                    tomorrow = (now + timedelta(days=1)).strftime('%Y%m%d')
                    if dt == tomorrow:
                        if tm == '0900' and cat == 'SKY': data['weather_am_tomorrow'] = val
                        if tm == '1500' and cat == 'SKY': data['weather_pm_tomorrow'] = val

            # 기상청 TMX/TMN 데이터가 누락될 경우 대비해 리스트에서 직접 계산
            for dt, tmps in daily_temp.items():
                if tmps:
                    if f"TMX_{dt}" not in data: data[f"TMX_{dt}"] = max(tmps)
                    if f"TMN_{dt}" not in data: data[f"TMN_{dt}"] = min(tmps)

        except Exception as e:
            _LOGGER.error("단기예보 로드 실패: %s", e)
        
        data["daily_raw"] = daily_temp
        return data

    async def _get_mid_term(self, now):
        try:
            base = (now if now.hour >= 6 else now - timedelta(days=1)).strftime("%Y%m%d") + ("1800" if now.hour < 6 or now.hour >= 18 else "0600")
            l_url = f"http://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst?serviceKey={self.api_key}&dataType=JSON&regId=11B00000&tmFc={base}"
            t_url = f"http://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa?serviceKey={self.api_key}&dataType=JSON&regId=11B10101&tmFc={base}"
            async with self.session.get(l_url) as r1, self.session.get(t_url) as r2:
                return (await r1.json())['response']['body']['items']['item'][0], (await r2.json())['response']['body']['items']['item'][0]
        except: return {}, {}

    def _merge_forecasts(self, short, mid_l, mid_t, now):
        # 센서에서 사용하는 핵심 키값 명시적 할당
        today = now.strftime('%Y%m%d')
        tomorrow = (now + timedelta(days=1)).strftime('%Y%m%d')
        
        short["TMX_today"] = short.get(f"TMX_{today}")
        short["TMN_today"] = short.get(f"TMN_{today}")
        short["TMX_tomorrow"] = short.get(f"TMX_{tomorrow}")
        short["TMN_tomorrow"] = short.get(f"TMN_{tomorrow}")
        
        # 캘린더 로직과 유사하게 날씨 텍스트 변환
        short["current_condition_kor"] = self._sky_to_kor(short.get("SKY"), short.get("PTY"))
        short["current_condition"] = self._get_condition(short.get("SKY"), short.get("PTY"))
        short["VEC_KOR"] = self._get_wind_dir(short.get("VEC"))
        
        # 예보 리스트 생성 (기존 로직)
        daily = []
        for i in range(3):
            dt = (now + timedelta(days=i)).strftime('%Y%m%d')
            if f"TMX_{dt}" in short:
                daily.append({
                    "datetime": (now + timedelta(days=i)).isoformat(),
                    "native_temperature": float(short[f"TMX_{dt}"]),
                    "native_templow": float(short[f"TMN_{dt}"]),
                    "condition": self._get_condition(short.get("SKY"), short.get("PTY"))
                })
        short["forecast_daily"] = daily
        return short

    def _sky_to_kor(self, s, p):
        if str(p) in "145": return "비"
        if str(p) == "3": return "눈"
        return {"1":"맑음","3":"구름많음","4":"흐림"}.get(str(s), "맑음")

    def _get_condition(self, s, p):
        if str(p) in "1245": return "rainy"
        if str(p) == "3": return "snowy"
        return "sunny" if str(s) == "1" else "cloudy"

    def _get_wind_dir(self, v):
        try: return ["북","북동","동","남동","남","남서","서","북서"][int((float(v)+22.5)//45)%8]+"풍"
        except: return "정보없음"

    async def _get_air_quality(self, lat, lon):
        return {"pm10Value":"보통","pm10Grade":"2","pm25Value":"좋음","pm25Grade":"1"}
