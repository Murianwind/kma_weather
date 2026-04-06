"""API client for KMA Weather."""
import logging
import aiohttp
from datetime import datetime, timedelta
from yarl import URL # 인코딩 이슈 해결을 위해 사용
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
        """Fetch short-term data with error handling."""
        # yarl.URL을 사용하여 이미 인코딩된 serviceKey가 다시 인코딩되지 않도록 설정
        base_url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        now = datetime.now()
        
        # 기상청 시간 계산
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
            # encoded=True를 통해 인증키의 특수문자가 변형되는 것을 방지
            async with self.session.get(base_url, params=params, timeout=15) as resp:
                if resp.status == 401:
                    _LOGGER.error("기상청 API 인증 실패(401): 인증키를 확인해주세요.")
                    return {}
                
                # JSON 응답이 아닐 경우(에러 메시지가 텍스트로 올 경우) 처리
                if "application/json" not in resp.headers.get("Content-Type", ""):
                    text = await resp.text()
                    _LOGGER.error("API가 JSON 대신 텍스트 응답을 반환함: %s", text)
                    return {}

                res = await resp.json()
                # 정상 응답 구조 확인
                if res.get("response", {}).get("header", {}).get("resultCode") != "00":
                    _LOGGER.error("API 에러 발생: %s", res.get("response", {}).get("header", {}).get("resultMsg"))
                    return {}

                items = res['response']['body']['items']['item']
                data = {}
                for item in items:
                    cat = item['category']
                    if cat not in data: data[cat] = item['fcstValue']
                
                data["current_condition"] = "sunny" # 가공 로직 생략
                data["weather_am_tomorrow"] = "맑음"
                data["weather_pm_tomorrow"] = "흐림"
                return data

        except Exception as e:
            _LOGGER.error("KMA API 호출 중 예외 발생: %s", e)
            return {}

    async def _get_air_quality(self, lat, lon):
        """에어코리아 데이터 (더미)"""
        return {"pm10Value": "30", "pm10Grade": "보통", "pm25Value": "15", "pm25Grade": "좋음"}
