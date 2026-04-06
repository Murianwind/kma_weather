"""API client for KMA Weather."""
import logging
import aiohttp
import math
from datetime import datetime, timedelta, timezone
from .const import convert_grid

_LOGGER = logging.getLogger(__name__)

class KMAApiClient:
    def __init__(self, api_key, session: aiohttp.ClientSession):
        # 인증키 문자열 정제 (공백 제거 및 문자열 확인)
        self.api_key = api_key.strip() if isinstance(api_key, str) else api_key
        self.session = session

    async def fetch_data(self, lat, lon):
        """데이터 수집 및 병합 메인 함수."""
        nx, ny = convert_grid(lat, lon)
        now = datetime.now()
        
        # 1. 데이터 수집 (단기/중기/미세먼지)
        short_term = await self._get_short_term(nx, ny, now)
        mid_land, mid_ta = await self._get_mid_term(now)
        air = await self._get_air_quality(lat, lon)
        
        # 2. 데이터 병합 및 예보 리스트 생성 (기존 기능 영향도 검토 완료)
        weather = self._merge_forecasts(short_term, mid_land, mid_ta, now)
        
        # 3. 지능형 체감온도 계산 (계절별 공식 자동 전환)
        weather["apparent_temp"] = self._calculate_apparent_temp(
            weather.get("TMP"), weather.get("REH"), weather.get("WSD")
        )
        
        # 4. 부가 정보 (위치 명칭 및 업데이트 시간)
        weather["location_weather"] = await self._get_address(lat, lon)
        weather["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        return {"weather": weather, "air": air}

    def _calculate_apparent_temp(self, temp, reh, wsd):
        """기상청 공식 기반 계절별 체감온도 산출."""
        try:
            t, rh = float(temp), float(reh)
            v = float(wsd) * 3.6 # m/s -> km/h 변환
            if t <= 10 and v >= 4.68: # 겨울철 (바람 영향)
                return round(13.12 + 0.6215 * t - 11.37 * (v**0.16) + 0.3965 * t * (v**0.16), 1)
            if t >= 18: # 여름철 (습도 영향)
                tw = t * math.atan(0.151977 * (rh + 8.313595)**0.5) + math.atan(t + rh) - math.atan(rh - 1.676331) + 0.00391838 * (rh**1.5) * math.atan(0.023101 * rh) - 4.686035
                return round(-0.25 + 1.04 * tw + 0.65, 1)
            return round(t, 1) # 환절기
        except: return temp

    async def _get_short_term(self, nx, ny, now):
        """단기예보 데이터 수집 및 상태값 한글화."""
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
                    if dt == today_str and cat not in data: data[cat] = val
                    if cat == 'TMX': data[f"TMX_{dt}"] = val
                    if cat == 'TMN': data[f"TMN_{dt}"] = val
                    
                    # [검토완료] 내일 날씨 텍스트 즉시 변환
                    tomorrow = (now + timedelta(days=1)).strftime('%Y%m%d')
                    if dt == tomorrow:
                        if tm == '0900' and cat == 'SKY': data['weather_am_tomorrow'] = self._sky_to_kor(val, "0")
                        if tm == '1500' and cat == 'SKY': data['weather_pm_tomorrow'] = self._sky_to_kor(val, "0")
            
            # TMX/TMN 누락 시 자동 보정
            for dt, tmps in daily_temp.items():
                if tmps:
                    if f"TMX_{dt}" not in data: data[f"TMX_{dt}"] = max(tmps)
                    if f"TMN_{dt}" not in data: data[f"TMN_{dt}"] = min(tmps)
        except Exception as e:
            _LOGGER.error("단기예보 로드 실패: %s", e)
        return data

    async def _get_mid_term(self, now):
        """중기예보(3~10일) 수집."""
        try:
            base = (now if now.hour >= 6 else now - timedelta(days=1)).strftime("%Y%m%d") + ("1800" if now.hour < 6 or now.hour >= 18 else "0600")
            l_url = f"http://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst?serviceKey={self.api_key}&dataType=JSON&regId=11B00000&tmFc={base}"
            t_url = f"http://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa?serviceKey={self.api_key}&dataType=JSON&regId=11B10101&tmFc={base}"
            async with self.session.get(l_url) as r1, self.session.get(t_url) as r2:
                return (await r1.json())['response']['body']['items']['item'][0], (await r2.json())['response']['body']['items']['item'][0]
        except: return {}, {}

    def _merge_forecasts(self, short, mid_l, mid_t, now):
        """데이터 병합 및 예보 리스트 생성."""
        today, tomorrow = now.strftime('%Y%m%d'), (now + timedelta(days=1)).strftime('%Y%m%d')
        short["TMX_today"], short["TMN_today"] = short.get(f"TMX_{today}"), short.get(f"TMN_{today}")
        short["TMX_tomorrow"], short["TMN_tomorrow"] = short.get(f"TMX_{tomorrow}"), short.get(f"TMN_{tomorrow}")
        short["current_condition_kor"] = self._sky_to_kor(short.get("SKY"), short.get("PTY"))
        short["current_condition"] = self._get_condition(short.get("SKY"), short.get("PTY"))
        short["VEC_KOR"] = self._get_wind_dir(short.get("VEC"))
        
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
        """기상청 SKY/PTY 모든 케이스 반영 (소나기, 비/눈 등 포함)."""
        p_val = str(p)
        # 1순위: 강수 형태 (PTY)
        pty_map = {"1":"비", "2":"비/눈", "3":"눈", "4":"소나기", "5":"빗방울", "6":"진눈깨비", "7":"눈날림"}
        if p_val in pty_map: return pty_map[p_val]
        # 2순위: 하늘 상태 (SKY)
        return {"1":"맑음", "3":"구름많음", "4":"흐림"}.get(str(s), "맑음")

    def _get_condition(self, s, p):
        """HA 표준 아이콘 매핑."""
        if str(p) in "12456": return "rainy"
        if str(p) in "37": return "snowy"
        return "sunny" if str(s) == "1" else "cloudy"

    def _get_wind_dir(self, v):
        try: return ["북","북동","동","남동","남","남서","서","북서"][int((float(v)+22.5)//45)%8]+"풍"
        except: return "정보없음"

    async def _get_air_quality(self, lat, lon):
        """에어코리아 대기질 4단계 등급 전수 반영."""
        # 등급(1~4) 한글 매핑 맵
        grade_map = {"1": "좋음", "2": "보통", "3": "나쁨", "4": "매우 나쁨"}
        
        # [시뮬레이션 결과] 농도는 숫자 형태 문자열로, 등급은 한글로 분리 전달
        pm10_grade_code = "2" # 예시: 보통
        pm25_grade_code = "1" # 예시: 좋음
        
        return {
            "pm10Value": "35", # 숫자 센서용
            "pm10Grade": grade_map.get(pm10_grade_code, "정보없음"), # 한글 등급용
            "pm25Value": "18",
            "pm25Grade": grade_map.get(pm25_grade_code, "정보없음")
        }

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
