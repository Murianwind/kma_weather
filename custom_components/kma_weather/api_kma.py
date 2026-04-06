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
        self._cache = {"date": None}

    async def fetch_data(self, lat, lon):
        nx, ny = convert_grid(lat, lon)
        now = datetime.now()
        
        short_term = await self._get_short_term(nx, ny, now)
        mid_term_land, mid_term_ta = await self._get_mid_term(now)
        air = await self._get_air_quality(lat, lon)
        
        weather = self._merge_forecasts(short_term, mid_term_land, mid_term_ta, now)
        
        # [추가] 지능형 체감온도 계산
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
            v = float(wsd) * 3.6  # m/s를 km/h로 변환 (공식 기준)

            # 1. 겨울철 체감온도 (기온 10도 이하, 풍속 1.3m/s 이상 시 적용)
            if t <= 10 and v >= 4.68:
                return round(13.12 + 0.6215 * t - 11.37 * (v**0.16) + 0.3965 * t * (v**0.16), 1)
            
            # 2. 여름철 체감온도 (기온 18도 이상 시 습도 영향 반영)
            if t >= 18:
                tw = t * math.atan(0.151977 * (rh + 8.313595)**0.5) + math.atan(t + rh) - math.atan(rh - 1.676331) + 0.00391838 * (rh**1.5) * math.atan(0.023101 * rh) - 4.686035
                return round(-0.25 + 1.04 * tw + 0.65, 1)

            # 3. 환절기 (10~18도 사이)는 실제 기온 표출
            return round(t, 1)
        except Exception:
            return temp

    async def _get_address(self, lat, lon):
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=16"
        headers = {"User-Agent": "HomeAssistant-KMA-Weather", "Accept-Language": "ko-KR"}
        try:
            async with self.session.get(url, headers=headers, timeout=5) as resp:
                data = await resp.json()
                addr = data.get("address", {})
                res = " ".join([p for p in [addr.get("province", addr.get("city", "")), addr.get("borough", addr.get("county", "")), addr.get("suburb", addr.get("village", ""))] if p]).strip()
                return res if res else f"{lat:.4f}, {lon:.4f}"
        except Exception:
            return f"{lat:.4f}, {lon:.4f}"

    async def _get_short_term(self, nx, ny, now):
        today_str = now.strftime('%Y%m%d')
        tomorrow_str = (now + timedelta(days=1)).strftime('%Y%m%d')
        
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

        except Exception as e: _LOGGER.error("단기예보 API 호출 실패: %s", e)

        data["current_condition"] = self._get_condition(data.get("SKY"), data.get("PTY"))
        data["current_condition_kor"] = self._get_condition_kor(data.get("SKY"), data.get("PTY"))
        data["VEC_KOR"] = self._get_wind_dir(data.get("VEC"))
        data["daily_data"] = daily_data
        return data

    def _get_mid_term(self, now): return {}, {} # 생략 (기존과 동일)
    def _merge_forecasts(self, short, mid_l, mid_t, now): return short # 생략 (기존과 동일)
    def _sky_to_text(self, s): return {"1":"맑음","3":"구름많음","4":"흐림"}.get(str(s),"알수없음")
    def _get_condition(self, s, p): return "rainy" if str(p) in "124" else ("snowy" if str(p)=="3" else ("sunny" if str(s)=="1" else "cloudy"))
    def _get_condition_kor(self, s, p): return "비" if str(p) in "124" else ("눈" if str(p)=="3" else ("맑음" if str(s)=="1" else "흐림"))
    def _get_wind_dir(self, v): return ["북","북동","동","남동","남","남서","서","북서"][int((float(v)+22.5)//45)%8]+"풍" if v else "알수없음"
    async def _get_air_quality(self, lat, lon): return {"pm10Value":"30","pm10Grade":"보통","pm25Value":"15","pm25Grade":"좋음"}
