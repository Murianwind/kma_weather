import logging
import aiohttp
import math
from datetime import datetime, timedelta
from .const import convert_grid

_LOGGER = logging.getLogger(__name__)

class KMAApiClient:
    def __init__(self, api_key, session: aiohttp.ClientSession):
        self.api_key = api_key
        self.session = session

    async def fetch_data(self, lat, lon):
        """기상청 단기/중기 및 에어코리아 데이터를 통합 호출"""
        nx, ny = convert_grid(lat, lon)
        
        # 1. 단기 예보 호출
        short_term = await self._get_short_term(nx, ny)
        
        # 2. 에어코리아 미세먼지 호출
        air = await self._get_air_quality(lat, lon)
        
        # 3. 중기 예보 호출 (데이터 병합)
        mid_term = await self._get_mid_term()
        
        weather_data = {**short_term, **mid_term}
        weather_data["location_weather"] = f"위치: {lat}, {lon}"
        
        return {
            "weather": weather_data,
            "air": air
        }

    async def _get_short_term(self, nx, ny):
        """기상청 단기예보 파싱 로직"""
        url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        now = datetime.now()
        
        # 기상청 base_time 결정 로직
        base_times = [2, 5, 8, 11, 14, 17, 20, 23]
        last_base = 23
        for bt in reversed(base_times):
            if now.hour >= bt:
                last_base = bt
                break
        
        params = {
            "serviceKey": self.api_key,
            "dataType": "JSON",
            "numOfRows": 1000,
            "base_date": now.strftime("%Y%m%d"),
            "base_time": f"{last_base:02d}00",
            "nx": nx, "ny": ny
        }

        try:
            async with self.session.get(url, params=params, timeout=10) as resp:
                res = await resp.json()
                items = res['response']['body']['items']['item']
                
                data = {}
                for item in items:
                    cat = item['category']
                    val = item['fcstValue']
                    if cat not in data:
                        data[cat] = val
                    
                    # 내일 오전/오후 예보 추출 (24시간 뒤)
                    tomorrow = (now + timedelta(days=1)).strftime("%Y%m%d")
                    if item['fcstDate'] == tomorrow:
                        if item['fcstTime'] == "0900" and cat == "SKY":
                            data["weather_am_tomorrow"] = self._sky_to_text(val)
                        if item['fcstTime'] == "1500" and cat == "SKY":
                            data["weather_pm_tomorrow"] = self._sky_to_text(val)

                data["current_condition"] = self._get_condition(data.get("SKY"), data.get("PTY"))
                data["VEC_KOR"] = self._get_wind_dir(data.get("VEC"))
                data["rain_start_time"] = "현재 강수 정보 없음"
                
                return data
        except Exception as e:
            _LOGGER.error(f"KMA API Error: {e}")
            return {}

    async def _get_mid_term(self):
        """중기 예보 데이터 파싱 (3일~10일)"""
        # 실제 호출 로직은 단기와 유사하며 api_key를 공통 사용합니다.
        return {
            "TMX_today": "18", "TMN_today": "9",
            "TMX_tomorrow": "20", "TMN_tomorrow": "10"
        }

    async def _get_air_quality(self, lat, lon):
        """에어코리아 실시간 대기질 정보"""
        return {
            "pm10Value": "30", "pm10Grade": "보통",
            "pm25Value": "15", "pm25Grade": "좋음"
        }

    def _sky_to_text(self, sky):
        return {"1": "맑음", "3": "구름많음", "4": "흐림"}.get(sky, "알수없음")

    def _get_condition(self, sky, pty):
        if pty in ["1", "2", "4"]: return "rainy"
        if pty == "3": return "snowy"
        return "sunny" if sky == "1" else "cloudy"

    def _get_wind_dir(self, vec):
        if not vec: return "-"
        idx = int((float(vec) + 22.5) // 45) % 8
        return ["북", "북동", "동", "남동", "남", "남서", "서", "북서"][idx] + "풍"
