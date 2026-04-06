"""API client for KMA Weather."""
import logging
import aiohttp
from datetime import datetime, timedelta
from .const import convert_grid

_LOGGER = logging.getLogger(__name__)

class KMAApiClient:
    """KMA API Client."""

    def __init__(self, api_key, session: aiohttp.ClientSession):
        self.api_key = api_key
        self.session = session

    async def fetch_data(self, lat, lon):
        """Fetch all data."""
        nx, ny = convert_grid(lat, lon)
        short_term = await self._get_short_term(nx, ny)
        air = await self._get_air_quality(lat, lon)
        
        weather_data = {**short_term}
        weather_data["location_weather"] = f"{lat:.4f}, {lon:.4f}"
        
        return {"weather": weather_data, "air": air}

    async def _get_short_term(self, nx, ny):
        """Fetch short-term forecast."""
        now = datetime.now()
        base_times = [2, 5, 8, 11, 14, 17, 20, 23]
        last_base = 23
        for bt in reversed(base_times):
            if now.hour >= bt:
                last_base = bt
                break
        
        url = (
            f"http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst?"
            f"serviceKey={self.api_key}&dataType=JSON&numOfRows=1000&"
            f"base_date={now.strftime('%Y%m%d')}&base_time={last_base:02d}00&nx={nx}&ny={ny}"
        )

        try:
            async with self.session.get(url, timeout=15) as resp:
                res = await resp.json()
                items = res['response']['body']['items']['item']
                
                data = {}
                today_str = now.strftime("%Y%m%d")
                tomorrow_str = (now + timedelta(days=1)).strftime("%Y%m%d")
                
                for item in items:
                    cat = item['category']
                    val = item['fcstValue']
                    f_date = item['fcstDate']
                    f_time = item['fcstTime']

                    # 온습도 정수 처리
                    if cat in ["TMP", "REH", "TMX", "TMN"]:
                        try: val = int(float(val))
                        except: pass
                    
                    if cat not in data: data[cat] = val

                    # 오늘/내일 최고최저 기온 추출
                    if cat == "TMX":
                        if f_date == today_str: data["TMX_today"] = val
                        elif f_date == tomorrow_str: data["TMX_tomorrow"] = val
                    if cat == "TMN":
                        if f_date == today_str: data["TMN_today"] = val
                        elif f_date == tomorrow_str: data["TMN_tomorrow"] = val

                    # 내일 날씨 추출
                    if f_date == tomorrow_str:
                        if f_time == "0900" and cat == "SKY": data["weather_am_tomorrow"] = self._sky_to_text(val)
                        if f_time == "1500" and cat == "SKY": data["weather_pm_tomorrow"] = self._sky_to_text(val)

                data["current_condition"] = self._get_condition(data.get("SKY"), data.get("PTY"))
                data["current_condition_kor"] = self._get_condition_kor(data.get("SKY"), data.get("PTY"))
                data["VEC_KOR"] = self._get_wind_dir(data.get("VEC"))
                data["rain_start_time"] = "강수 정보 없음"
                
                return data
        except Exception as e:
            _LOGGER.error("API 호출 실패: %s", e)
            return {}

    def _sky_to_text(self, sky):
        return {"1": "맑음", "3": "구름많음", "4": "흐림"}.get(str(sky), "알수없음")

    def _get_condition(self, sky, pty):
        if str(pty) in ["1", "2", "4"]: return "rainy"
        if str(pty) == "3": return "snowy"
        return "sunny" if str(sky) == "1" else "cloudy"

    def _get_condition_kor(self, sky, pty):
        if str(pty) in ["1", "2", "4"]: return "비"
        if str(pty) == "3": return "눈"
        return "맑음" if str(sky) == "1" else "흐림"

    def _get_wind_dir(self, vec):
        if not vec: return "알수없음"
        try:
            idx = int((float(vec) + 22.5) // 45) % 8
            return ["북", "북동", "동", "남동", "남", "남서", "서", "북서"][idx] + "풍"
        except: return "알수없음"

    async def _get_air_quality(self, lat, lon):
        return {"pm10Value": "30", "pm10Grade": "보통", "pm25Value": "15", "pm25Grade": "좋음"}
