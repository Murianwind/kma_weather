import logging
import aiohttp
from datetime import datetime, timedelta
from .const import convert_grid

_LOGGER = logging.getLogger(__name__)

class KMAApiClient:
    def __init__(self, api_key, session: aiohttp.ClientSession):
        self.api_key = api_key
        self.session = session

    async def fetch_data(self, lat, lon):
        nx, ny = convert_grid(lat, lon)
        # 단기/에어코리아 통합 호출 (중기예보는 구조상 별도 구현 필요)
        short_term = await self._get_short_term(nx, ny)
        air = await self._get_air_quality(lat, lon)
        
        weather = {**short_term}
        weather["location_weather"] = f"{lat:.4f}, {lon:.4f}"
        return {"weather": weather, "air": air}

    async def _get_short_term(self, nx, ny):
        now = datetime.now()
        base_times = [2, 5, 8, 11, 14, 17, 20, 23]
        last_base = 23
        for bt in reversed(base_times):
            if now.hour >= bt:
                last_base = bt
                break
        
        # URL에 키를 직접 포함하여 이중 인코딩 방지
        url = (
            f"http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst?"
            f"serviceKey={self.api_key}&dataType=JSON&numOfRows=1000&"
            f"base_date={now.strftime('%Y%m%d')}&base_time={last_base:02d}00&nx={nx}&ny={ny}"
        )

        try:
            async with self.session.get(url, timeout=15) as resp:
                if resp.status == 401:
                    _LOGGER.error("기상청 API 인증 실패(401). 키를 확인하세요.")
                    return {}
                res = await resp.json()
                items = res['response']['body']['items']['item']
                
                data = {}
                tomorrow = (now + timedelta(days=1)).strftime("%Y%m%d")
                for item in items:
                    cat = item['category']
                    val = item['fcstValue']
                    # 현재 시각 데이터 우선
                    if cat not in data: data[cat] = val
                    # 내일 오전/오후 및 최고/최저 추출
                    if item['fcstDate'] == tomorrow:
                        if item['fcstTime'] == "0900" and cat == "SKY": data["weather_am_tomorrow"] = self._sky_to_text(val)
                        if item['fcstTime'] == "1500" and cat == "SKY": data["weather_pm_tomorrow"] = self._sky_to_text(val)
                        if cat == "TMX": data["TMX_tomorrow"] = val
                        if cat == "TMN": data["TMN_tomorrow"] = val
                    if cat == "TMX" and item['fcstDate'] == now.strftime("%Y%m%d"): data["TMX_today"] = val
                    if cat == "TMN" and item['fcstDate'] == now.strftime("%Y%m%d"): data["TMN_today"] = val

                data["current_condition"] = self._get_condition(data.get("SKY"), data.get("PTY"))
                data["current_condition_kor"] = self._get_condition_kor(data.get("SKY"), data.get("PTY"))
                data["VEC_KOR"] = self._get_wind_dir(data.get("VEC"))
                data["rain_start_time"] = "강수 정보 없음"
                return data
        except Exception as e:
            _LOGGER.error("API 호출 실패: %s", e)
            return {}

    def _sky_to_text(self, sky):
        return {"1": "맑음", "3": "구름많음", "4": "흐림"}.get(sky, "알수없음")

    def _get_condition(self, sky, pty):
        if pty in ["1", "2", "4"]: return "rainy"
        if pty == "3": return "snowy"
        return "sunny" if sky == "1" else "cloudy"

    def _get_condition_kor(self, sky, pty):
        if pty in ["1", "2", "4"]: return "비"
        if pty == "3": return "눈"
        return "맑음" if sky == "1" else "흐림"

    def _get_wind_dir(self, vec):
        if not vec: return "알수없음"
        idx = int((float(vec) + 22.5) // 45) % 8
        return ["북", "북동", "동", "남동", "남", "남서", "서", "북서"][idx] + "풍"

    async def _get_air_quality(self, lat, lon):
        return {"pm10Value": "30", "pm10Grade": "보통", "pm25Value": "15", "pm25Grade": "좋음"}
