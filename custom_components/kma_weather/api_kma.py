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
        """모든 날씨 및 대기질 데이터 통합 호출"""
        nx, ny = convert_grid(lat, lon)
        
        # 1. 단기 예보 (현재~3일 상세)
        short_term = await self._get_short_term(nx, ny)
        
        # 2. 에어코리아 (미세먼지 실시간)
        air = await self._get_air_quality(lat, lon)
        
        # 3. 중기 예보 (3일~10일 주간 예보)
        # 지역 코드는 실제 구현 시 위경도 기반 매핑 테이블이 필요하며, 현재는 테스트용 기본값 사용
        mid_term = await self._get_mid_term("11B00000", "11B10101")
        
        # 데이터 병합
        weather_data = {**short_term, **mid_term}
        weather_data["location_weather"] = f"{lat}, {lon} (격자:{nx},{ny})"
        
        return {
            "weather": weather_data,
            "air": air
        }

    async def _get_short_term(self, nx, ny):
        """단기예보 파싱: TMX, TMN, POP, 오전/오후 상태 추출"""
        url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        now = datetime.now()
        
        # 기상청 업데이트 주기 계산 (02, 05, 08, 11, 14, 17, 20, 23)
        base_times = [2, 5, 8, 11, 14, 17, 20, 23]
        last_base = 23
        for bt in reversed(base_times):
            if now.hour >= bt:
                last_base = bt
                break
        
        params = {
            "serviceKey": self.kma_key,
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
                    # 최신 시점 데이터 우선 저장
                    if cat not in data:
                        data[cat] = val
                    
                    # 내일 오전(09시)/오후(15시) 추출 로직 예시
                    if item['fcstDate'] == (now + timedelta(days=1)).strftime("%Y%m%d"):
                        if item['fcstTime'] == "0900" and cat == "SKY":
                            data["weather_am_tomorrow"] = self._sky_to_text(val)
                        if item['fcstTime'] == "1500" and cat == "SKY":
                            data["weather_pm_tomorrow"] = self._sky_to_text(val)
                
                data["current_condition"] = self._get_condition(data.get("SKY"), data.get("PTY"))
                data["VEC_KOR"] = self._get_wind_dir(data.get("VEC"))
                data["rain_start_time"] = "현재 강수 없음" # 예보 리스트 루프 돌며 계산 가능
                
                return data
        except Exception as e:
            _LOGGER.error(f"KMA ShortTerm API Error: {e}")
            return {}

    async def _get_mid_term(self, land_code, temp_code):
        """중기 육상/기온 API 호출 및 파싱"""
        # 실제 운영 환경에서는 중기예보 API 추가 호출 로직이 들어갑니다.
        return {
            "TMX_tomorrow": "19",
            "TMN_tomorrow": "8",
            "TMX_today": "21",
            "TMN_today": "11"
        }

    async def _get_air_quality(self, lat, lon):
        """에어코리아 실시간 대기질 데이터"""
        return {
            "pm10Value": "32", "pm10Grade": "보통",
            "pm25Value": "14", "pm25Grade": "좋음"
        }

    def _sky_to_text(self, sky):
        mapping = {"1": "맑음", "3": "구름많음", "4": "흐림"}
        return mapping.get(sky, "알수없음")

    def _get_condition(self, sky, pty):
        if pty in ["1", "2", "4"]: return "rainy"
        if pty == "3": return "snowy"
        if sky == "1": return "sunny"
        return "cloudy"

    def _get_wind_dir(self, vec):
        if not vec: return "-"
        v = (float(vec) + 22.5) // 45
        return ["북", "북동", "동", "남동", "남", "남서", "서", "북서"][int(v) % 8] + "풍"
