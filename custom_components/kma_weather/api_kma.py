import logging
import asyncio
import math
import aiohttp
import hashlib
from datetime import datetime, timedelta
from urllib.parse import unquote
from zoneinfo import ZoneInfo

_LOGGER = logging.getLogger(__name__)


def _safe_float(v):
    try:
        if v == "" or v is None or v == "-":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# ── 한글 → HA 표준 영문 매핑 (단일 정의) ──────────────────────────────────
KOR_TO_CONDITION: dict[str, str] = {
    "맑음":    "sunny",
    "구름많음": "partlycloudy",
    "흐림":    "cloudy",
    "비":      "rainy",
    "비/눈":   "rainy",
    "소나기":  "rainy",
    "눈":      "snowy",
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

        # 측정소 캐싱 정보
        self._cached_station = None
        self._cached_lat_lon = None
        self._station_cache_time = None

        # Nominatim User-Agent 생성
        self._nominatim_user_agent = self._build_nominatim_user_agent()

    def _build_nominatim_user_agent(self):
        """Nominatim 정책을 준수하는 고유한 User-Agent 생성"""
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
        """세분화된 예외 처리를 적용한 API 호출 헬퍼"""
        try:
            async with self.session.get(
                url, params=params, headers=headers, timeout=timeout
            ) as response:
                response.raise_for_status()
                return await response.json(content_type=None)

        except asyncio.TimeoutError:
            _LOGGER.error(
                "API 타임아웃 (%s): 응답 시간이 %s초를 초과했습니다.",
                url,
                timeout,
            )
        except aiohttp.ClientError as err:
            _LOGGER.error("HTTP/연결 오류 (%s): %s", url, err)
        except ValueError as err:
            _LOGGER.error("JSON 파싱 오류 (%s): %s", url, err)
        except Exception as err:
            _LOGGER.error("알 수 없는 API 오류 (%s): %s", url, err)
        return None

    async def fetch_data(self, lat, lon, nx, ny):
        self.lat, self.lon, self.nx, self.ny = lat, lon, nx, ny
        now = datetime.now(self.tz)
        tasks = [
            self._get_short_term(now),
            self._get_mid_term(now),
            self._get_air_quality(),
            self._get_address(lat, lon),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        short_res, mid_res, air_data, address = [
            r if not isinstance(r, Exception) else None for r in results
        ]
        return self._merge_all(now, short_res, mid_res, air_data, address)

    async def _get_address(self, lat, lon):
        try:
            url = "https://nominatim.openstreetmap.org/reverse"
            params = {
                "format": "json",
                "lat": lat,
                "lon": lon,
                "zoom": 16,
            }

            headers = {
                "User-Agent": self._nominatim_user_agent,
                "Accept-Language": "ko",
            }

            d = await self._fetch(
                url, params=params, headers=headers, timeout=5
            )

            if d:
                a = d.get("address", {})
                parts = [
                    a.get("city", a.get("province", "")),
                    a.get("borough", a.get("county", "")),
                    a.get("suburb", a.get("village", "")),
                ]
                return " ".join([p for p in parts if p]).strip()

            return f"{lat:.4f}, {lon:.4f}"
        except Exception:
            return f"{lat:.4f}, {lon:.4f}"

    async def _get_air_quality(self):
        try:
            now = datetime.now(self.tz)
            sn = None

            loc_changed = True
            if self._cached_lat_lon:
                dist = math.sqrt(
                    (self.lat - self._cached_lat_lon[0]) ** 2
                    + (self.lon - self._cached_lat_lon[1]) ** 2
                )
                if dist < 0.01:
                    loc_changed = False

            if (
                self._cached_station
                and self._station_cache_time
                and (now - self._station_cache_time).total_seconds() < 600
                and not loc_changed
            ):
                sn = self._cached_station
            else:
                tm_x, tm_y = self._wgs84_to_tm(self.lat, self.lon)
                url_st = "https://apis.data.go.kr/B552584/MsrstnInfoInqireSvc/getNearbyMsrstnList"
                params_st = {
                    "serviceKey": self.api_key,
                    "returnType": "json",
                    "tmX": f"{tm_x:.2f}",
                    "tmY": f"{tm_y:.2f}",
                }

                st_json = await self._fetch(
                    url_st, params=params_st, timeout=10
                )
                if not st_json:
                    return {}

                items = (
                    st_json.get("response", {})
                    .get("body", {})
                    .get("items", [])
                )
                if not items:
                    return {}

                sn = items[0].get("stationName")
                self._cached_station = sn
                self._cached_lat_lon = (self.lat, self.lon)
                self._station_cache_time = now

            url_data = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty"
            params_data = {
                "serviceKey": self.api_key,
                "returnType": "json",
                "stationName": sn,
                "dataTerm": "daily",
                "ver": "1.3",
            }

            air_json = await self._fetch(
                url_data, params=params_data, timeout=10
            )
            if not air_json:
                return {"station": sn}

            ai_list = (
                air_json.get("response", {})
                .get("body", {})
                .get("items", [])
            )
            if not ai_list:
                return {"station": sn}

            ai = ai_list[0]
            return {
                "pm10Value": ai.get("pm10Value"),
                "pm10Grade": self._translate_grade(ai.get("pm10Grade")),
                "pm25Value": ai.get("pm25Value"),
                "pm25Grade": self._translate_grade(ai.get("pm25Grade")),
                "station": sn,
            }
        except Exception as e:
            _LOGGER.error(f"Air quality fetch error: {e}")
            return {}

    def _translate_grade(self, g):
        return {
            "1": "좋음",
            "2": "보통",
            "3": "나쁨",
            "4": "매우나쁨",
        }.get(str(g), "정보없음")

    async def _get_short_term(self, now):
        adj = now - timedelta(minutes=10)
        hour = adj.hour

        base_hours = [2, 5, 8, 11, 14, 17, 20, 23]
        valid_hours = [h for h in base_hours if h <= hour]

        if valid_hours:
            base_h = max(valid_hours)
            base_d = adj.strftime("%Y%m%d")
        else:
            base_h = 23
            base_d = (adj - timedelta(days=1)).strftime("%Y%m%d")

        _LOGGER.debug("단기예보 호출: base_date=%s, base_time=%02d00", base_d, base_h)

        url = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        params = {
            "serviceKey": self.api_key,
            "dataType": "JSON",
            "base_date": base_d,
            "base_time": f"{base_h:02d}00",
            "nx": self.nx,
            "ny": self.ny,
            "numOfRows": 1500,
            "pageNo": 1,
        }
        return await self._fetch(url, params=params, timeout=15)

    async def _get_mid_term(self, now):
        base = (now if now.hour >= 18 else (now if now.hour >= 6 else now - timedelta(days=1))).strftime("%Y%m%d") + ("0600" if 6 <= now.hour < 18 else "1800")
        url_ta = "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa"
        url_land = "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst"

        return await asyncio.gather(
            self._fetch(url_ta, {"serviceKey": self.api_key, "dataType": "JSON", "regId": self.reg_id_temp, "tmFc": base}, timeout=15),
            self._fetch(url_land, {"serviceKey": self.api_key, "dataType": "JSON", "regId": self.reg_id_land, "tmFc": base}, timeout=15)
        )

    def _calculate_apparent_temp(self, temp, reh, wsd):
        try:
            t = _safe_float(temp)
            if t is None: return None
            v = _safe_float(wsd)
            v_kmh = v * 3.6 if v is not None else 0
            if t <= 10 and v_kmh >= 4.8:
                return round(13.12 + 0.6215 * t - 11.37 * (v_kmh ** 0.16) + 0.3965 * t * (v_kmh ** 0.16), 1)
            rh = _safe_float(reh)
            if t >= 25 and rh is not None and rh >= 40:
                hi = 0.5 * (t + 61.0 + ((t - 68.0) * 1.2) + (rh * 0.094))
                return round(hi, 1)
            return t
        except (TypeError, ValueError):
            return temp

    # ── [수정] 한글 → HA 표준 영문 변환 (모듈 상단 KOR_TO_CONDITION 사용) ──
    @staticmethod
    def kor_to_condition(kor: str | None) -> str | None:
        """한글 날씨 상태를 HA 표준 영문 condition 으로 변환합니다.
        
        두 값의 동기화를 위한 단일 변환 지점입니다.
        current_condition 은 항상 이 메서드를 통해 current_condition_kor 로부터 파생됩니다.
        """
        if kor is None:
            return None
        return KOR_TO_CONDITION.get(kor)

    def _merge_all(self, now, short_res, mid_res, air_data, address=None):
        weather_data = {
            "TMP": None, "REH": None, "WSD": None, "VEC": None, "POP": None, "PTY": None, "SKY": None,
            "TMX_today": None, "TMN_today": None, "TMX_tomorrow": None, "TMN_tomorrow": None,
            "wf_am_today": None, "wf_pm_today": None, "wf_am_tomorrow": None, "wf_pm_tomorrow": None,
            "apparent_temp": None, "rain_start_time": "강수없음", "address": address, "현재 위치": address,
            "forecast_twice_daily": [],
            "debug_nx": self.nx, "debug_ny": self.ny, "debug_lat": self.lat, "debug_lon": self.lon,
            "debug_reg_id_temp": self.reg_id_temp, "debug_reg_id_land": self.reg_id_land
        }

        forecast_map = {}
        if short_res and "response" in short_res:
            items = short_res.get("response", {}).get("body", {}).get("items", {}).get("item", [])
            for it in items:
                d, t, cat, val = it["fcstDate"], it["fcstTime"], it["category"], it["fcstValue"]
                forecast_map.setdefault(d, {}).setdefault(t, {})[cat] = val

            today_str = now.strftime("%Y%m%d")
            tomorrow_str = (now + timedelta(days=1)).strftime("%Y%m%d")

            for target_date, prefix in [(today_str, "today"), (tomorrow_str, "tomorrow")]:
                if target_date in forecast_map:
                    day_data = forecast_map[target_date]
                    tmps = [_safe_float(v.get("TMP")) for v in day_data.values() if "TMP" in v]
                    if tmps:
                        weather_data[f"TMX_{prefix}"] = max(tmps)
                        weather_data[f"TMN_{prefix}"] = min(tmps)

                    am_val = day_data.get("0900", {})
                    pm_val = day_data.get("1500", {})
                    weather_data[f"wf_am_{prefix}"] = self._get_sky_kor(am_val.get("SKY"), am_val.get("PTY"))
                    weather_data[f"wf_pm_{prefix}"] = self._get_sky_kor(pm_val.get("SKY"), pm_val.get("PTY"))

            curr_h = f"{now.hour:02d}00"
            if today_str in forecast_map:
                available_times = sorted(forecast_map[today_str].keys())
                best_time = None

                future_times = [t for t in available_times if t >= curr_h]
                if future_times:
                    best_time = future_times[0]
                elif available_times:
                    best_time = available_times[-1]

                if best_time:
                    _LOGGER.debug("현재 날씨 데이터 슬롯 선택: %s (현재 시각: %s)", best_time, curr_h)
                    weather_data.update(forecast_map[today_str][best_time])

            days_ko = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
            found_rain = False
            for d_str in sorted(forecast_map.keys()):
                for t_str in sorted(forecast_map[d_str].keys()):
                    if d_str == today_str and t_str <= curr_h: continue
                    if forecast_map[d_str][t_str].get("PTY", "0") != "0" and not found_rain:
                        dt = datetime.strptime(d_str + t_str, "%Y%m%d%H%M")
                        weekday = days_ko[dt.weekday()]
                        time_label = f"{dt.hour}시" + (f" {dt.minute}분" if dt.minute > 0 else "")
                        weather_data["rain_start_time"] = f"{dt.month}월 {dt.day}일 {weekday} {time_label}"
                        found_rain = True
                        break
                if found_rain: break

        twice_daily = []
        mid_ta = mid_res[0].get("response",{}).get("body",{}).get("items",{}).get("item",[{}])[0] if mid_res and mid_res[0] else {}
        mid_land = mid_res[1].get("response",{}).get("body",{}).get("items",{}).get("item",[{}])[0] if mid_res and mid_res[1] else {}

        SHORT_SLOTS = ["0000", "0300", "0600", "0900", "1200", "1500", "1800", "2100"]
        DAYTIME_SLOTS = [s for s in SHORT_SLOTS if int(s) < 1200]
        NIGHTTIME_SLOTS = [s for s in SHORT_SLOTS if int(s) >= 1200]

        curr_hhmm = f"{now.hour:02d}{now.minute:02d}"

        for i in range(10):
            target_date = now + timedelta(days=i)
            d_str = target_date.strftime("%Y%m%d")

            for is_am in [True, False]:
                if i == 0:
                    candidate_slots = DAYTIME_SLOTS if is_am else NIGHTTIME_SLOTS
                    chosen_slot = next(
                        (s for s in candidate_slots if s > curr_hhmm),
                        None
                    )
                    if chosen_slot is None:
                        continue
                    short_hour_data = forecast_map.get(d_str, {}).get(chosen_slot, {})
                    if not short_hour_data:
                        continue
                    slot_hour = int(chosen_slot[:2])
                    dt_iso = target_date.replace(hour=slot_hour, minute=0, second=0, microsecond=0).isoformat()
                else:
                    hour = 9 if is_am else 21
                    dt_iso = target_date.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()
                    short_hour_data = forecast_map.get(d_str, {}).get(f"{hour:02d}00", {})

                tmps = [_safe_float(v.get("TMP")) for v in forecast_map.get(d_str, {}).values() if "TMP" in v]

                if short_hour_data:
                    # ── [수정] 예보 condition 도 kor_to_condition 으로 파생 ──
                    kor = self._get_sky_kor(short_hour_data.get("SKY"), short_hour_data.get("PTY"))
                    twice_daily.append({
                        "datetime": dt_iso, "is_daytime": is_am,
                        "native_temperature": max(tmps) if tmps else None,
                        "native_templow": min(tmps) if tmps else None,
                        "native_precipitation_probability": _safe_float(short_hour_data.get("POP")),
                        "condition": self.kor_to_condition(kor),
                    })
                elif i >= 2:
                    wf = mid_land.get(f"wf{i}Am" if is_am else f"wf{i}Pm") or mid_land.get(f"wf{i}")
                    if wf:
                        kor = self._translate_mid_condition_kor(wf)
                        twice_daily.append({
                            "datetime": dt_iso, "is_daytime": is_am,
                            "native_temperature": _safe_float(mid_ta.get(f"taMax{i}")),
                            "native_templow": _safe_float(mid_ta.get(f"taMin{i}")),
                            "native_precipitation_probability": _safe_float(mid_land.get(f"rnSt{i}Am" if is_am else f"rnSt{i}Pm")),
                            "condition": self.kor_to_condition(kor),
                        })

        weather_data["forecast_twice_daily"] = twice_daily

        if weather_data.get("VEC"):
            kor_vec = self._get_vec_kor(weather_data["VEC"])
            weather_data["VEC_KOR"] = kor_vec
            weather_data["현재 풍향"] = kor_vec

        weather_data["apparent_temp"] = self._calculate_apparent_temp(
            weather_data.get("TMP"), weather_data.get("REH"), weather_data.get("WSD")
        )

        # ── [수정] current_condition_kor 를 먼저 결정하고,
        #          current_condition 은 항상 kor_to_condition 으로 파생 ──────
        kor_now = self._get_sky_kor(weather_data.get("SKY"), weather_data.get("PTY"))
        weather_data["current_condition_kor"] = kor_now
        weather_data["current_condition"] = self.kor_to_condition(kor_now)

        return {"weather": weather_data, "air": air_data or {}, "raw_forecast": forecast_map}

    # ── [수정] 중기예보 한글 변환 메서드 추가 (kor_to_condition 과 연동) ────
    def _translate_mid_condition_kor(self, wf: str) -> str:
        """중기예보 날씨 문자열을 KOR_TO_CONDITION 키에 맞는 한글로 정규화합니다."""
        wf = str(wf)
        if "비" in wf:      return "비"
        if "눈" in wf:      return "눈"
        if "구름많음" in wf: return "구름많음"
        if "흐림" in wf:    return "흐림"
        return "맑음"

    def _translate_mid_condition(self, wf):
        """하위 호환성 유지용 — 내부적으로 kor_to_condition 을 경유합니다."""
        return self.kor_to_condition(self._translate_mid_condition_kor(wf))

    def _get_condition(self, s, p):
        """하위 호환성 유지용 — kor_to_condition 을 경유합니다."""
        return self.kor_to_condition(self._get_sky_kor(s, p))

    def _get_sky_kor(self, sky, pty):
        p, s = str(pty or "0"), str(sky or "1")
        if p in ["1", "2", "3", "4"]: return {"1":"비","2":"비/눈","3":"눈","4":"소나기"}.get(p, "비")
        return "맑음" if s == "1" else ("구름많음" if s == "3" else "흐림")

    def _get_vec_kor(self, vec):
        v = _safe_float(vec)
        if v is None: return None
        if 22.5 <= v < 67.5: return "북동"
        if 67.5 <= v < 112.5: return "동"
        if 112.5 <= v < 157.5: return "남동"
        if 157.5 <= v < 202.5: return "남"
        if 202.5 <= v < 247.5: return "남서"
        if 247.5 <= v < 292.5: return "서"
        if 292.5 <= v < 337.5: return "북서"
        return "북"

    def _wgs84_to_tm(self, lat, lon):
        a, f = 6378137.0, 1 / 298.257222101
        e2 = 2*f - f**2
        lat0, lon0 = math.radians(38.0), math.radians(127.0)
        phi, lam = math.radians(lat), math.radians(lon)
        N = a / math.sqrt(1 - e2 * math.sin(phi)**2)
        T, C, A = math.tan(phi)**2, e2 / (1 - e2) * math.cos(phi)**2, math.cos(phi) * (lam - lon0)
        def M(p): return a * ((1 - e2/4 - 3*e2**2/64 - 5*e2**3/256) * p - (3*e2/8 + 3*e2**2/32 + 45*e2**3/1024) * math.sin(2*p) + (15*e2**2/256 + 45*e2**3/1024) * math.sin(4*p) - (35*e2**3/3072) * math.sin(6*p))
        return 200000.0 + 1.0 * N * (A + (1-T+C)*A**3/6 + (5-18*T+T**2+72*C-58*(e2/(1-e2)))*A**5/120), 500000.0 + 1.0 * (M(phi) - M(lat0) + N*math.tan(phi)*(A**2/2 + (5-T+9*C+4*C**2)*A**4/24 + (61-58*T+T**2+600*C-330*(e2/(1-e2)))*A**6/720))
