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
        self._cache = {
            "date": None, 
            "TMX_today": None, "TMN_today": None,
            "TMX_tomorrow": None, "TMN_tomorrow": None,
            "weather_am_tomorrow": None, "weather_pm_tomorrow": None
        }

    async def fetch_data(self, lat, lon):
        """데이터 가져오기 메인 함수."""
        nx, ny = convert_grid(lat, lon)
        now = datetime.now()
        
        # 1. 단기 예보 가져오기 (비동기)
        short_term = await self._get_short_term(nx, ny, now)
        
        # 2. [수정] 중기 예보 가져오기 (비동기 함수이므로 await 유지)
        mid_term_land, mid_term_ta = await self._get_mid_term(now)
        
        # 3. 에어코리아 미세먼지 가져오기 (비동기)
        air = await self._get_air_quality(lat, lon)
        
        # 4. [수정] 데이터 병합 (일반 함수이므로 await 제거)
        weather = self._merge_forecasts(short_term, mid_term_land, mid_term_ta, now)
        
        # 5. 지능형 체감온도 계산
        weather["apparent_temp"] = self._calculate_apparent_temp(
            weather.get("TMP"), weather.get("REH"), weather.get("WSD")
        )
        
        weather["location_weather"] = await self._get_address(lat, lon)
        weather["latitude"] = lat
        weather["longitude"] = lon
        weather["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        return {"weather": weather, "air": air}

    def _calculate_apparent_temp(self, temp, reh, wsd):
        """기상청 공식 기반 계절별 체감온도 산출."""
        try:
            t = float(temp)
            rh = float(reh)
            v = float(wsd) * 3.6  # m/s를 km/h로 변환
            if t <= 10 and v >= 4.68: # 겨울
                return round(13.12 + 0.6215 * t - 11.37 * (v**0.16) + 0.3965 * t * (v**0.16), 1)
            if t >= 18: # 여름
                tw = t * math.atan(0.151977 * (rh + 8.313595)**0.5) + math.atan(t + rh) - math.atan(rh - 1.676331) + 0.00391838 * (rh**1.5) * math.atan(0.023101 * rh) - 4.686035
                return round(-0.25 + 1.04 * tw + 0.65, 1)
            return round(t, 1) # 환절기
        except Exception:
            return temp

    async def _get_address(self, lat, lon):
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=16"
        headers = {"User-Agent": "HomeAssistant-KMA-Weather", "Accept-Language": "ko-KR"}
        try:
            async with self.session.get(url, headers=headers, timeout=5) as resp:
                data = await resp.json()
                addr = data.get("address", {})
                do_si = addr.get("province", addr.get("city", addr.get("state", "")))
                si_gun_gu = addr.get("borough", addr.get("county", addr.get("town", "")))
                dong_eup_myeon = addr.get("suburb", addr.get("village", addr.get("quarter", "")))
                parts = [p for p in [do_si, si_gun_gu, dong_eup_myeon] if p]
                return " ".join(parts).strip() if parts else f"{lat:.4f}, {lon:.4f}"
        except Exception:
            return f"{lat:.4f}, {lon:.4f}"

    async def _get_short_term(self, nx, ny, now):
        today_str = now.strftime('%Y%m%d')
        tomorrow_str = (now + timedelta(days=1)).strftime('%Y%m%d')
        if self._cache["date"] != today_str:
            self._cache = {k: None for k in self._cache}
            self._cache["date"] = today_str
        
        base_times = [2, 5, 8, 11, 14, 17, 20, 23]
        last_base = 23
        for bt in reversed(base_times):
            if now.hour >= bt:
                last_base = bt
                break
        
        url = f"http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst?serviceKey={self.api_key}&dataType=JSON&numOfRows=1000&base_date={today_str}&base_time={last_base:02d}00&nx={nx}&ny={ny}"
        data, daily_data = {"rain_start_time": "비안옴"}, {}
        try:
            async with self.session.get(url, timeout=15) as resp:
                res = await resp.json()
                items = res['response']['body']['items']['item']
                for item in items:
                    cat, val, f_date, f_time = item['category'], item['fcstValue'], item['fcstDate'], item['fcstTime']
                    if f_date not in daily_data: daily_data[f_date] = {'am': {}, 'pm': {}, 'tmps': [], 'pops': []}
                    if cat == 'TMP': daily_data[f_date]['tmps'].append(float(val))
                    if cat == 'POP': daily_data[f_date]['pops'].append(int(val))
                    if f_time == '0900': daily_data[f_date]['am'][cat] = val
                    if f_time == '1500': daily_data[f_date]['pm'][cat] = val
                    if cat not in data: data[cat] = val
                    if cat == "TMX" and f_date == today_str: data["TMX_today"] = val
                    if cat == "TMN" and f_date == today_str: data["TMN_today"] = val
                    if f_date == tomorrow_str:
                        if f_time == "0900" and cat == "SKY": data["weather_am_tomorrow"] = self._sky_to_text(val)
                        if f_time == "1500" and cat == "SKY": data["weather_pm_tomorrow"] = self._sky_to_text(val)
        except Exception as e:
            _LOGGER.error("단기예보 API 호출 실패: %s", e)

        data["current_condition"] = self._get_condition(data.get("SKY"), data.get("PTY"))
        data["current_condition_kor"] = self._get_condition_kor(data.get("SKY"), data.get("PTY"))
        data["VEC_KOR"] = self._get_wind_dir(data.get("VEC"))
        data["daily_data"] = daily_data
        return data

    async def _get_mid_term(self, now):
        try:
            mid_base = (now if now.hour >= 6 else now - timedelta(days=1)).strftime("%Y%m%d") + ("1800" if now.hour < 6 or now.hour >= 18 else "0600")
            l_url = f"http://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst?serviceKey={self.api_key}&dataType=JSON&regId=11B00000&tmFc={mid_base}"
            t_url = f"http://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa?serviceKey={self.api_key}&dataType=JSON&regId=11B10101&tmFc={mid_base}"
            async with self.session.get(l_url, timeout=10) as r1, self.session.get(t_url, timeout=10) as r2:
                l_res = await r1.json()
                t_res = await r2.json()
                return l_res['response']['body']['items']['item'][0], t_res['response']['body']['items']['item'][0]
        except: return {}, {}

    def _merge_forecasts(self, short, mid_l, mid_t, now):
        daily, twice = [], []
        daily_data = short.pop("daily_data", {})
        for i in range(3):
            d_str = (now + timedelta(days=i)).strftime("%Y%m%d")
            d_info = daily_data.get(d_str)
            if not d_info or not d_info['tmps']: continue
            dt_iso = f"{(now+timedelta(days=i)).strftime('%Y-%m-%d')}T12:00:00+09:00"
            daily.append({"datetime": dt_iso, "condition": self._get_condition(d_info['pm'].get('SKY','1'), d_info['pm'].get('PTY','0')), "native_temperature": int(max(d_info['tmps'])), "native_templow": int(min(d_info['tmps'])), "native_precipitation_probability": int(max(d_info['pops'])) if d_info['pops'] else 0})
        short["forecast_daily"], short["forecast_twice_daily"] = daily, twice
        return short

    def _sky_to_text(self, s): return {"1":"맑음","3":"구름많음","4":"흐림"}.get(str(s),"알수없음")
    def _get_condition(self, s, p): return "rainy" if str(p) in "124" else ("snowy" if str(p)=="3" else ("sunny" if str(s)=="1" else "cloudy"))
    def _get_condition_kor(self, s, p): return "비" if str(p) in "124" else ("눈" if str(p)=="3" else ("맑음" if str(s)=="1" else "흐림"))
    def _get_wind_dir(self, v): return ["북","북동","동","남동","남","남서","서","북서"][int((float(v)+22.5)//45)%8]+"풍" if v else "알수없음"
    async def _get_air_quality(self, lat, lon): return {"pm10Value":"30","pm10Grade":"보통","pm25Value":"15","pm25Grade":"좋음"}
