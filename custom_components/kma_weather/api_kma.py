from __future__ import annotations
import logging
import asyncio
import math
import re
import json
import hashlib
import aiohttp
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo
from homeassistant.core import HomeAssistant
from .const import haversine as _haversine_fn, safe_float as _safe_float

_LOGGER = logging.getLogger(__name__)

# ── 상수는 const.py에서 import ────────────────────────────────────────────────
from .const import (
    WARN_TYPE_MAP        as _WARN_TYPE_MAP,
    API_SERVICES         as _API_SERVICES,
    UNSUBSCRIBED_CODES   as _UNSUBSCRIBED_CODES,
    POLLEN_GRADE         as _POLLEN_GRADE,
    POLLEN_SEASONS       as _POLLEN_SEASONS,
)
# 꽃가루 종류 목록 - POLLEN_SEASONS 키에서 파생하여 중복 제거
_POLLEN_KINDS: tuple[str, ...] = tuple(_POLLEN_SEASONS.keys())
_POLLEN_GRADE_RANK = {"좋음": 1, "보통": 2, "나쁨": 3, "매우나쁨": 4}


KOR_TO_CONDITION: dict[str, str] = {
    "맑음": "sunny",
    "구름많음": "partlycloudy",
    "흐림": "cloudy",
    "비": "rainy",
    "비/눈": "snowy-rainy",
    "눈": "snowy",
    "소나기": "pouring",
    "빗방울": "rainy",
    "빗방울/눈날림": "snowy-rainy",
    "눈날림": "snowy",
    "구름많고 비": "rainy",
    "구름많고 눈": "snowy",
    "구름많고 비/눈": "snowy-rainy",
    "구름많고 소나기": "pouring",
    "흐리고 비": "rainy",
    "흐리고 눈": "snowy",
    "흐리고 비/눈": "snowy-rainy",
    "흐리고 소나기": "pouring",
}

