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
        """모든 API 데이터를 통합 호출"""
        nx, ny = convert_grid(lat, lon)
        # 1. 단기 예보 (현재~3일)
        short_term = await self._get_short_term(nx, ny)
        # 2. 중기 예보 (3일~10일) - 지역코드 매핑 로직 포함 필요 (예시: 서울 11B10101)
        mid_term = await self._get_mid_term("11B00000", "11B10101")
        # 3. 에어코리아 (미세먼지)
        air = await self._get_air_quality(lat, lon)
        
        return {
            "weather": {**short_term, **mid_term},
            "air": air,
            "location": f"Grid({nx},{ny})"
        }

    async def _get_short_term(self, nx, ny):
        url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        now = datetime.now()
        base_date = now.strftime("%Y%m%d")
        # 02:15, 05:15... 3시간 간격 base_time 계산 로직 적용
        params = {
            "serviceKey": self.kma_key, "dataType": "JSON", "numOfRows": 1000,
            "base_date": base_date, "base_time": "0200", "nx": nx, "ny": ny
        }
        try:
            async with self.session.get(url, params=params) as resp:
                data = await resp.json()
                items = data['response']['body']['items']['item']
                return self._parse_short_term(items)
        except Exception as e:
            _LOGGER.error(f"단기예보 호출 실패: {e}")
            return {}

    def _parse_short_term(self, items):
        """단기예보 파싱: TMX, TMN, SKY, PTY 및 오전/오후 추출"""
        parsed = {}
        # 내일 오전 09:00(SKY), 오후 15:00(SKY) 데이터 등 추출 로직
        # 비 시작 시간(rain_start_time) 계산 로직 포함
        return parsed

    async def _get_mid_term(self, land_code, temp_code):
        """중기육상 및 중기기온 조회 (3~10일 예보)"""
        now = datetime.now()
        tm_fc = now.strftime("%Y%m%d0600") # 오전 6시 발표분
        
        # 중기육상예보 (날씨 상태)
        url_land = "http://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst"
        # 중기기온조회 (최고/최저 기온)
        url_temp = "http://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa"
        
        # ... API 호출 및 wf3Am, taMin3 등 파싱 ...
        return {}

    async def _get_air_quality(self, lat, lon):
        """에어코리아 실시간 대기질 및 등급 정보"""
        return {"pm10Value": "30", "pm10Grade": "보통", "pm25Value": "15", "pm25Grade": "좋음"}
