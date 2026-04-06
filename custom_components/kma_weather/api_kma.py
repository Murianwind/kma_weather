import logging
import aiohttp
from datetime import datetime, timedelta
from .const import convert_grid

_LOGGER = logging.getLogger(__name__)

class KMAApiClient:
    def __init__(self, kma_key, air_key, session: aiohttp.ClientSession):
        self.kma_key = kma_key
        self.air_key = air_key
        self.session = session

    async def fetch_data(self, lat, lon):
        nx, ny = convert_grid(lat, lon)
        # 1. 단기예보 (오늘~모레)
        short_term = await self._get_short_term(nx, ny)
        # 2. 중기예보 (3일~10일) - 지역코드는 단순화를 위해 서울/경기(11B00000) 예시
        mid_term = await self._get_mid_term("11B00000", "11B10101")
        # 3. 에어코리아 (미세먼지)
        air = await self._get_air_quality(lat, lon)
        
        return {"weather": {**short_term, **mid_term}, "air": air}

    async def _get_short_term(self, nx, ny):
        url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        now = datetime.now()
        params = {
            "serviceKey": self.kma_key, "dataType": "JSON", "numOfRows": 1000,
            "base_date": now.strftime("%Y%m%d"), "base_time": "0200", "nx": nx, "ny": ny
        }
        async with self.session.get(url, params=params) as resp:
            res = await resp.json()
            items = res['response']['body']['items']['item']
            # 오늘 최고/최저, 내일 오전/오후 파싱 로직 적용
            return self._parse_short_term(items)

    async def _get_mid_term(self, land_code, temp_code):
        """중기육상예보 및 중기기온조회 병합"""
        now = datetime.now()
        tm_fc = now.strftime("%Y%m%d0600") # 오전 6시 발표 기준
        
        # 중기육상 (날씨 상태)
        url_land = "http://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst"
        # 중기기온 (최저/최고 기온)
        url_temp = "http://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa"
        
        # ... API 호출 및 데이터 병합 파싱 (wf3Am, taMin3 등 추출) ...
        return {"wf3Am": "흐림", "taMin3": 10, "taMax3": 18} # 예시 데이터

    def _parse_short_term(self, items):
        # 단기 예보 아이템에서 TMX, TMN, SKY, PTY 추출 로직
        return {"TMP": "15", "TMX_today": "20", "TMN_today": "10"}
