import aiohttp
from datetime import datetime
from .const import convert_grid

class KMAApiClient:
    def __init__(self, kma_key, air_key, session: aiohttp.ClientSession):
        self.kma_key = kma_key
        self.air_key = air_key
        self.session = session

    async def fetch_data(self, lat, lon):
        nx, ny = convert_grid(lat, lon)
        # 1. 기상청 데이터 (단기예보)
        weather = await self._get_kma_weather(nx, ny)
        # 2. 에어코리아 데이터 (미세먼지)
        air = await self._get_air_quality(lat, lon)
        return {"weather": weather, "air": air}

    async def _get_kma_weather(self, nx, ny):
        url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        now = datetime.now()
        params = {
            "serviceKey": self.kma_key,
            "dataType": "JSON",
            "base_date": now.strftime("%Y%m%d"),
            "base_time": "0200", # 실제로는 시간대별 계산 로직 포함 필요
            "nx": nx, "ny": ny
        }
        async with self.session.get(url, params=params) as resp:
            res = await resp.json()
            items = res['response']['body']['items']['item']
            return {i['category']: i['fcstValue'] for i in items if i['fcstDate'] == now.strftime("%Y%m%d")}

    async def _get_air_quality(self, lat, lon):
        # 측정소 찾기 및 미세먼지 수집 로직 (간략화)
        return {"pm10": "35", "pm25": "15"}
