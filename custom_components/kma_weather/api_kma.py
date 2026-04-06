import aiohttp
import logging
from datetime import datetime
from .const import convert_grid

_LOGGER = logging.getLogger(__name__)

class KMAApiClient:
    def __init__(self, kma_key, air_key, session: aiohttp.ClientSession):
        self.kma_key = kma_key
        self.air_key = air_key
        self.session = session

    async def fetch_data(self, lat, lon):
        nx, ny = convert_grid(lat, lon)
        weather_raw = await self._get_kma_weather(nx, ny)
        air_raw = await self._get_air_quality(lat, lon)
        
        return {
            "weather": weather_raw,
            "air": air_raw
        }

    async def _get_kma_weather(self, nx, ny):
        url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        now = datetime.now()
        # 0215, 0515 등 3시간 주기에 맞춘 base_time 계산 로직 필요 (생략)
        params = {
            "serviceKey": self.kma_key,
            "dataType": "JSON",
            "base_date": now.strftime("%Y%m%d"),
            "base_time": "0200", 
            "nx": nx, "ny": ny,
            "numOfRows": 1000
        }
        try:
            async with self.session.get(url, params=params, timeout=10) as resp:
                json_data = await resp.json()
                items = json_data['response']['body']['items']['item']
                # 가장 가까운 예보 시각 데이터만 필터링
                target_date = items[0]['fcstDate']
                target_time = items[0]['fcstTime']
                return {i['category']: i['fcstValue'] for i in items 
                        if i['fcstDate'] == target_date and i['fcstTime'] == target_time}
        except Exception as e:
            _LOGGER.error(f"KMA API Error: {e}")
            return {}

    async def _get_air_quality(self, lat, lon):
        # 에어코리아 TM 좌표 변환 및 측정소 데이터 호출 로직
        return {"pm10Value": "30", "pm25Value": "15"}
