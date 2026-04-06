import logging
import aiohttp
from datetime import datetime
from .const import convert_grid

_LOGGER = logging.getLogger(__name__)

class KMAApiClient:
    def __init__(self, kma_key, air_key, session: aiohttp.ClientSession):
        self.kma_key = kma_key
        self.air_key = air_key
        self.session = session

    async def fetch_weather(self, lat, lon):
        """기상청 단기예보 API 호출"""
        nx, ny = convert_grid(lat, lon)
        now = datetime.now()
        # 기상청 업데이트 기준에 맞춘 날짜/시간 생성 로직 (0215, 0515...)
        base_date = now.strftime("%Y%m%d")
        base_time = self._get_base_time(now)
        
        url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        params = {
            "serviceKey": self.kma_key,
            "numOfRows": 1000,
            "pageNo": 1,
            "dataType": "JSON",
            "base_date": base_date,
            "base_time": base_time,
            "nx": nx,
            "ny": ny
        }
        
        async with self.session.get(url, params=params) as response:
            if response.status == 200:
                return await response.json()
            return None

    async def get_nearby_station(self, lat, lon):
        """에어코리아 근접 측정소 명칭 찾기"""
        url = "http://apis.data.go.kr/B552584/MsrstnInfoInqireSvc/getNearbyMsrstnList"
        params = {
            "serviceKey": self.air_key,
            "returnType": "json",
            "tmX": lat, # 실제로는 TM 좌표 변환 필요 (간소화를 위해 로직 추가 예정)
            "tmY": lon
        }
        # ... 측정소 검색 로직 ...
        return "종로구" # 예시 결과

    def _get_base_time(self, now):
        """0215부터 3시간 간격 계산"""
        hours = [2, 5, 8, 11, 14, 17, 20, 23]
        curr_hour = now.hour
        for h in reversed(hours):
            if curr_hour >= h:
                return f"{h:02d}00"
        return "2300" # 어제 데이터
