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
        self._cache = {
            "date": None, 
            "TMX_today": None, "TMN_today": None,
            "TMX_tomorrow": None, "TMN_tomorrow": None,
            "weather_am_tomorrow": None, "weather_pm_tomorrow": None
        }

    async def fetch_data(self, lat, lon):
        nx, ny = convert_grid(lat, lon)
        now = datetime.now()
        
        short_term = await self._get_short_term(nx, ny, now)
        mid_term_land, mid_term_ta = await self._get_mid_term(now)
        air = await self._get_air_quality(lat, lon)
        
        weather = self._merge_forecasts(short_term, mid_term_land, mid_term_ta, now)
        
        # [수정] 위경도를 기반으로 주소를 가져오고, 원본 위경도도 함께 저장
        weather["location_weather"] = await self._get_address(lat, lon)
        weather["latitude"] = lat
        weather["longitude"] = lon
        
        return {"weather": weather, "air": air}

    async def _get_address(self, lat, lon):
        """OpenStreetMap을 이용해 위경도를 한국어 주소로 변환"""
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=16"
        headers = {
            "User-Agent": "HomeAssistant-KMA-Weather",
            "Accept-Language": "ko-KR,ko;q=0.9" # 한국어 응답 요청
        }
        try:
            async with self.session.get(url, headers=headers, timeout=5) as resp:
                data = await resp.json()
                addr = data.get("address", {})
                
                # 한국 주소 체계 파싱 (시/도 + 시/군/구 + 동/읍/면)
                do_si = addr.get("province", addr.get("city", addr.get("state", "")))
                si_gun_gu = addr.get("borough", addr.get("county", addr.get("town", "")))
                dong_eup_myeon = addr.get("suburb", addr.get("village", addr.get("quarter", "")))
                
                parts = []
                for p in [do_si, si_gun_gu, dong_eup_myeon]:
                    if p and p not in parts:
                        parts.append(p)
                
                res = " ".join(parts).strip()
                return res if res else f"{lat:.4f}, {lon:.4f}"
        except Exception as e:
            _LOGGER.warning("주소 변환 실패(API 오류): %s", e)
            return f"{lat:.4f}, {lon:.4f}"

    async def _get_short_term(self, nx, ny, now):
        today_str = now.strftime('%Y%m%d')
        tomorrow_str = (now + timedelta(days=1)).strftime('%Y%m%d')

        if self._cache["date"] != today_str:
            self._cache = {k: None for k in self._cache}
            self._cache["date"] = today_str

        base_times = [2, 5, 8, 11, 14, 17, 20, 23]
        last_base = 23
        for bt in reversed(base_times):
            if now.hour >= bt:
                last_base = bt
                break
        
        url = (
            f"http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst?"
            f"serviceKey={self.api_key}&dataType=JSON&numOfRows=1000&"
            f"base_date={today_str}&base_time={last_base:02d}00&nx={nx}&ny={ny}"
        )

        data, daily_data = {}, {}
        try:
            async with self.session.get(url, timeout=15) as resp:
                res = await resp.json()
                items = res['response']['body']['items']['item']
                
                for item in items:
                    cat, val, f_date, f_time = item['category'], item['fcstValue'], item['fcstDate'], item['fcstTime']
                    try: val = float(val) if '.' in val else int(val)
                    except: pass

                    if f_date not in daily_data:
                        daily_data[f_date] = {'am': {}, 'pm': {}, 'tmps': [], 'pops': []}
                    
                    if cat == 'TMP': daily_data[f_date]['tmps'].append(val)
                    if cat == 'POP': daily_data[f_date]['pops'].append(val)
                    if f_time == '0900': daily_data[f_date]['am'][cat] = val
                    if f_time == '1500': daily_data[f_date]['pm'][cat] = val

                    if cat not in data: data[cat] = val

                    if cat == "TMX" and f_date == today_str: data["TMX_today"] = val
                    if cat == "TMN" and f_date == today_str: data["TMN_today"] = val
                    if cat == "TMX" and f_date == tomorrow_str: data["TMX_tomorrow"] = val
                    if cat == "TMN" and f_date == tomorrow_str: data["TMN_tomorrow"] = val
                    
                    if f_date == tomorrow_str:
                        if f_time == "0900" and cat == "SKY": data["weather_am_tomorrow"] = self._sky_to_text(val)
                        if f_time == "1500" and cat == "SKY": data["weather_pm_tomorrow"] = self._sky_to_text(val)

        except Exception as e:
            _LOGGER.error("단기예보 API 호출 실패: %s", e)

        keys_to_cache = ["TMX_today", "TMN_today", "TMX_tomorrow", "TMN_tomorrow", "weather_am_tomorrow", "weather_pm_tomorrow"]
        for k in keys_to_cache:
            if k in data: self._cache[k] = data[k]
            elif self._cache[k] is not None: data[k] = self._cache[k]

        for k in ["TMP", "REH", "TMX_today", "TMN_today", "TMX_tomorrow", "TMN_tomorrow"]:
            if data.get(k) is not None: data[k] = int(data[k])

        data["daily_data"] = daily_data
        data["current_condition"] = self._get_condition(data.get("SKY"), data.get("PTY"))
        data["current_condition_kor"] = self._get_condition_kor(data.get("SKY"), data.get("PTY"))
        data["VEC_KOR"] = self._get_wind_dir(data.get("VEC"))
        data["rain_start_time"] = "강수 정보 없음"
        
        return data

    async def _get_mid_term(self, now):
        try:
            if now.hour < 6: mid_base = (now - timedelta(days=1)).strftime("%Y%m%d") + "1800"
            elif now.hour < 18: mid_base = now.strftime("%Y%m%d") + "0600"
            else: mid_base = now.strftime("%Y%m%d") + "1800"

            land_url = f"http://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst?serviceKey={self.api_key}&dataType=JSON&numOfRows=10&pageNo=1&regId=11B00000&tmFc={mid_base}"
            ta_url = f"http://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa?serviceKey={self.api_key}&dataType=JSON&numOfRows=10&pageNo=1&regId=11B10101&tmFc={mid_base}"

            land_data, ta_data = {}, {}
            async with self.session.get(land_url, timeout=10) as resp:
                land_data = (await resp.json())['response']['body']['items']['item'][0]
            async with self.session.get(ta_url, timeout=10) as resp:
                ta_data = (await resp.json())['response']['body']['items']['item'][0]

            return land_data, ta_data
        except Exception as e:
            _LOGGER.error("중기예보 에러 (10일치 누락 가능성): %s", e)
            return {}, {}

    def _merge_forecasts(self, short_term, mid_land, mid_ta, now):
        daily, twice = [], []
        daily_data = short_term.pop("daily_data", {})
        
        for i in range(3):
            d_str = (now + timedelta(days=i)).strftime("%Y%m%d")
            dt_str = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]}"
            d_info = daily_data.get(d_str)
            if not d_info or not d_info['tmps']: continue

            if i == 0:
                t_max = short_term.get("TMX_today", max(d_info['tmps']))
                t_min = short_term.get("TMN_today", min(d_info['tmps']))
            elif i == 1:
                t_max = short_term.get("TMX_tomorrow", max(d_info['tmps']))
                t_min = short_term.get("TMN_tomorrow", min(d_info['tmps']))
            else:
                t_max, t_min = max(d_info['tmps']), min(d_info['tmps'])

            daily.append({
                "datetime": f"{dt_str}T12:00:00+09:00",
                "condition": self._get_condition(d_info['pm'].get('SKY', '1'), d_info['pm'].get('PTY', '0')),
                "native_temperature": int(t_max),
                "native_templow": int(t_min),
                "native_precipitation_probability": int(max(d_info['pops'])) if d_info['pops'] else 0,
            })
            
            if d_info['am']:
                twice.append({"datetime": f"{dt_str}T09:00:00+09:00", "is_daytime": True, "condition": self._get_condition(d_info['am'].get('SKY', '1'), d_info['am'].get('PTY', '0')), "native_temperature": int(float(d_info['am'].get('TMP', 0))), "native_precipitation_probability": int(d_info['am'].get('POP', 0))})
            if d_info['pm']:
                twice.append({"datetime": f"{dt_str}T15:00:00+09:00", "is_daytime": False, "condition": self._get_condition(d_info['pm'].get('SKY', '1'), d_info['pm'].get('PTY', '0')), "native_temperature": int(float(d_info['pm'].get('TMP', 0))), "native_precipitation_probability": int(d_info['pm'].get('POP', 0))})

        if mid_land and mid_ta:
            for i in range(3, 11):
                d_str = (now + timedelta(days=i)).strftime("%Y%m%d")
                dt_str = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]}"
                
                t_max = mid_ta.get(f"taMax{i}")
                t_min = mid_ta.get(f"taMin{i}")
                wf = mid_land.get(f"wf{i}Pm", mid_land.get(f"wf{i}", "맑음"))
                pop = mid_land.get(f"rnSt{i}Pm", mid_land.get(f"rnSt{i}", 0))

                if t_max is not None and t_min is not None:
                    daily.append({
                        "datetime": f"{dt_str}T12:00:00+09:00",
                        "condition": self._map_mid_sky(str(wf)),
                        "native_temperature": int(t_max),
                        "native_templow": int(t_min),
                        "native_precipitation_probability": int(pop) if pop else 0,
                    })

        short_term["forecast_daily"] = daily
        short_term["forecast_twice_daily"] = twice
        return short_term

    def _sky_to_text(self, sky): return {"1": "맑음", "3": "구름많음", "4": "흐림"}.get(str(sky), "알수없음")
    def _map_mid_sky(self, wf):
        if "비" in wf or "소나기" in wf: return "rainy"
        if "눈" in wf: return "snowy"
        if "구름" in wf or "흐림" in wf: return "cloudy"
        return "sunny"
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
        try: return ["북", "북동", "동", "남동", "남", "남서", "서", "북서"][int((float(vec) + 22.5) // 45) % 8] + "풍"
        except: return "알수없음"

    async def _get_air_quality(self, lat, lon):
        return {"pm10Value": "30", "pm10Grade": "보통", "pm25Value": "15", "pm25Grade": "좋음"}
