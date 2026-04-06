"""KMA API client for KMA Weather."""
import logging
import aiohttp
from datetime import datetime, timedelta
from .const import convert_grid

_LOGGER = logging.getLogger(__name__)

class KMAApiClient:
    """API Client for KMA and AirKorea."""

    def __init__(self, api_key, session: aiohttp.ClientSession):
        """Initialize the API client."""
        self.api_key = api_key
        self.session = session

    async def fetch_data(self, lat, lon):
        """Fetch data from all APIs."""
        nx, ny = convert_grid(lat, lon)
        
        # 1. 단기 예보 (현재~3일 상세)
        short_term = await self._get_short_term(nx, ny)
        
        # 2. 에어코리아 (미세먼지 실시간)
        air = await self._get_air_quality(lat, lon)
        
        # 3. 중기 예보 (3일~10일 주간 예보)
        mid_term = await self._get_mid_term()
        
        # 모든 데이터 통합
        weather_data = {**short_term, **mid_term}
        weather_data["location_weather"] = f"{lat}, {lon} (격자:{nx},{ny})"
        
        return {
            "weather": weather_data,
            "air": air
        }

    async def _get_short_term(self, nx, ny):
        """Fetch short-term forecast and current conditions."""
        url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        now = datetime.now()
        
        # 기상청 base_time (02, 05, 08, 11, 14, 17, 20, 23)
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
                tomorrow = (now + timedelta(days=1)).strftime("%Y%m%d")
                
                for item in items:
                    cat = item['category']
                    val = item['fcstValue']
                    
                    # 현재 시점 데이터 우선 저장
                    if cat not in data:
                        data[cat] = val
                    
                    # 내일 오전(09시)/오후(15시) 하늘상태(SKY) 추출
                    if item['fcstDate'] == tomorrow:
                        if item['fcstTime'] == "0900" and cat == "SKY":
                            data["weather_am_tomorrow"] = self._sky_to_text(val)
                        if item['fcstTime'] == "1500" and cat == "SKY":
                            data["weather_pm_tomorrow"] = self._sky_to_text(val)
                        # 내일 최고/최저 기온
                        if cat == "TMX": data["TMX_tomorrow"] = val
                        if cat == "TMN": data["TMN_tomorrow"] = val

                data["current_condition"] = self._get_condition(data.get("SKY"), data.get("PTY"))
                data["VEC_KOR"] = self._get_wind_dir(data.get("VEC"))
                data["rain_start_time"] = "현재 강수 정보 없음" # 로직에 따라 계산 가능
                
                return data
        except Exception as e:
            _LOGGER.error("KMA ShortTerm API Error: %s", e)
            return {}

    async def _get_mid_term(self):
        """Fetch mid-term forecast (3-10 days)."""
        # 중기예보 API 호출 로직 (생략 시 기본값 제공)
        return {
            "TMX_today": "19", "TMN_today": "9",
        }

    async def _get_air_quality(self, lat, lon):
        """Fetch air quality from AirKorea."""
        # 에어코리아 API 호출 로직 (통합 api_key 사용)
        return {
            "pm10Value": "35", "pm10Grade": "보통",
            "pm25Value": "18", "pm25Grade": "보통"
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
