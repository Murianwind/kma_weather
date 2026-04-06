"""API client for KMA Weather."""
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
        
        url = (
            f"http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst?"
            f"serviceKey={self.api_key}&dataType=JSON&numOfRows=1000&"
            f"base_date={now.strftime('%Y%m%d')}&base_time={last_base:02d}00&nx={nx}&ny={ny}"
        )

        try:
            async with self.session.get(url, timeout=15) as resp:
                res = await resp.json()
                items = res['response']['body']['items']['item']
                
                data = {}
                daily_data = {}
                today_str = now.strftime("%Y%m%d")
                tomorrow_str = (now + timedelta(days=1)).strftime("%Y%m%d")
                
                for item in items:
                    cat = item['category']
                    val = item['fcstValue']
                    f_date = item['fcstDate']
                    f_time = item['fcstTime']

                    # 정수/소수 변환 가능 시 처리
                    try: val = float(val) if '.' in val else int(val)
                    except: pass

                    # 날짜별 데이터 수집 (예보 생성 및 최저/최고 계산용)
                    if f_date not in daily_data:
                        daily_data[f_date] = {'am': {}, 'pm': {}, 'tmps': [], 'pops': []}
                    
                    if cat == 'TMP': daily_data[f_date]['tmps'].append(val)
                    if cat == 'POP': daily_data[f_date]['pops'].append(val)
                    if f_time == '0900': daily_data[f_date]['am'][cat] = val
                    if f_time == '1500': daily_data[f_date]['pm'][cat] = val

                    # 현재 데이터 (가장 빠른 예보) 저장
                    if cat not in data: data[cat] = val

                    # 기상청 공식 TMX/TMN 저장
                    if cat == "TMX" and f_date == today_str: data["TMX_today"] = val
                    if cat == "TMN" and f_date == today_str: data["TMN_today"] = val
                    if cat == "TMX" and f_date == tomorrow_str: data["TMX_tomorrow"] = val
                    if cat == "TMN" and f_date == tomorrow_str: data["TMN_tomorrow"] = val

                # [해결 1] 기상청이 TMX/TMN을 누락했을 경우, 시간별 기온(TMP)으로 직접 계산 (대체 로직)
                today_info = daily_data.get(today_str, {})
                if "TMX_today" not in data and today_info.get('tmps'): data["TMX_today"] = max(today_info['tmps'])
                if "TMN_today" not in data and today_info.get('tmps'): data["TMN_today"] = min(today_info['tmps'])

                # [해결 3] 날씨 요약 (매일 / 매일 2회) 예보 리스트 생성
                forecast_daily = []
                forecast_twice_daily = []

                for d_str, d_info in daily_data.items():
                    if not d_info['tmps']: continue
                    dt_str = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]}"
                    
                    # 매일 예보 (Daily)
                    forecast_daily.append({
                        "datetime": f"{dt_str}T12:00:00+09:00",
                        "condition": self._get_condition(d_info['pm'].get('SKY', d_info['am'].get('SKY', '1')), 
                                                         d_info['pm'].get('PTY', d_info['am'].get('PTY', '0'))),
                        "native_temperature": max(d_info['tmps']),
                        "native_templow": min(d_info['tmps']),
                        "native_precipitation_probability": max(d_info['pops']) if d_info['pops'] else 0,
                    })

                    # 매일 2회 예보 (AM)
                    if d_info['am']:
                        forecast_twice_daily.append({
                            "datetime": f"{dt_str}T09:00:00+09:00",
                            "is_daytime": True,
                            "condition": self._get_condition(d_info['am'].get('SKY', '1'), d_info['am'].get('PTY', '0')),
                            "native_temperature": float(d_info['am'].get('TMP', 0)),
                            "native_precipitation_probability": int(d_info['am'].get('POP', 0)),
                        })
                    # 매일 2회 예보 (PM)
                    if d_info['pm']:
                        forecast_twice_daily.append({
                            "datetime": f"{dt_str}T15:00:00+09:00",
                            "is_daytime": False,
                            "condition": self._get_condition(d_info['pm'].get('SKY', '1'), d_info['pm'].get('PTY', '0')),
                            "native_temperature": float(d_info['pm'].get('TMP', 0)),
                            "native_precipitation_probability": int(d_info['pm'].get('POP', 0)),
                        })

                data["forecast_daily"] = forecast_daily
                data["forecast_twice_daily"] = forecast_twice_daily

                # 기타 현재 상태 가공
                data["current_condition"] = self._get_condition(data.get("SKY"), data.get("PTY"))
                data["current_condition_kor"] = self._get_condition_kor(data.get("SKY"), data.get("PTY"))
                data["VEC_KOR"] = self._get_wind_dir(data.get("VEC"))
                tomorrow_info = daily_data.get(tomorrow_str, {})
                data["weather_am_tomorrow"] = self._sky_to_text(tomorrow_info.get('am', {}).get('SKY', '1'))
                data["weather_pm_tomorrow"] = self._sky_to_text(tomorrow_info.get('pm', {}).get('SKY', '1'))
                data["rain_start_time"] = "강수 정보 없음"
                
                return data
        except Exception as e:
            _LOGGER.error("API 호출 실패: %s", e)
            return {}

    def _sky_to_text(self, sky):
        return {"1": "맑음", "3": "구름많음", "4": "흐림"}.get(str(sky), "알수없음")

    def _get_condition(self, sky, pty):
        if str(pty) in ["1", "2", "4"]: return "rainy"
        if str(pty) == "3": return "snowy"
        return "sunny" if str(sky) == "1" else "cloudy"

    def _get_condition_kor(self, sky, pty):
        if str(pty) in ["1", "2", "4"]: return "비"
        if str(pty) == "3": return "눈"
        return "맑음" if str(sky) == "1" else "흐림"

    def _get_wind_dir(self, vec):
        if not vec: return "알수없음"
        try:
            idx = int((float(vec) + 22.5) // 45) % 8
            return ["북", "북동", "동", "남동", "남", "남서", "서", "북서"][idx] + "풍"
        except: return "알수없음"

    async def _get_air_quality(self, lat, lon):
        return {"pm10Value": "30", "pm10Grade": "보통", "pm25Value": "15", "pm25Grade": "좋음"}
