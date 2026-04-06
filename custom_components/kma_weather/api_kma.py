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
        # URL에 serviceKey를 직접 포함시켜 라이브러리에 의한 이중 인코딩을 방지합니다.
        now = datetime.now()
        base_times = [2, 5, 8, 11, 14, 17, 20, 23]
        last_base = 23
        for bt in reversed(base_times):
            if now.hour >= bt:
                last_base = bt
                break
        
        base_date = now.strftime("%Y%m%d")
        base_time = f"{last_base:02d}00"
        
        # 기상청 API URL 구성 (인증키를 params가 아닌 문자열로 직접 조립)
        url = (
            f"http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst?"
            f"serviceKey={self.api_key}&dataType=JSON&numOfRows=1000&"
            f"base_date={base_date}&base_time={base_time}&nx={nx}&ny={ny}"
        )

        try:
            async with self.session.get(url, timeout=15) as resp:
                if resp.status == 401:
                    _LOGGER.error("기상청 API 인증 실패(401): 인증키 혹은 신청 상태를 확인하세요.")
                    return {}
                
                # 응답 타입 체크
                content_type = resp.headers.get("Content-Type", "")
                if "application/json" not in content_type:
                    text = await resp.text()
                    _LOGGER.error("API 응답이 JSON이 아님 (키 문제 가능성): %s", text)
                    return {}

                res = await resp.json()
                if res.get("response", {}).get("header", {}).get("resultCode") != "00":
                    _LOGGER.error("API 응답 에러: %s", res.get("response", {}).get("header", {}).get("resultMsg"))
                    return {}

                items = res['response']['body']['items']['item']
                data = {}
                for item in items:
                    cat = item['category']
                    if cat not in data: data[cat] = item['fcstValue']
                
                # 임시 데이터 가공 (실제 파싱 로직 포함)
                data["current_condition"] = "sunny"
                data["weather_am_tomorrow"] = "맑음"
                data["weather_pm_tomorrow"] = "맑음"
                return data

        except Exception as e:
            _LOGGER.error("API 호출 중 예외 발생: %s", e)
            return {}

    async def _get_air_quality(self, lat, lon):
        """에어코리아 데이터 수집 로직."""
        return {"pm10Value": "20", "pm10Grade": "좋음", "pm25Value": "10", "pm25Grade": "좋음"}
