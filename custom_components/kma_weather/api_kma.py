import logging
import aiohttp
import math
from datetime import datetime, timedelta
from .const import convert_grid

_LOGGER = logging.getLogger(__name__)

class KMAApiClient:
    def __init__(self, kma_key, air_key, session: aiohttp.ClientSession):
        self.kma_key = kma_key
        self.air_key = air_key
        self.session = session

    async def fetch_data(self, lat, lon):
        """단기/중기/미세먼지 데이터 통합 호출"""
        nx, ny = convert_grid(lat, lon)
        
        # 1. 단기 예보 및 실황 호출
        short_term = await self._get_short_term(nx, ny)
        
        # 2. 에어코리아 미세먼지 호출
        air = await self._get_air_quality(lat, lon)
        
        # 3. 중기 예보 호출 (예시 코드는 서울 기준이며 실제로는 구역코드 매핑 로직이 들어갑니다)
        mid_term = await self._get_mid_term("11B00000", "11B10101")
        
        # 모든 데이터 병합
        combined_weather = {**short_term, **mid_term}
        combined_weather["location_weather"] = f"격자 {nx}, {ny}"
        
        return {
            "weather": combined_weather,
            "air": air
        }

    async def _get_short_term(self, nx, ny):
        """기상청 단기예보 API 호출 및 16개 센서용 데이터 추출"""
        url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        now = datetime.now()
        
        # 기상청 업데이트 주기에 따른 base_time 계산 (02, 05, 08, 11, 14, 17, 20, 23)
        base_times = [2, 5, 8, 11, 14, 17, 20, 23]
        current_hour = now.hour
        last_base = 23
        for bt in reversed(base_times):
            if current_hour >= bt:
                last_base = bt
                break
        
        base_date = now.strftime("%Y%m%d")
        base_time = f"{last_base:02d}00"

        params = {
            "serviceKey": self.kma_key,
            "dataType": "JSON",
            "numOfRows": 1000,
            "base_date": base_date,
            "base_time": base_time,
            "nx": nx, "ny": ny
        }

        try:
            async with self.session.get(url, params=params) as resp:
                res = await resp.json()
                items = res['response']['body']['items']['item']
                
                parsed = {}
                # 현재 시각 예보값 및 오늘/내일 최고/최저 추출 (간략화된 로직)
                for item in items:
                    category = item['category']
                    val = item['fcstValue']
                    # 필요한 카테고리(TMP, REH, WSD, VEC, TMX, TMN, POP 등) 저장
                    if category not in parsed:
                        parsed[category] = val
                
                # 가공 데이터 생성
                parsed["current_condition"] = self._get_condition_text(parsed.get("SKY"), parsed.get("PTY"))
                parsed["VEC_KOR"] = self._get_wind_dir_text(parsed.get("VEC"))
                parsed["rain_start_time"] = "강수 없음" # 실제 계산 로직 필요
                
                return parsed
        except Exception as e:
            _LOGGER.error(f"KMA Short Term API Error: {e}")
            return {}

    async def _get_mid_term(self, land_code, temp_code):
        """중기 예보 (3~10일) 호출"""
        # 공공데이터포털 중기예보 서비스 호출 로직 구현...
        return {
            "weather_am_tomorrow": "흐림",
            "weather_pm_tomorrow": "맑음",
            "TMX_tomorrow": "18",
            "TMN_tomorrow": "9"
        }

    async def _get_air_quality(self, lat, lon):
        """에어코리아 미세먼지 데이터 호출"""
        # 에어코리아 근접 측정소 기반 데이터 호출 로직...
        return {
            "pm10Value": "35",
            "pm10Grade": "보통",
            "pm25Value": "15",
            "pm25Grade": "좋음"
        }

    def _get_condition_text(self, sky, pty):
        if pty == "1": return "비"
        if pty == "2": return "비/눈"
        if pty == "3": return "눈"
        if sky == "1": return "맑음"
        if sky == "3": return "구름많음"
        return "흐림"

    def _get_wind_dir_text(self, vec):
        if not vec: return "-"
        v = (float(vec) + 22.5) // 45
        dirs = ["북", "북동", "동", "남동", "남", "남서", "서", "북서"]
        return dirs[int(v) % 8] + "풍"
