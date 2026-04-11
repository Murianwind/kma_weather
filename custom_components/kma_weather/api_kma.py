import logging
import asyncio
import math
import hashlib
from datetime import datetime, timedelta
from urllib.parse import unquote
from zoneinfo import ZoneInfo

_LOGGER = logging.getLogger(__name__)

def _safe_float(v):
    try:
        if v == "" or v is None or v == "-": return None
        return float(v)
    except (TypeError, ValueError): return None

KOR_TO_CONDITION: dict[str, str] = {
    "맑음": "sunny", "구름많음": "partlycloudy", "흐림": "cloudy",
    "비": "rainy", "비/눈": "rainy", "소나기": "rainy", "눈": "snowy",
}

class KMAWeatherAPI:
    def __init__(self, session, api_key, reg_id_temp, reg_id_land, hass=None):
        self.session = session
        self.api_key = unquote(api_key)
        self.reg_id_temp = reg_id_temp
        self.reg_id_land = reg_id_land
        self.hass = hass
        self.tz = ZoneInfo("Asia/Seoul")
        self.lat = self.lon = self.nx = self.ny = None
        self._cached_station = self._cached_lat_lon = self._station_cache_time = None
        self._nominatim_user_agent = self._build_nominatim_user_agent()

        # ── 데이터 소실 방지용 캐시 ──────────────────────────────────────
        # API 호출 실패 / 빈 응답 시 직전 성공 데이터를 재사용한다.
        # tm_fc_dt도 함께 저장해 mid_day_idx 계산 일관성을 보장한다.
        self._cache_forecast_map: dict = {}          # 단기예보 raw map
        self._cache_mid_ta: dict = {}                # 중기기온 item[0]
        self._cache_mid_land: dict = {}              # 중기육상 item[0]
        self._cache_mid_tm_fc_dt: datetime | None = None  # 중기예보 기준 시각

    def _build_nominatim_user_agent(self):
        base = "HomeAssistant-KMA-Weather"
        if self.hass:
            try:
                uuid = getattr(self.hass, "installation_uuid", None)
                if uuid:
                    return f"{base}/{uuid.replace('-', '')[:12]}"
            except Exception:
                pass
        try:
            hashed = hashlib.sha1(self.api_key.encode()).hexdigest()[:12]
            return f"{base}/{hashed}"
        except Exception:
            return base

    async def _fetch(self, url, params, headers=None, timeout=15):
        try:
            async with self.session.get(url, params=params, headers=headers, timeout=timeout) as response:
                response.raise_for_status()
                return await response.json(content_type=None)
        except Exception as err:
            _LOGGER.error("API 호출 실패 (%s): %s", url, err)
        return None

    async def fetch_data(self, lat, lon, nx, ny):
        self.lat, self.lon, self.nx, self.ny = lat, lon, nx, ny
        now = datetime.now(self.tz)
        tasks = [
            self._get_short_term(now), self._get_mid_term(now),
            self._get_air_quality(), self._get_address(lat, lon)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return self._merge_all(now, *[r if not isinstance(r, Exception) else None for r in results])

    async def _get_address(self, lat, lon):
        try:
            url = "https://nominatim.openstreetmap.org/reverse"
            d = await self._fetch(url, params={"format": "json", "lat": lat, "lon": lon, "zoom": 16},
                                  headers={"User-Agent": self._nominatim_user_agent, "Accept-Language": "ko"}, timeout=5)
            if d:
                a = d.get("address", {})
                parts = [a.get("city", a.get("province", "")), a.get("borough", a.get("county", "")),
                         a.get("suburb", a.get("village", ""))]
                return " ".join([p for p in parts if p]).strip()
        except:
            pass
        return f"{lat:.4f}, {lon:.4f}"

    async def _get_air_quality(self):
        try:
            sn = self._cached_station
            if not sn:
                tm_x, tm_y = self._wgs84_to_tm(self.lat, self.lon)
                st_json = await self._fetch(
                    "https://apis.data.go.kr/B552584/MsrstnInfoInqireSvc/getNearbyMsrstnList",
                    {"serviceKey": self.api_key, "returnType": "json", "tmX": f"{tm_x:.2f}", "tmY": f"{tm_y:.2f}"})
                items = st_json.get("response", {}).get("body", {}).get("items", []) if st_json else []
                if not items: return {}
                sn = self._cached_station = items[0].get("stationName")

            air_json = await self._fetch(
                "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty",
                {"serviceKey": self.api_key, "returnType": "json", "stationName": sn, "dataTerm": "daily", "ver": "1.3"})
            ai_list = air_json.get("response", {}).get("body", {}).get("items", []) if air_json else []
            if not ai_list: return {"station": sn}

            ai = ai_list[0]
            return {
                "pm10Value": ai.get("pm10Value"),
                "pm10Grade": self._translate_grade(ai.get("pm10Grade") or ai.get("pm10Grade1h")),
                "pm25Value": ai.get("pm25Value"),
                "pm25Grade": self._translate_grade(ai.get("pm25Grade") or ai.get("pm25Grade1h")),
                "station": sn,
            }
        except Exception as e:
            _LOGGER.error(f"Air quality fetch error: {e}")
            return {}

    def _translate_grade(self, g):
        return {"1": "좋음", "2": "보통", "3": "나쁨", "4": "매우나쁨"}.get(str(g), "정보없음")

    async def _get_short_term(self, now):
        adj = now - timedelta(minutes=10)
        hour = adj.hour
        valid_hours = [h for h in [2, 5, 8, 11, 14, 17, 20, 23] if h <= hour]
        if valid_hours:
            base_h = max(valid_hours)
            base_d = adj.strftime("%Y%m%d")
        else:
            # 자정~01:59: 전날 23시 발표본 사용
            base_h = 23
            base_d = (adj - timedelta(days=1)).strftime("%Y%m%d")
        return await self._fetch(
            "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
            {"serviceKey": self.api_key, "dataType": "JSON", "base_date": base_d,
             "base_time": f"{base_h:02d}00", "nx": self.nx, "ny": self.ny, "numOfRows": 1500})

    def _get_mid_base_dt(self, now) -> datetime:
        """
        중기예보 발표 시각(06시/18시) 기준, API 게시 지연 30분 감안.
        반환값: 실제 사용할 tmFc datetime (Asia/Seoul)
        """
        effective = now - timedelta(minutes=30)
        if effective.hour < 6:
            return (effective - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
        elif effective.hour < 18:
            return effective.replace(hour=6, minute=0, second=0, microsecond=0)
        else:
            return effective.replace(hour=18, minute=0, second=0, microsecond=0)

    async def _get_mid_term(self, now):
        tm_fc_dt = self._get_mid_base_dt(now)
        base = tm_fc_dt.strftime("%Y%m%d%H%M")
        results = await asyncio.gather(
            self._fetch("https://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa",
                        {"serviceKey": self.api_key, "dataType": "JSON", "regId": self.reg_id_temp, "tmFc": base}),
            self._fetch("https://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst",
                        {"serviceKey": self.api_key, "dataType": "JSON", "regId": self.reg_id_land, "tmFc": base})
        )
        return (results[0], results[1], tm_fc_dt)

    def _calculate_apparent_temp(self, temp, reh, wsd):
        t, rh, v = _safe_float(temp), _safe_float(reh), _safe_float(wsd)
        if t is None: return temp
        v_kmh = v * 3.6 if v is not None else 0
        if t <= 10 and v_kmh >= 4.8:
            return round(13.12 + 0.6215 * t - 11.37 * (v_kmh ** 0.16) + 0.3965 * t * (v_kmh ** 0.16), 1)
        if t >= 25 and rh is not None and rh >= 40:
            return round(0.5 * (t + 61.0 + ((t - 68.0) * 1.2) + (rh * 0.094)), 1)
        return t

    @staticmethod
    def kor_to_condition(kor: str | None) -> str | None:
        if kor is None: return None
        return KOR_TO_CONDITION.get(kor)

    def _merge_all(self, now, short_res, mid_res, air_data, address=None):
        weather_data = {
            "TMP": None, "REH": None, "WSD": None, "VEC": None, "POP": None,
            "TMX_today": None, "TMN_today": None, "TMX_tomorrow": None, "TMN_tomorrow": None,
            "rain_start_time": "강수없음", "forecast_daily": [], "forecast_twice_daily": [], "address": address,
        }

        # ── [단기예보] raw 파싱 후 캐시 갱신 ──────────────────────────────
        # 새로 받은 데이터가 유효한 경우만 캐시를 교체한다.
        # 실패(None) 또는 빈 응답이면 직전 캐시를 그대로 유지한다.
        new_forecast_map = {}
        if short_res and "response" in short_res:
            for it in short_res.get("response", {}).get("body", {}).get("items", {}).get("item", []):
                new_forecast_map.setdefault(it["fcstDate"], {}).setdefault(it["fcstTime"], {})[it["category"]] = it["fcstValue"]

        if new_forecast_map:
            # 유효한 데이터 수신 → 캐시 갱신
            self._cache_forecast_map = new_forecast_map
            _LOGGER.debug("단기예보 캐시 갱신: %d일치", len(new_forecast_map))
        else:
            # 실패 또는 빈 응답 → 이전 캐시 재사용
            _LOGGER.warning("단기예보 수신 실패 또는 빈 응답 → 캐시 재사용 (날짜 수: %d)", len(self._cache_forecast_map))

        forecast_map = self._cache_forecast_map

        # ── [중기예보] 언패킹 후 캐시 갱신 ───────────────────────────────
        # mid_res 튜플: (ta응답, land응답, tmFc_datetime)
        # 두 응답 모두 유효한 경우만 캐시를 교체한다.
        if mid_res and isinstance(mid_res, tuple) and len(mid_res) == 3:
            mid_ta_res, mid_land_res, new_tm_fc_dt = mid_res
        else:
            mid_ta_res = mid_res[0] if mid_res else None
            mid_land_res = mid_res[1] if mid_res and len(mid_res) > 1 else None
            new_tm_fc_dt = self._get_mid_base_dt(now)

        new_mid_ta = (mid_ta_res.get("response", {}).get("body", {}).get("items", {}).get("item", [{}])[0]
                      if mid_ta_res else None)
        new_mid_land = (mid_land_res.get("response", {}).get("body", {}).get("items", {}).get("item", [{}])[0]
                        if mid_land_res else None)

        if new_mid_ta and new_mid_land:
            # 두 응답 모두 유효 → 캐시 갱신 (tm_fc_dt도 함께 저장)
            self._cache_mid_ta = new_mid_ta
            self._cache_mid_land = new_mid_land
            self._cache_mid_tm_fc_dt = new_tm_fc_dt
            _LOGGER.debug("중기예보 캐시 갱신: tmFc=%s", new_tm_fc_dt.strftime("%Y%m%d%H%M"))
        else:
            # 실패 또는 빈 응답 → 이전 캐시 재사용
            _LOGGER.warning("중기예보 수신 실패 또는 빈 응답 → 캐시 재사용 (tmFc=%s)",
                            self._cache_mid_tm_fc_dt.strftime("%Y%m%d%H%M") if self._cache_mid_tm_fc_dt else "없음")

        # 이후 로직은 항상 캐시 기반으로 동작
        mid_ta = self._cache_mid_ta
        mid_land = self._cache_mid_land
        # tm_fc_dt: 캐시된 값이 있으면 사용, 없으면 현재 계산값 사용
        tm_fc_dt = self._cache_mid_tm_fc_dt if self._cache_mid_tm_fc_dt else new_tm_fc_dt

        # ── 현재 날씨 파싱 ────────────────────────────────────────────────
        today_str, curr_h = now.strftime("%Y%m%d"), f"{now.hour:02d}00"
        if today_str in forecast_map:
            times = sorted(forecast_map[today_str].keys())
            best_t = next((t for t in times if t >= curr_h), times[-1] if times else None)
            if best_t:
                weather_data.update(forecast_map[today_str][best_t])

        # ── 강수 시작 시각 ─────────────────────────────────────────────────
        for d_str in sorted(forecast_map.keys()):
            rain_times = [t_str for t_str in sorted(forecast_map[d_str].keys())
                          if _safe_float(forecast_map[d_str][t_str].get("PTY", "0")) > 0]
            if rain_times:
                t = rain_times[0]
                month = int(d_str[4:6])
                day = int(d_str[6:8])
                hour = int(t[:2])
                minute = int(t[2:])
                if minute > 0:
                    weather_data["rain_start_time"] = f"{month}월 {day}일 {hour}시 {minute}분"
                else:
                    weather_data["rain_start_time"] = f"{month}월 {day}일 {hour}시"
                break

        # ── 단기/중기 커버 날짜 결정 ──────────────────────────────────────
        # 핵심 조건: 09시와 15시 TMP 데이터가 모두 있어야 단기로 처리한다.
        # 이 두 시각이 없는 날짜(새벽~오전 일부만 있는 경계 날짜)는
        # 중기예보로 처리하여 최고/최저 기온 오류를 방지한다.
        short_term_limit = (now + timedelta(days=3)).strftime("%Y%m%d")
        short_covered_dates = {
            d for d in forecast_map
            if d <= short_term_limit
            and "0900" in forecast_map[d] and "TMP" in forecast_map[d]["0900"]
            and "1500" in forecast_map[d] and "TMP" in forecast_map[d]["1500"]
        }

        twice_daily, daily_forecast = [], []

        for i in range(10):
            target_date = now + timedelta(days=i)
            d_str = target_date.strftime("%Y%m%d")

            t_max, t_min = None, None
            wf_am, wf_pm = "맑음", "맑음"

            if d_str in short_covered_dates:
                # ── 단기예보 처리 ──────────────────────────────────────────
                short_temps = [_safe_float(v.get("TMP")) for v in forecast_map[d_str].values() if "TMP" in v]
                valid_temps = [t for t in short_temps if t is not None]
                t_max = max(valid_temps) if valid_temps else None
                t_min = min(valid_temps) if valid_temps else None
                wf_am = self._get_sky_kor(
                    forecast_map[d_str].get("0900", {}).get("SKY"),
                    forecast_map[d_str].get("0900", {}).get("PTY"))
                wf_pm = self._get_sky_kor(
                    forecast_map[d_str].get("1500", {}).get("SKY"),
                    forecast_map[d_str].get("1500", {}).get("PTY"))
            else:
                # ── 중기예보 처리 ──────────────────────────────────────────
                # mid_day_idx: tm_fc_dt 기준 며칠 후인지 계산
                # target_date는 시각 포함 datetime, tm_fc_dt도 동일하므로
                # .date() 로 날짜만 비교한다.
                mid_day_idx = (target_date.date() - tm_fc_dt.date()).days

                # 중기예보 API 제공 범위: 발표 기준 3~10일
                # 범위 밖(0~2일)은 단기예보로 처리해야 하는데
                # short_covered_dates에도 없다면 데이터 공백 상태.
                # 이 경우 단기 캐시에서 직접 구할 수 있는 값을 사용한다.
                if mid_day_idx < 3:
                    # 단기 캐시에서 최선값 추출 (09시/15시 없어도 있는 값으로)
                    if d_str in forecast_map:
                        short_temps = [_safe_float(v.get("TMP")) for v in forecast_map[d_str].values() if "TMP" in v]
                        valid_temps = [t for t in short_temps if t is not None]
                        t_max = max(valid_temps) if valid_temps else None
                        t_min = min(valid_temps) if valid_temps else None
                        # 가장 가까운 낮 시각 데이터로 날씨 대표값 결정
                        rep_t = min(
                            [t for t in forecast_map[d_str].keys()],
                            key=lambda t: abs(int(t[:2]) - 12),
                            default=None
                        )
                        if rep_t:
                            wf_am = self._get_sky_kor(
                                forecast_map[d_str][rep_t].get("SKY"),
                                forecast_map[d_str][rep_t].get("PTY"))
                            wf_pm = wf_am
                    _LOGGER.debug(
                        "경계 날짜 단기캐시 사용 i=%d date=%s mid_day_idx=%d t_max=%s t_min=%s",
                        i, d_str, mid_day_idx, t_max, t_min)
                else:
                    t_max = _safe_float(mid_ta.get(f"taMax{mid_day_idx}"))
                    t_min = _safe_float(mid_ta.get(f"taMin{mid_day_idx}"))
                    wf_am = self._translate_mid_condition_kor(
                        mid_land.get(f"wf{mid_day_idx}Am") or mid_land.get(f"wf{mid_day_idx}"))
                    wf_pm = self._translate_mid_condition_kor(
                        mid_land.get(f"wf{mid_day_idx}Pm") or mid_land.get(f"wf{mid_day_idx}"))
                    _LOGGER.debug(
                        "중기예보 i=%d date=%s tm_fc_dt=%s mid_day_idx=%d t_max=%s t_min=%s",
                        i, d_str, tm_fc_dt.strftime("%Y%m%d%H%M"), mid_day_idx, t_max, t_min)

            # 오늘(i=0), 내일(i=1) 날씨 정보 저장
            if i == 0:
                weather_data["wf_am_today"] = wf_am
                weather_data["wf_pm_today"] = wf_pm
                weather_data["_raw_today_max"] = t_max
                weather_data["_raw_today_min"] = t_min
            elif i == 1:
                weather_data.update({
                    "TMX_tomorrow": t_max,
                    "TMN_tomorrow": t_min,
                    "wf_am_tomorrow": wf_am,
                    "wf_pm_tomorrow": wf_pm,
                })

            for is_am in [True, False]:
                # 오늘 오전: 현재 12시 이후라면 오전 슬롯 스킵
                if i == 0 and is_am and now.hour >= 12:
                    continue
                twice_daily.append({
                    "datetime": target_date.replace(hour=9 if is_am else 21, minute=0, second=0, microsecond=0).isoformat(),
                    "is_daytime": is_am,
                    "native_temperature": t_max,
                    "native_templow": t_min,
                    "condition": self.kor_to_condition(wf_am if is_am else wf_pm),
                    "_day_index": i,
                })

            daily_forecast.append({
                "datetime": target_date.replace(hour=12, minute=0, second=0, microsecond=0).isoformat(),
                "native_temperature": t_max,
                "native_templow": t_min,
                "condition": self.kor_to_condition(wf_pm),
                "_day_index": i,
            })

        weather_data.update({"forecast_twice_daily": twice_daily, "forecast_daily": daily_forecast})
        kor_now = self._get_sky_kor(weather_data.get("SKY"), weather_data.get("PTY"))
        weather_data.update({
            "current_condition_kor": kor_now,
            "current_condition": self.kor_to_condition(kor_now),
            "apparent_temp": self._calculate_apparent_temp(
                weather_data.get("TMP"), weather_data.get("REH"), weather_data.get("WSD")),
        })
        if weather_data.get("VEC"):
            weather_data["VEC_KOR"] = self._get_vec_kor(weather_data["VEC"])
        return {"weather": weather_data, "air": air_data or {}, "raw_forecast": forecast_map}

    def _translate_mid_condition_kor(self, wf: str) -> str:
        wf = str(wf or "맑음")
        if "비" in wf: return "비"
        if "눈" in wf: return "눈"
        if "구름많음" in wf: return "구름많음"
        if "흐림" in wf: return "흐림"
        return "맑음"

    def _get_sky_kor(self, sky, pty):
        p, s = str(pty or "0"), str(sky or "1")
        if p in ["1", "2", "3", "4"]:
            return {"1": "비", "2": "비/눈", "3": "눈", "4": "소나기"}.get(p, "비")
        return "맑음" if s == "1" else ("구름많음" if s == "3" else "흐림")

    def _get_vec_kor(self, vec):
        v = _safe_float(vec)
        if v is None: return None
        if 22.5 <= v < 67.5: return "북동"
        elif 67.5 <= v < 112.5: return "동"
        elif 112.5 <= v < 157.5: return "남동"
        elif 157.5 <= v < 202.5: return "남"
        elif 202.5 <= v < 247.5: return "남서"
        elif 247.5 <= v < 292.5: return "서"
        elif 292.5 <= v < 337.5: return "북서"
        return "북"

    def _translate_mid_condition(self, wf): return self.kor_to_condition(self._translate_mid_condition_kor(wf))
    def _get_condition(self, s, p): return self.kor_to_condition(self._get_sky_kor(s, p))

    def _wgs84_to_tm(self, lat, lon):
        a, f = 6378137.0, 1 / 298.257222101
        e2 = 2 * f - f ** 2
        lat0, lon0 = math.radians(38.0), math.radians(127.0)
        phi, lam = math.radians(lat), math.radians(lon)
        N = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
        T, C, A = math.tan(phi) ** 2, e2 / (1 - e2) * math.cos(phi) ** 2, math.cos(phi) * (lam - lon0)

        def M(p):
            return a * ((1 - e2/4 - 3*e2**2/64 - 5*e2**3/256) * p
                        - (3*e2/8 + 3*e2**2/32 + 45*e2**3/1024) * math.sin(2*p)
                        + (15*e2**2/256 + 45*e2**3/1024) * math.sin(4*p)
                        - (35*e2**3/3072) * math.sin(6*p))

        return (200000.0 + 1.0 * N * (A + (1-T+C)*A**3/6 + (5-18*T+T**2+72*C-58*(e2/(1-e2)))*A**5/120),
                500000.0 + 1.0 * (M(phi) - M(lat0) + N*math.tan(phi)*(
                    A**2/2 + (5-T+9*C+4*C**2)*A**4/24 + (61-58*T+T**2+600*C-330*(e2/(1-e2)))*A**6/720)))