class KMAWeatherAPI:
    def __init__(self, session: aiohttp.ClientSession, api_key: str, hass: HomeAssistant | None = None) -> None:
        self.session = session
        self._raw_api_key = api_key  # 사용자가 입력한 인코딩된 키 저장
        self.api_key = unquote(api_key)
        self.hass = hass
        self.tz = ZoneInfo("Asia/Seoul")
        self.lat = self.lon = self.nx = self.ny = None

        # 에어코리아 측정소 캐시
        self._cached_station: str | None = None
        self._cached_station_lat: float | None = None
        self._cached_station_lon: float | None = None

        self._nominatim_user_agent = self._build_nominatim_user_agent()

        self._cache_forecast_map: dict = {}
        self._cache_mid_ta: dict = {}
        self._cache_mid_land: dict = {}
        self._cache_mid_tm_fc_dt: datetime | None = None

        # API 미신청 알림 중복 방지
        self._notified_unsubscribed: set[str] = set()

        # 승인 확인된 API (실제 데이터 호출 대상)
        self._approved_apis: set[str] = set()

        # 승인 여부 미확인 또는 미신청/만료 API
        # → 매 업데이트마다 호출해서 확인, 미신청이면 로그 출력
        # → 승인 확인 시 _approved_apis로 이동
        self._pending_apis: set[str] = {"air", "station", "warning", "pollen"}

        # API 호출 카운터 콜백 (coordinator에서 주입)
        # coordinator가 없는 단독 테스트 환경에서는 None
        self._call_counter_ref = None

        # 꽃가루 캐시: 종류별(pine/oak/grass) 독립 관리
        # 각 종류: {"today": 등급, "tomorrow": 등급, "today_date": YYYYMMDD, "tomorrow_date": YYYYMMDD}
        self._pollen_cache: dict[str, dict] = {
            "pine":  {"today": None, "tomorrow": None, "today_date": None, "tomorrow_date": None},
            "oak":   {"today": None, "tomorrow": None, "today_date": None, "tomorrow_date": None},
            "grass": {"today": None, "tomorrow": None, "today_date": None, "tomorrow_date": None},
        }

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

    # ── 꽃가루 지역코드 룩업 ────────────────────────────────────────────────

    # ── API 미신청 감지 및 알림 ─────────────────────────────────────────────
    def _check_unsubscribed(self, service_key: str, result_code: str) -> bool:
        if result_code not in _UNSUBSCRIBED_CODES:
            return False

        # 미신청/만료 감지 시 _approved_apis에서 제거 → 관련 센서 unavailable 전환
        if service_key in self._approved_apis:
            _LOGGER.warning("API 만료/중지 감지 [%s]: resultCode=%s → _approved_apis에서 제거", service_key, result_code)
            self._approved_apis.discard(service_key)

        # pollen 중지 시 캐시 즉시 무효화 → 재활성화 후 새 데이터 호출
        if service_key == "pollen":
            for _k in _POLLEN_KINDS:
                self._pollen_cache[_k] = {"today": None, "tomorrow": None,
                                           "today_date": None, "tomorrow_date": None}

        # _approved에서 제거된 경우 _pending에 다시 추가 → 다음 업데이트에서 재확인
        if service_key not in self._pending_apis:
            self._pending_apis.add(service_key)

        name, url = _API_SERVICES.get(service_key, (service_key, ""))

        # WARNING 로그는 매번 출력 (사용자가 로그에서 확인 가능)
        _LOGGER.warning("API 미신청 감지 [%s]: resultCode=%s → %s", service_key, result_code, url)

        # HA 알림(persistent_notification)은 최초 1회만 (중복 방지)
        if service_key in self._notified_unsubscribed:
            return True

        self._notified_unsubscribed.add(service_key)

        msg = (
            f"**기상청 스마트 날씨 — API 미신청 감지**\n\n"
            f"**{name}** 서비스가 활용신청되지 않았거나 접근이 거부되었습니다 "
            f"(오류코드: {result_code}).\n\n"
            f"아래 링크에서 활용신청 후 승인을 기다려 주세요:\n"
            f"[{name} 신청하기]({url})\n\n"
            f"신청 후 HA를 재시작하거나 수동 업데이트를 누르면 정상 작동합니다."
        )
        if self.hass:
            try:
                self.hass.components.persistent_notification.async_create(
                    message=msg,
                    title="기상청 스마트 날씨: API 신청 필요",
                    notification_id=f"kma_weather_unsubscribed_{service_key}",
                )
            except Exception as e:
                _LOGGER.debug("persistent_notification 발송 실패: %s", e)

        return True

    def _mark_approved(self, service_key: str) -> None:
        if service_key not in self._approved_apis:
            _LOGGER.info("API 승인 확인 [%s] → 관련 센서가 추가됩니다", service_key)
            self._approved_apis.add(service_key)
        # 승인됐으므로 미확인 목록에서 제거
        self._pending_apis.discard(service_key)
        self._notified_unsubscribed.discard(service_key)

    def _mask_key(self, msg: str) -> str:
        """로그 메시지 내의 API 키를 마스킹 처리한다."""
        msg_str = str(msg)
        # URL 내의 serviceKey 파라미터 값을 우선적으로 마스킹 (인코딩 변종 대응)
        if "serviceKey=" in msg_str:
            msg_str = re.sub(r"serviceKey=[^&'\" ]*", "serviceKey=********", msg_str)
            
        # 인코딩된 키와 디코딩된 키 모두 마스킹
        for key in (self._raw_api_key, self.api_key):
            if key and len(key) > 5 and key in msg_str:
                msg_str = msg_str.replace(key, "********")
        return msg_str

    # URL → 카운팅 키 매핑
    _CALL_COUNT_KEY: dict[str, str] = {
        "VilageFcstInfoService_2.0": "단기예보",
        "MidFcstInfoService":        "중기예보",
        "MsrstnInfoInqireSvc":       "에어코리아_측정소",
        "ArpltnInforInqireSvc":      "에어코리아_대기",
        "WthrWrnInfoService":        "기상특보",
        "HealthWthrIdxServiceV3":    "꽃가루",
    }

    async def _fetch(self, url, params, headers=None, timeout=15):
        # URL에서 서비스 키 추출 → coordinator 카운터 증가
        if self.hass is not None:
            for fragment, key in self._CALL_COUNT_KEY.items():
                if fragment in url:
                    if hasattr(self, "_call_counter_ref") and self._call_counter_ref is not None:
                        self._call_counter_ref(key)
                    break

        for attempt in range(2):  # 일시적 오류(429, 5xx) 발생 시 최대 1회 재시도
            try:
                async with self.session.get(
                    url, params=params, headers=headers, timeout=timeout
                ) as response:
                    if response.status in (429, 500, 502, 503, 504):
                        if attempt == 0:
                            _LOGGER.warning("API %s 발생 (트래픽 초과). 10초 후 재시도합니다. (%s)", response.status, self._mask_key(url))
                            await asyncio.sleep(10.0)
                            continue
                        return {"_http_error": str(response.status)}

                    if response.status in (401, 403):
                        _LOGGER.debug("API 인증 실패 (%s): HTTP %s", self._mask_key(url), response.status)
                        return {"_http_error": str(response.status)}
                    if response.status == 404:
                        _LOGGER.debug("API 404 응답 (%s) - 미신청 또는 중지된 서비스", self._mask_key(url))
                        return {"_http_error": "404"}

                    response.raise_for_status()
                    text = await response.text()
                    try:
                        return json.loads(text)
                    except (json.JSONDecodeError, ValueError):
                        _LOGGER.error("API 응답 파싱 실패 (%s): 알 수 없는 형식", self._mask_key(url))
                        return None
            except Exception as err:
                # 재시도 대상 에러가 아니거나, 이미 재시도를 한 경우에만 에러 로그 출력
                is_retryable = any(code in str(err) for code in ("429", "500", "502", "503", "504"))
                if attempt == 1 or not is_retryable:
                    _LOGGER.error("API 호출 실패 (%s): %s", self._mask_key(url), self._mask_key(err))
                    break
                await asyncio.sleep(3.0)
        return None

    def _extract_result_code(self, data: dict | None) -> str | None:
        if not data:
            return None
        # HTTP 오류 응답 (401/403/404) → 미신청 코드로 매핑
        if "_http_error" in data:
            if data["_http_error"] == "429": return "429"
            return "30"  # 미신청 코드와 동일하게 처리
        return (
            data.get("response", {})
                .get("header", {})
                .get("resultCode")
        )

    # ── fetch_data ───────────────────────────────────────────────────────────
    async def fetch_data(
        self,
        lat: float, lon: float,
        nx: int, ny: int,
        reg_id_temp: str, reg_id_land: str,
        warn_area_code: str | None,
        pollen_area_no: str,
        pollen_area_name: str,
    ) -> dict | None:
        self.lat, self.lon, self.nx, self.ny = lat, lon, nx, ny
        now = datetime.now(self.tz)

        async def _skip_coro(default):
            return default

        # 승인 여부 판단:
        # _approved_apis에 있음 → 실제 데이터 호출 (_get_* 내부 로직 정상 실행)
        # _pending_apis에 있음 → 승인 확인용 경량 호출 (_get_* 내부에서 resultCode만 확인)
        # 둘 다 없음 → 건너뜀 (승인 후 _pending 제거됐으나 _approved에도 없는 이상 상태)
        def _should_call(key: str) -> bool:
            """승인 여부 판단 헬퍼 함수"""
            return key in self._approved_apis or key in self._pending_apis
        # 변수 초기화 (에디터의 '정의되지 않았을 수 있음' 경고 방지)
        short_res = mid_res = air_data = address = warning = pollen_data = None

        try:
            # ── API 순차 호출 (429 Too Many Requests 방지) ──
            short_res = await self._get_short_term(now)
            await asyncio.sleep(2.0)  # 호출 간격 2초로 상향 (429 방어)

            mid_res = await self._get_mid_term(now, reg_id_temp, reg_id_land)
            await asyncio.sleep(2.0)

            if _should_call("air") or _should_call("station"):
                air_data = await self._get_air_quality(lat, lon)
                await asyncio.sleep(2.0)
            else:
                air_data = {}

            address = await self._get_address(lat, lon)
            await asyncio.sleep(1.2)
            await asyncio.sleep(1.5)

            if _should_call("warning"):
                warning = await self._get_warning(warn_area_code)
                await asyncio.sleep(1.2)
            else:
                warning = None

            if _should_call("pollen"):
                pollen_data = await self._get_pollen(now, pollen_area_no, pollen_area_name)
            else:
                pollen_data = None
        except Exception as e:
            _LOGGER.error("데이터 수집 중 오류 발생: %s", self._mask_key(e))
            return None

        merged = self._merge_all(now, short_res, mid_res, air_data, address, warning, pollen_data)
        # 단기/중기 미신청 신호: coordinator가 감지하도록 data에 포함
        # TODO: 향후 (data, flags) 튜플로 분리하여 SRP 개선 가능
        if short_res == "UNSUBSCRIBED":
            merged["_short_unsubscribed"] = True
        if isinstance(mid_res, tuple) and mid_res[0] == "UNSUBSCRIBED":
            merged["_mid_unsubscribed"] = True
        return merged

    # ── 주소 (Nominatim) ────────────────────────────────────────────────────
    async def _get_address(self, lat: float, lon: float) -> str:
        try:
            url = "https://nominatim.openstreetmap.org/reverse"
            d = await self._fetch(
                url,
                params={"format": "json", "lat": lat, "lon": lon, "zoom": 16},
                headers={"User-Agent": self._nominatim_user_agent, "Accept-Language": "ko"},
                timeout=5,
            )
            if d:
                a = d.get("address", {})
                parts = [
                    a.get("city", a.get("province", "")),
                    a.get("borough", a.get("county", "")),
                    a.get("suburb", a.get("village", "")),
                ]
                return " ".join([p for p in parts if p]).strip()
        except:
            pass
        return f"{lat:.4f}, {lon:.4f}"

    # ── 에어코리아 ───────────────────────────────────────────────────────────
    async def _get_air_quality(self, lat: float, lon: float) -> dict:
        try:
            if (self._cached_station
                    and self._cached_station_lat is not None
                    and _haversine_fn(
                        self._cached_station_lat, self._cached_station_lon, lat, lon
                    ) > 2.0):
                _LOGGER.debug(
                    "위치 이동 감지 → 에어코리아 측정소 캐시 무효화 (%s → 재계산)",
                    self._cached_station,
                )
                self._cached_station = None
                self._cached_station_lat = None
                self._cached_station_lon = None

            sn = self._cached_station
            if not sn:
                tm_x, tm_y = self._wgs84_to_tm(lat, lon)
                st_json = await self._fetch(
                    "https://apis.data.go.kr/B552584/MsrstnInfoInqireSvc/getNearbyMsrstnList",
                    {"serviceKey": self.api_key, "returnType": "json",
                     "tmX": f"{tm_x:.2f}", "tmY": f"{tm_y:.2f}"},
                )
                code = self._extract_result_code(st_json)
                if code and self._check_unsubscribed("station", code):
                    return {}
                items = (st_json.get("response", {}).get("body", {}).get("items", [])
                         if st_json else [])
                if not items:
                    return {}
                sn = items[0].get("stationName")
                self._cached_station = sn
                self._cached_station_lat = lat
                self._cached_station_lon = lon

            air_json = await self._fetch(
                "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty",
                {"serviceKey": self.api_key, "returnType": "json",
                 "stationName": sn, "dataTerm": "daily", "ver": "1.3"},
            )
            code = self._extract_result_code(air_json)
            if code and self._check_unsubscribed("air", code):
                return {"station": sn}

            ai_list = (air_json.get("response", {}).get("body", {}).get("items", [])
                       if air_json else [])
            if not ai_list:
                return {"station": sn}

            ai = ai_list[0]
            self._mark_approved("air")
            p10v = ai.get("pm10Value")
            p25v = ai.get("pm25Value")

            return {
                "pm10Value": p10v,
                "pm10Grade": self._get_air_grade(p10v, "pm10"),
                "pm25Value": p25v,
                "pm25Grade": self._get_air_grade(p25v, "pm25"),
                "station": sn,
            }
        except Exception as e:
            _LOGGER.error("에어코리아 데이터 호출 실패: %s", self._mask_key(e))
            return {"station": sn} if sn else {}

    def _get_air_grade(self, val: object, p_type: str) -> str:
        """농도 값을 기준으로 한국 환경부 표준 4단계 등급을 직접 계산한다."""
        v = _safe_float(val)
        if v is None:
            return "정보없음"

        if p_type == "pm10":
            # 미세먼지(PM10) 기준: 30, 80, 150
            if v <= 30: return "좋음"
            if v <= 80: return "보통"
            if v <= 150: return "나쁨"
            return "매우나쁨"

        # 초미세먼지(PM2.5) 기준: 15, 35, 75
        if v <= 15: return "좋음"
        if v <= 35: return "보통"
        if v <= 75: return "나쁨"
        return "매우나쁨"

    # ── 단기예보 ────────────────────────────────────────────────────────────
    async def _get_short_term(self, now: datetime) -> dict | None:
        adj = now - timedelta(minutes=10)
        hour = adj.hour

        # 23시~익일 02시 사이에는 안정성을 위해 직전(20시) 발표 데이터를 사용함
        if hour >= 23 or hour < 2:
            base_h = 20
            base_d = (adj - timedelta(days=1)).strftime("%Y%m%d") if hour < 2 else adj.strftime("%Y%m%d")
        else:
            # 02:10 ~ 22:59 사이: 현재 시간 기준 가장 최근 발표 시각 선택 (2, 5, 8, 11, 14, 17, 20)
            base_h = max(h for h in [2, 5, 8, 11, 14, 17, 20] if h <= hour)
            base_d = adj.strftime("%Y%m%d")

        data = await self._fetch(
            "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
            {"serviceKey": self.api_key, "dataType": "JSON",
             "base_date": base_d, "base_time": f"{base_h:02d}00",
             "nx": self.nx, "ny": self.ny, "numOfRows": 1500},
        )
        code = self._extract_result_code(data)
        if code and self._check_unsubscribed("short", code):
            return "UNSUBSCRIBED"  # 미신청/만료 → 업데이트 중단 신호
        items = (data or {}).get("response", {}).get("body", {}).get("items", {}).get("item", [])
        if items:
            self._mark_approved("short")
        return data

    # ── 중기예보 ────────────────────────────────────────────────────────────
    def _get_mid_base_dt(self, now: datetime) -> datetime:
        effective = now - timedelta(minutes=30)
        if effective.hour < 6:
            return (effective - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
        elif effective.hour < 18:
            return effective.replace(hour=6, minute=0, second=0, microsecond=0)
        else:
            return effective.replace(hour=18, minute=0, second=0, microsecond=0)

    async def _get_mid_term(
        self, now: datetime, reg_id_temp: str, reg_id_land: str
    ) -> tuple:
        tm_fc_dt = self._get_mid_base_dt(now)
        base = tm_fc_dt.strftime("%Y%m%d%H%M")

        async def _fetch_both(b):
            res1 = await self._fetch(
                "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa",
                {"serviceKey": self.api_key, "dataType": "JSON",
                 "regId": reg_id_temp, "tmFc": b},
            )
            await asyncio.sleep(1.0)
            res2 = await self._fetch(
                "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst",
                {"serviceKey": self.api_key, "dataType": "JSON",
                 "regId": reg_id_land, "tmFc": b},
            )
            return (res1, res2)

        results = await _fetch_both(base)

        for res in results:
            if not isinstance(res, Exception):
                code = self._extract_result_code(res)
                if code and self._check_unsubscribed("mid", code):
                    return ("UNSUBSCRIBED", None, tm_fc_dt)  # 미신청/만료 → 업데이트 중단 신호

        def _is_valid(res):
            if isinstance(res, Exception) or not res:
                return False
            if res == "UNSUBSCRIBED":
                return False
            items = res.get("response", {}).get("body", {}).get("items", {}).get("item", [])
            return len(items) > 0

        if not _is_valid(results[0]) or not _is_valid(results[1]):
            prev_dt = (
                (tm_fc_dt - timedelta(days=1)).replace(hour=18)
                if tm_fc_dt.hour == 6
                else tm_fc_dt.replace(hour=6)
            )
            _LOGGER.warning(
                "중기예보 최신(%s) 응답이 비어있습니다. 이전 시각(%s)으로 재시도합니다.",
                base, prev_dt.strftime("%Y%m%d%H%M"),
            )
            retry_results = await _fetch_both(prev_dt.strftime("%Y%m%d%H%M"))
            if _is_valid(retry_results[0]) and _is_valid(retry_results[1]):
                return (retry_results[0], retry_results[1], prev_dt)

        r0 = results[0] if not isinstance(results[0], Exception) else None
        r1 = results[1] if not isinstance(results[1], Exception) else None
        if _is_valid(r0) and _is_valid(r1):
            self._mark_approved("mid")
        return (r0, r1, tm_fc_dt)

    # ── 기상특보 ────────────────────────────────────────────────────────────
    async def _get_warning(self, warn_area_code: str | None) -> str | None:
        if not warn_area_code:
            return None
        try:
            now = datetime.now(self.tz)
            from_tm = (now - timedelta(days=5)).strftime("%Y%m%d")
            to_tm = now.strftime("%Y%m%d")

            data = await self._fetch(
                "https://apis.data.go.kr/1360000/WthrWrnInfoService/getPwnCd",
                {"serviceKey": self.api_key, "dataType": "JSON",
                 "areaCode": warn_area_code,
                 "fromTmFc": from_tm, "toTmFc": to_tm,
                 "numOfRows": 1000, "pageNo": 1},
            )
            code = self._extract_result_code(data)
            if code and self._check_unsubscribed("warning", code):
                return None
            if not data:
                return None

            items = (
                data.get("response", {})
                    .get("body", {})
                    .get("items", {})
                    .get("item", [])
            )
            if not items:
                return "특보없음"

            # warnVar별로 tmFc(발표시각) 기준 최신 item 선택
            # command=2(해제)가 최신이면 해당 특보는 해제된 것
            latest: dict[str, dict] = {}
            for item in items:
                key = str(item.get("warnVar", ""))
                tfc = item.get("tmFc", 0)
                if key not in latest or tfc > latest[key].get("tmFc", 0):
                    latest[key] = item

            active = [
                item for item in latest.values()
                if str(item.get("command", "")) in ("1", "3")
                and str(item.get("cancel", "1")) == "0"
                and str(item.get("endTime", "1")) == "0"
            ]
            self._mark_approved("warning")
            if not active:
                return "특보없음"

            warn_names, seen = [], set()
            for item in active:
                pair = _WARN_TYPE_MAP.get(str(item.get("warnVar", "")))
                if pair:
                    name = pair[1] if str(item.get("warnStress", "0")) == "1" else pair[0]
                    if name not in seen:
                        seen.add(name)
                        warn_names.append(name)

            return ", ".join(warn_names) if warn_names else "특보없음"

        except Exception as e:
            _LOGGER.error("기상특보 조회 오류: %s", self._mask_key(e))
            return None

    # ── 꽃가루 농도 위험지수 ────────────────────────────────────────────────
    async def _get_pollen(self, now: datetime, area_no: str, area_name: str) -> dict | None:
        """
        꽃가루 농도 위험지수.
        pine/oak/grass 각각 독립적으로 캐시 관리.

        각 종류별 동작:
        - 자정~07시: tomorrow 표시. 없으면 전날 18시 호출
        - 07시~자정: today 표시. 없으면 06시 호출. 그래도 없으면 전날 18시 tomorrow
        - 19시 이후: tomorrow 갱신 (백그라운드)
        - 비시즌: 좋음 (API 호출 없음)
        - 미신청/중지: None (unavailable)
        """
        month = now.month
        in_season = {
            k: _POLLEN_SEASONS[k][0] <= month <= _POLLEN_SEASONS[k][1]
            for k in ("oak", "pine", "grass")
        }
        offseason = not any(in_season.values())
        today_str = now.strftime("%Y%m%d")
        h = now.hour
        prev_str = (now - timedelta(days=1)).strftime("%Y%m%d")

        # ── 자정: today 캐시 만료 삭제 ───────────────────────────────────────
        for k in _POLLEN_KINDS:
            c = self._pollen_cache[k]
            if c["today_date"] and c["today_date"] != today_str:
                c["today"] = None
                c["today_date"] = None

        # ── 비시즌 + 승인됨: API 호출 없이 좋음 ──────────────────────────────
        if offseason and "pollen" in self._approved_apis:
            return {
                "oak": "좋음", "pine": "좋음", "grass": "좋음", "worst": "좋음",
                "area_name": area_name, "area_no": area_no, "announcement": "비시즌",
            }

        # ── API 활성 여부 확인 (매 업데이트마다) ─────────────────────────────
        if "pollen" in self._approved_apis or "pollen" in self._pending_apis:
            if "pollen" in self._pending_apis:
                for k in _POLLEN_KINDS:
                    self._pollen_cache[k] = {"today": None, "tomorrow": None,
                                             "today_date": None, "tomorrow_date": None}

            check_time = today_str + "06" if h >= 6 else prev_str + "18"
            check_r = await self._fetch(
                "https://apis.data.go.kr/1360000/HealthWthrIdxServiceV3/getPinePollenRiskIdxV3",
                {"serviceKey": self.api_key, "dataType": "JSON",
                 "areaNo": area_no, "time": check_time,
                 "numOfRows": "1", "pageNo": "1"},
            )
            check_code = self._extract_result_code(check_r)
            if check_code and self._check_unsubscribed("pollen", check_code):
                for k in _POLLEN_KINDS:
                    self._pollen_cache[k] = {"today": None, "tomorrow": None,
                                             "today_date": None, "tomorrow_date": None}
                return None
            if check_code == "99":
                # 지역 데이터 없음 → 시즌 항목은 None(unknown), 비시즌 항목은 좋음
                grades = {k: (None if in_season[k] else "좋음") for k in _POLLEN_KINDS}
                season_known = [g for k, g in grades.items() if in_season[k] and g is not None]
                worst = None  # 시즌 항목 모두 데이터 없음
                return {**grades, "worst": worst,
                        "area_name": area_name, "area_no": area_no,
                        "announcement": "데이터없음"}
            if check_code == "00":
                self._mark_approved("pollen")

        # ── 단일 엔드포인트 호출 헬퍼 ────────────────────────────────────────
        def _ann(date_str: str, hour: int) -> str:
            """발표 시각 문자열 생성."""
            return f"{date_str[:4]}년 {date_str[4:6]}월 {date_str[6:]}일 {hour:02d}시 발표"

        base_url = "https://apis.data.go.kr/1360000/HealthWthrIdxServiceV3"
        endpoints = {
            "pine":  f"{base_url}/getPinePollenRiskIdxV3",
            "oak":   f"{base_url}/getOakPollenRiskIdxV3",
            "grass": f"{base_url}/getWeedsPollenRiskndxV3",
        }

        async def _fetch_one(kind: str, time_str: str, fetch_key: str) -> str | None:
            """단일 종류 API 호출. 등급 문자열 반환. 없음→None, 미신청→'UNSUB'"""
            if not in_season[kind]:
                return "좋음"  # 비시즌
            params = {
                "serviceKey": self.api_key, "dataType": "JSON",
                "areaNo": area_no, "time": time_str,
                "numOfRows": "10", "pageNo": "1",
            }
            try:
                r = await self._fetch(endpoints[kind], params)
            except Exception:
                return None
            code = self._extract_result_code(r)
            if code and self._check_unsubscribed("pollen", code):
                return "UNSUB"
            if code == "99":
                return None  # 지역 데이터 없음
            if code != "00":
                return None
            self._mark_approved("pollen")
            if offseason:
                return "좋음"
            items = (r.get("response", {}).get("body", {})
                      .get("items", {}).get("item", []))
            if isinstance(items, dict): items = [items]
            if not items: return None
            val = items[0].get(fetch_key, "")
            if not val:
                fallback = "tomorrow" if fetch_key == "today" else "today"
                val = items[0].get(fallback, "")
            if code == "99": return None  # 지역 데이터 없음 → unknown
            return _POLLEN_GRADE.get(str(val)) if val else None

        async def _get_grade(kind: str) -> str | None:
            """종류별 현재 표시할 등급 결정. 캐시 우선, 없으면 API 호출."""
            if not in_season[kind]:
                return "좋음"

            c = self._pollen_cache[kind]
            ann_today = _ann(today_str, 6)
            ann_18    = _ann(today_str, 18)
            ann_prev  = _ann(prev_str, 18)

            if h < 7:
                # 자정~07시: tomorrow 표시
                if c["tomorrow"] is None:
                    g = await _fetch_one(kind, prev_str + "18", "tomorrow")
                    if g == "UNSUB": return None
                    if g is not None:
                        c["tomorrow"] = g
                        c["tomorrow_date"] = today_str
                return c["tomorrow"]

            else:
                # 07시~자정: today 우선
                if c["today"] is not None:
                    # 19시 이후 tomorrow 백그라운드 갱신
                    if h >= 19 and c["tomorrow_date"] != today_str:
                        g = await _fetch_one(kind, today_str + "18", "tomorrow")
                        if g == "UNSUB": return None
                        if g is not None:
                            c["tomorrow"] = g
                            c["tomorrow_date"] = today_str
                        else:
                            c["tomorrow"] = None
                            c["tomorrow_date"] = None
                    return c["today"]

                # today 없으면 06시 호출
                if c["today_date"] != today_str:
                    g = await _fetch_one(kind, today_str + "06", "today")
                    if g == "UNSUB": return None
                    if g is not None:
                        c["today"] = g
                        c["today_date"] = today_str
                        # 19시 이후면 tomorrow도 갱신
                        if h >= 19 and c["tomorrow_date"] != today_str:
                            tg = await _fetch_one(kind, today_str + "18", "tomorrow")
                            if tg is not None and tg != "UNSUB":
                                c["tomorrow"] = tg
                                c["tomorrow_date"] = today_str
                        return c["today"]

                # 06시도 없으면 전날 18시 tomorrow
                if c["tomorrow"] is None:
                    g = await _fetch_one(kind, prev_str + "18", "tomorrow")
                    if g == "UNSUB": return None
                    if g is not None:
                        c["tomorrow"] = g
                        c["tomorrow_date"] = today_str
                return c["tomorrow"]

        try:
            # ── 429 에러 방지를 위해 순차 호출로 변경 ──
            pine_g = await _get_grade("pine")
            await asyncio.sleep(1.2)  # 꽃가루 개별 API 간격 상향
            oak_g = await _get_grade("oak")
            await asyncio.sleep(1.2)
            grass_g = await _get_grade("grass")

            # 미신청 감지
            if pine_g is None and oak_g is None and grass_g is None:
                return {} if not any(in_season.values()) else {}

            order = ["좋음", "보통", "나쁨", "매우나쁨"]
            season_known = [
                g for k, g in [("pine", pine_g), ("oak", oak_g), ("grass", grass_g)]
                if in_season[k] and g is not None
            ]
            worst = max(season_known, key=lambda g: order.index(g)) if season_known else None

            # announcement: 가장 최근 발표 시각
            ann = _ann(today_str, 18 if h >= 19 else 6)

            return {
                "pine":  pine_g  if pine_g  is not None else ("좋음" if not in_season["pine"]  else None),
                "oak":   oak_g   if oak_g   is not None else ("좋음" if not in_season["oak"]   else None),
                "grass": grass_g if grass_g is not None else ("좋음" if not in_season["grass"] else None),
                "worst": worst,
                "area_name": area_name, "area_no": area_no, "announcement": ann,
            }

        except Exception as e:
            _LOGGER.error("꽃가루 데이터 수집 중 오류 발생: %s", self._mask_key(e))
            return {}

    # ── 유틸리티 ────────────────────────────────────────────────────────────


    def _calculate_apparent_temp(self, temp, reh, wsd):
        t, rh, v = _safe_float(temp), _safe_float(reh), _safe_float(wsd)
        if t is None:
            return temp
        v_kmh = v * 3.6 if v is not None else 0
        if t <= 10 and v_kmh >= 4.8:
            return round(13.12 + 0.6215 * t - 11.37 * (v_kmh ** 0.16) + 0.3965 * t * (v_kmh ** 0.16), 1)
        if t >= 25 and rh is not None and rh >= 40:
            return round(0.5 * (t + 61.0 + ((t - 68.0) * 1.2) + (rh * 0.094)), 1)
        return t

    @staticmethod
    def kor_to_condition(kor: str | None) -> str | None:
        if kor is None:
            return None
        return KOR_TO_CONDITION.get(kor)

    def _get_short_ampm(self, day_data: dict) -> tuple[str, str]:
        def rep_slot(hours):
            skies, ptys = [], []
            for t in hours:
                if t in day_data:
                    td = day_data[t]
                    if "SKY" in td: skies.append(td["SKY"])
                    if "PTY" in td: ptys.append(td["PTY"])
            if not skies and not ptys:
                return None
            pty_rep = max(set(ptys), key=ptys.count) if ptys else "0"
            sky_rep = max(set(skies), key=skies.count) if skies else "1"
            return self._get_sky_kor(sky_rep, pty_rep)

        am_hours = [f"{h:02d}00" for h in range(6, 12)]
        pm_hours = [f"{h:02d}00" for h in range(12, 18)]
        wf_am = rep_slot(am_hours)
        wf_pm = rep_slot(pm_hours)
        return wf_am, wf_pm

    # ── 데이터 병합 ─────────────────────────────────────────────────────────
    def _merge_all(self, now, short_res, mid_res, air_data, address=None, warning=None, pollen_data=None):
        weather_data = {
            "TMP": None, "REH": None, "WSD": None, "VEC": None, "POP": None,
            "TMX_today": None, "TMN_today": None, "TMX_tomorrow": None, "TMN_tomorrow": None,
            "rain_start_time": "강수없음", "forecast_daily": [], "forecast_twice_daily": [],
            "address": address, "warning": warning,
        }

        new_forecast_map = {}
        if short_res and short_res != "UNSUBSCRIBED" and "response" in short_res:
            for it in (short_res.get("response", {}).get("body", {})
                       .get("items", {}).get("item", [])):
                new_forecast_map.setdefault(
                    it["fcstDate"], {}
                ).setdefault(it["fcstTime"], {})[it["category"]] = it["fcstValue"]

        if new_forecast_map:
            self._cache_forecast_map = new_forecast_map
            _LOGGER.debug("단기예보 캐시 갱신: %d일치", len(new_forecast_map))
        else:
            _LOGGER.warning(
                "단기예보 수신 실패 또는 빈 응답 → 캐시 재사용 (날짜 수: %d)",
                len(self._cache_forecast_map),
            )

        forecast_map = self._cache_forecast_map

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
            self._cache_mid_ta = new_mid_ta
            self._cache_mid_land = new_mid_land
            self._cache_mid_tm_fc_dt = new_tm_fc_dt
            _LOGGER.debug("중기예보 캐시 갱신: tmFc=%s", new_tm_fc_dt.strftime("%Y%m%d%H%M"))
        else:
            _LOGGER.warning(
                "중기예보 수신 실패 또는 빈 응답 → 캐시 재사용 (tmFc=%s)",
                self._cache_mid_tm_fc_dt.strftime("%Y%m%d%H%M")
                if self._cache_mid_tm_fc_dt else "없음",
            )

        mid_ta = self._cache_mid_ta
        mid_land = self._cache_mid_land
        tm_fc_dt = self._cache_mid_tm_fc_dt if self._cache_mid_tm_fc_dt else new_tm_fc_dt

        today_str, curr_h = now.strftime("%Y%m%d"), f"{now.hour:02d}00"
        if today_str in forecast_map:
            times = sorted(forecast_map[today_str].keys())
            best_t = next((t for t in times if t >= curr_h), times[-1] if times else None)
            if best_t:
                weather_data.update(forecast_map[today_str][best_t])
        else:
            past_dates = sorted(d for d in forecast_map if d < today_str)
            if past_dates:
                last_date = past_dates[-1]
                times = sorted(forecast_map[last_date].keys())
                if times:
                    weather_data.update(forecast_map[last_date][times[-1]])
                    _LOGGER.debug(
                        "오늘(%s) 날짜 데이터 없음 → 직전(%s) 마지막 슬롯(%s) 사용",
                        today_str, last_date, times[-1],
                    )

        _PTY_LABEL = {"1": "비", "2": "비/눈", "3": "눈", "4": "소나기"}
        for d_str in sorted(forecast_map.keys()):
            rain_times = [
                t_str for t_str in sorted(forecast_map[d_str].keys())
                if _safe_float(forecast_map[d_str][t_str].get("PTY", "0")) > 0
            ]
            if rain_times:
                t = rain_times[0]
                month, day = int(d_str[4:6]), int(d_str[6:8])
                hour, minute = int(t[:2]), int(t[2:])
                pty_val = str(forecast_map[d_str][t].get("PTY", "0"))
                label = _PTY_LABEL.get(pty_val, "강수")
                if minute > 0:
                    weather_data["rain_start_time"] = f"{month}월 {day}일 {hour}시 {minute}분 {label}"
                else:
                    weather_data["rain_start_time"] = f"{month}월 {day}일 {hour}시 {label}"
                break

        twice_daily, daily_forecast = [], []

        for i in range(10):
            target_date = now + timedelta(days=i)
            d_str = target_date.strftime("%Y%m%d")
            t_max = t_min = wf_am = wf_pm = None

            if i <= 3:
                if d_str in forecast_map:
                    short_temps = [
                        _safe_float(v.get("TMP"))
                        for v in forecast_map[d_str].values() if "TMP" in v
                    ]
                    valid_temps = [t for t in short_temps if t is not None]
                    t_max = max(valid_temps) if valid_temps else None
                    t_min = min(valid_temps) if valid_temps else None

                    if i == 0:
                        # 오늘(Day 0)의 오전/오후 대표 날씨 계산 (업데이트 시점부터 구간 설정)
                        today_slots = forecast_map[d_str]
                        all_times = sorted(today_slots.keys())
                        h_now = now.hour

                        def get_range_freq(target_times, fallback_key=None):
                            if not target_times:
                                if fallback_key and fallback_key in today_slots:
                                    s = today_slots[fallback_key]
                                    return self._get_sky_kor(s.get("SKY"), s.get("PTY"))
                                return None
                            c_list = [self._get_sky_kor(today_slots[t].get("SKY"), today_slots[t].get("PTY")) for t in target_times]
                            return max(set(c_list), key=c_list.count)

                        # 오전: 현재 시각 ~ 12:00 (오후에는 업데이트 안 함)
                        if h_now < 12:
                            am_range = [t for t in all_times if h_now <= int(t[:2]) < 12]
                            wf_am = get_range_freq(am_range, "1100")
                        else:
                            wf_am = get_range_freq([], "1100")

                        # 오후: max(12, 현재 시각) ~ 24:00
                        pm_start = max(12, h_now)
                        pm_range = [t for t in all_times if pm_start <= int(t[:2]) < 24]
                        wf_pm = get_range_freq(pm_range, "2300")
                    elif i == 1:
                        # 내일 날씨: 정오(12:00) 슬롯의 날씨를 대표값으로 사용
                        if "1200" in forecast_map[d_str]:
                            wf_noon = self._get_sky_kor(forecast_map[d_str]["1200"].get("SKY"), forecast_map[d_str]["1200"].get("PTY"))
                            wf_am = wf_pm = wf_noon
                        else:
                            wf_am, wf_pm = self._get_short_ampm(forecast_map[d_str])
                    else:
                        wf_am, wf_pm = self._get_short_ampm(forecast_map[d_str])

                _LOGGER.debug("단기예보 i=%d date=%s t_max=%s t_min=%s", i, d_str, t_max, t_min)

            else:
                mid_day_idx = (target_date.date() - tm_fc_dt.date()).days
                if mid_ta and f"taMax{mid_day_idx}" in mid_ta:
                    t_max = _safe_float(mid_ta.get(f"taMax{mid_day_idx}"))
                    t_min = _safe_float(mid_ta.get(f"taMin{mid_day_idx}"))
                    if mid_land:
                        wf_am = self._translate_mid_condition_kor(
                            mid_land.get(f"wf{mid_day_idx}Am") or mid_land.get(f"wf{mid_day_idx}")
                        )
                        wf_pm = self._translate_mid_condition_kor(
                            mid_land.get(f"wf{mid_day_idx}Pm") or mid_land.get(f"wf{mid_day_idx}")
                        )
                elif i <= 5 and d_str in forecast_map:
                    short_temps = [
                        _safe_float(v.get("TMP"))
                        for v in forecast_map[d_str].values() if "TMP" in v
                    ]
                    valid_temps = [t for t in short_temps if t is not None]
                    t_max = max(valid_temps) if valid_temps else None
                    t_min = min(valid_temps) if valid_temps else None
                    wf_am, wf_pm = self._get_short_ampm(forecast_map[d_str])

            if i == 0:
                weather_data["wf_am_today"] = wf_am
                weather_data["wf_pm_today"] = wf_pm
                weather_data["_raw_today_max"] = t_max
                weather_data["_raw_today_min"] = t_min
            elif i == 1:
                weather_data.update({
                    "TMX_tomorrow": t_max, "TMN_tomorrow": t_min,
                    "wf_am_tomorrow": wf_am, "wf_pm_tomorrow": wf_pm,
                })

            for is_am in [True, False]:
                if i == 0 and is_am and now.hour >= 12:
                    continue
                twice_daily.append({
                    "datetime": target_date.replace(
                        hour=9 if is_am else 21, minute=0, second=0, microsecond=0
                    ).isoformat(),
                    "is_daytime": is_am,
                    "native_temperature": t_max,
                    "native_templow": t_min,
                    "condition": self.kor_to_condition(wf_am if is_am else wf_pm),
                    "_day_index": i,
                })

            daily_forecast.append({
                "datetime": target_date.replace(
                    hour=12, minute=0, second=0, microsecond=0
                ).isoformat(),
                "native_temperature": t_max,
                "native_templow": t_min,
                "condition": self.kor_to_condition(wf_pm),
                "_day_index": i,
            })

        # ── 시간별 예보 (forecast_hourly) ─────────────────────────────────────
        _KST = ZoneInfo("Asia/Seoul")

        def _parse_precip(val, no_val):
            """강수/적설량 문자열을 float으로 변환. 없음→0, 미만→0.5, 숫자→숫자."""
            if not val or val in (no_val, "-"): return 0.0
            if "미만" in str(val): return 0.5
            digits = "".join(c for c in str(val) if c.isdigit() or c == ".")
            return float(digits) if digits else 0.0

        hourly_forecast = []
        for d_str in sorted(forecast_map.keys()):
            for t_str in sorted(forecast_map[d_str].keys()):
                slot = forecast_map[d_str][t_str]
                hour = int(t_str[:2])
                minute = int(t_str[2:])
                try:
                    dt = datetime(
                        int(d_str[:4]), int(d_str[4:6]), int(d_str[6:]),
                        hour, minute, 0,
                        tzinfo=_KST
                    )
                except Exception:
                    continue

                # 현재 시각 이후 데이터만 포함 (now가 naive면 aware로 변환)
                now_aware = now if now.tzinfo is not None else now.replace(tzinfo=_KST)
                if dt <= now_aware:
                    continue

                sky = slot.get("SKY")
                pty = slot.get("PTY")
                tmp = _safe_float(slot.get("TMP"))
                pop = _safe_float(slot.get("POP"))
                wsd = _safe_float(slot.get("WSD"))
                vec = _safe_float(slot.get("VEC"))
                reh = _safe_float(slot.get("REH"))
                pcp = slot.get("PCP", "강수없음")
                sno = slot.get("SNO", "적설없음")
                pty_str = str(slot.get("PTY", "0"))

                # PTY=3(눈)이면 적설량, 나머지는 강수량 사용
                raw_val = sno if pty_str == "3" else pcp
                no_precip = ("적설없음" if pty_str == "3" else "강수없음")

                precip = _parse_precip(raw_val, no_precip)

                apparent = self._calculate_apparent_temp(tmp, reh, wsd) if tmp is not None else None

                hourly_forecast.append({
                    "datetime": dt.isoformat(),
                    "condition": self.kor_to_condition(self._get_sky_kor(sky, pty)),
                    "native_temperature": tmp,
                    "native_apparent_temperature": apparent,
                    "precipitation_probability": int(pop) if pop is not None else None,
                    "native_precipitation": precip,
                    "native_wind_speed": wsd,
                    "wind_bearing": int(vec) if vec is not None else None,
                    "humidity": int(reh) if reh is not None else None,
                })

        weather_data.update({"forecast_twice_daily": twice_daily, "forecast_daily": daily_forecast, "forecast_hourly": hourly_forecast})
        
        # 현재 날씨 (현재 시각에 가장 가까운 예보 슬롯의 날씨)
        # weather_data는 이미 best_t 슬롯의 값으로 업데이트되어 있음
        kor_now = self._get_sky_kor(weather_data.get("SKY"), weather_data.get("PTY"))
            
        weather_data.update({
            "current_condition_kor": kor_now,
            "current_condition": self.kor_to_condition(kor_now),
            "apparent_temp": self._calculate_apparent_temp(
                weather_data.get("TMP"), weather_data.get("REH"), weather_data.get("WSD")),
        })
        if weather_data.get("VEC"):
            weather_data["VEC_KOR"] = self._get_vec_kor(weather_data["VEC"])
        return {
            "weather": weather_data,
            "air": air_data or {},
            "pollen": pollen_data,   # None이면 센서 unavailable (미신청/만료)
            "raw_forecast": forecast_map,
        }

    def _translate_mid_condition_kor(self, wf: str) -> str:
        wf = str(wf or "맑음")
        if wf in KOR_TO_CONDITION: return wf
        if "비/눈" in wf: return "비/눈"
        if "소나기" in wf: return "소나기"
        if "비" in wf: return "비"
        if "눈" in wf: return "눈"
        if "흐리" in wf or "흐림" in wf: return "흐림"
        if "구름" in wf: return "구름많음"
        return "맑음"

    def _get_sky_kor(self, sky, pty):
        p, s = str(pty or "0"), str(sky or "1")
        if p in ["1", "2", "3", "4", "5", "6", "7"]:
            return {"1": "비", "2": "비/눈", "3": "눈", "4": "소나기",
                    "5": "빗방울", "6": "빗방울/눈날림", "7": "눈날림"}.get(p, "비")
        return "맑음" if s == "1" else ("구름많음" if s == "3" else "흐림")

    def _get_vec_kor(self, vec):
        v = _safe_float(vec)
        if v is None: return None
        if 22.5 <= v < 67.5:    return "북동"
        elif 67.5 <= v < 112.5:  return "동"
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
        T = math.tan(phi) ** 2
        C = e2 / (1 - e2) * math.cos(phi) ** 2
        A = math.cos(phi) * (lam - lon0)

        def M(p):
            return a * ((1 - e2/4 - 3*e2**2/64 - 5*e2**3/256) * p
                        - (3*e2/8 + 3*e2**2/32 + 45*e2**3/1024) * math.sin(2*p)
                        + (15*e2**2/256 + 45*e2**3/1024) * math.sin(4*p)
                        - (35*e2**3/3072) * math.sin(6*p))

        return (
            200000.0 + N * (A + (1-T+C)*A**3/6 + (5-18*T+T**2+72*C-58*(e2/(1-e2)))*A**5/120),
            500000.0 + (M(phi) - M(lat0) + N*math.tan(phi)*(
                A**2/2 + (5-T+9*C+4*C**2)*A**4/24 + (61-58*T+T**2+600*C-330*(e2/(1-e2)))*A**6/720)),
        )
