import logging
import asyncio
import math
import json
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo
from .const import haversine as _haversine_fn, safe_float as _safe_float

_LOGGER = logging.getLogger(__name__)

# ── 특보 코드 → 한글 변환 ────────────────────────────────────────────────────
_WARN_TYPE_MAP: dict[str, tuple[str, str]] = {
    "1":  ("강풍주의보",     "강풍경보"),
    "2":  ("호우주의보",     "호우경보"),
    "3":  ("한파주의보",     "한파경보"),
    "4":  ("건조주의보",     "건조경보"),
    "5":  ("폭풍해일주의보", "폭풍해일경보"),
    "6":  ("풍랑주의보",     "풍랑경보"),
    "7":  ("태풍주의보",     "태풍경보"),
    "8":  ("대설주의보",     "대설경보"),
    "9":  ("황사주의보",     "황사경보"),
    "10": ("안개주의보",     "안개경보"),
    "11": ("지진해일주의보", "지진해일경보"),
    "12": ("폭염주의보",     "폭염경보"),
}

# ── API 서비스 정보 (미신청 감지용) ──────────────────────────────────────────
_API_SERVICES = {
    "short":   ("기상청 단기예보",        "https://www.data.go.kr/data/15084084/openapi.do"),
    "mid":     ("기상청 중기예보",        "https://www.data.go.kr/data/15059468/openapi.do"),
    "air":     ("에어코리아 대기오염정보", "https://www.data.go.kr/data/15073861/openapi.do"),
    "station": ("에어코리아 측정소정보",  "https://www.data.go.kr/data/15073877/openapi.do"),
    "warning": ("기상특보 조회서비스",    "https://www.data.go.kr/data/15000415/openapi.do"),
    "pollen":  ("기상청 생활기상지수",    "https://www.data.go.kr/data/15085289/openapi.do"),
}

# 미신청으로 판단하는 resultCode 목록
_UNSUBSCRIBED_CODES = {"20", "22", "30", "31", "33"}

# ── 꽃가루 관련 상수 ──────────────────────────────────────────────────────────
# 단계: 낮음=0, 보통=1, 높음=2, 매우높음=3 (문서 기준)
_POLLEN_GRADE = {"0": "좋음", "1": "보통", "2": "나쁨", "3": "매우나쁨"}
_POLLEN_GRADE_RANK = {"좋음": 1, "보통": 2, "나쁨": 3, "매우나쁨": 4}
# 꽃가루 제공 시즌 (시작월, 종료월 포함)
_POLLEN_SEASONS = {"oak": (3, 6), "pine": (3, 6), "grass": (4, 10)}





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
    def __init__(self, session, api_key, hass=None):
        self.session = session
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

        # ── 꽃가루 지역코드 룩업 (JSON, 읍면동 단위) ─────────────────────────
        # pollen_area_map.json: [{"c":"1111051500","n":"서울특별시 종로구 청운효자동","la":37.58,"lo":126.97},...]
        self._pollen_area_data: list[dict] | None = None
        self._pollen_cached_area_no: str = "1100000000"   # fallback: 서울
        self._pollen_cached_area_name: str = ""
        self._pollen_cached_lat: float | None = None
        self._pollen_cached_lon: float | None = None
        # pollen_area_map.json은 첫 _find_pollen_area 호출 시 로드됨 (lazy)

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
    def _load_pollen_area_map(self) -> None:
        """pollen_area_map.json을 로드한다."""
        try:
            json_path = Path(__file__).parent / "pollen_area_map.json"
            with open(json_path, encoding="utf-8") as f:
                self._pollen_area_data = json.load(f)
            _LOGGER.debug("꽃가루 지역코드 룩업 로드 완료: %d개 읍면동", len(self._pollen_area_data))
        except Exception as e:
            _LOGGER.warning("꽃가루 지역코드 룩업 로드 실패 (pollen_area_map.json 누락?): %s", e)
            self._pollen_area_data = None

    async def _find_pollen_area(self, lat: float, lon: float) -> tuple[str, str]:
        """
        위경도로 가장 가까운 읍면동의 (areaNo, 지역명)을 반환한다.
        좌표가 이전과 같으면 캐시를 반환한다.
        JSON이 아직 로딩되지 않았으면 executor에서 로딩한다.
        JSON 로드 실패 시 서울 fallback을 반환한다.
        """
        if (self._pollen_cached_lat == lat
                and self._pollen_cached_lon == lon
                and self._pollen_cached_area_no):
            return self._pollen_cached_area_no, self._pollen_cached_area_name

        if not self._pollen_area_data:
            if self.hass:
                try:
                    await self.hass.async_add_executor_job(self._load_pollen_area_map)
                except Exception as e:
                    _LOGGER.warning("pollen_area_map.json 로드 실패: %s", e)
            if not self._pollen_area_data:
                return "1100000000", ""

        best, best_d = None, float("inf")
        for r in self._pollen_area_data:
            d = (r["la"] - lat) ** 2 + (r["lo"] - lon) ** 2
            if d < best_d:
                best_d, best = d, r

        if best:
            self._pollen_cached_lat = lat
            self._pollen_cached_lon = lon
            self._pollen_cached_area_no = best["c"]
            self._pollen_cached_area_name = best["n"]
            _LOGGER.debug("꽃가루 지역 매칭: (%.4f, %.4f) → %s (%s)", lat, lon, best["n"], best["c"])
            return best["c"], best["n"]

        return "1100000000", ""

    # ── API 미신청 감지 및 알림 ─────────────────────────────────────────────
    def _check_unsubscribed(self, service_key: str, result_code: str) -> bool:
        if result_code not in _UNSUBSCRIBED_CODES:
            return False

        # 미신청/만료 감지 시 _approved_apis에서 제거 → 관련 센서 unavailable 전환
        if service_key in self._approved_apis:
            _LOGGER.warning("API 만료/중지 감지 [%s]: resultCode=%s → _approved_apis에서 제거", service_key, result_code)
            self._approved_apis.discard(service_key)

        # _approved에서 제거된 경우 _pending에 다시 추가 → 다음 업데이트에서 재확인
        if service_key not in self._pending_apis:
            self._pending_apis.add(service_key)

        # HA 알림은 최초 1회만 (중복 방지)
        if service_key in self._notified_unsubscribed:
            return True

        self._notified_unsubscribed.add(service_key)
        name, url = _API_SERVICES.get(service_key, (service_key, ""))

        msg = (
            f"**기상청 스마트 날씨 — API 미신청 감지**\n\n"
            f"**{name}** 서비스가 활용신청되지 않았거나 접근이 거부되었습니다 "
            f"(오류코드: {result_code}).\n\n"
            f"아래 링크에서 활용신청 후 승인을 기다려 주세요:\n"
            f"[{name} 신청하기]({url})\n\n"
            f"신청 후 HA를 재시작하거나 수동 업데이트를 누르면 정상 작동합니다."
        )
        _LOGGER.warning("API 미신청 감지 [%s]: resultCode=%s → %s", service_key, result_code, url)

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

    # URL → 카운팅 키 매핑
    _CALL_COUNT_KEY: dict[str, str] = {
        "VilageFcstInfoService_2.0": "단기예보",
        "MidFcstInfoService":        "중기예보",
        "MsrstnInfoInqireSvc":       "에어코리아_측정소",
        "ArpltnInforInqireSvc":      "에어코리아_대기",
        "WthrWrnInfoService":        "기상특보",
        "LivingWthrIdxServiceV4":    "꽃가루",
    }

    async def _fetch(self, url, params, headers=None, timeout=15):
        # URL에서 서비스 키 추출 → coordinator 카운터 증가
        if self.hass is not None:
            for fragment, key in self._CALL_COUNT_KEY.items():
                if fragment in url:
                    if hasattr(self, "_call_counter_ref") and self._call_counter_ref is not None:
                        self._call_counter_ref(key)
                    break
        try:
            async with self.session.get(
                url, params=params, headers=headers, timeout=timeout
            ) as response:
                if response.status == 401 or response.status == 403:
                    # 인증 실패 → 미신청/만료와 동일하게 처리
                    _LOGGER.warning("API 인증 실패 (%s): HTTP %s", url, response.status)
                    return {"_http_error": str(response.status)}
                if response.status == 404:
                    # 404는 API URL 문제 또는 서비스 비활성화
                    _LOGGER.debug("API 404 응답 (%s) - 미신청 또는 중지된 서비스일 수 있음", url)
                    return {"_http_error": "404"}
                response.raise_for_status()
                text = await response.text()
                # JSON 우선 파싱, 실패 시 XML로 처리
                # (일부 API는 dataType=JSON 요청에도 XML 반환)
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, ValueError):
                    if text.strip().startswith("<"):
                        _LOGGER.debug("XML 응답 감지 (%s) → XML 파싱", url)
                        return self._parse_xml_to_dict(text)
                    _LOGGER.error("API 응답 파싱 실패 (%s): 알 수 없는 형식", url)
                    return None
        except Exception as err:
            _LOGGER.error("API 호출 실패 (%s): %s", url, err)
        return None

    def _extract_result_code(self, data: dict | None) -> str | None:
        if not data:
            return None
        # HTTP 오류 응답 (401/403/404) → 미신청 코드로 매핑
        if "_http_error" in data:
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
        force_check: bool = False,
    ) -> dict | None:
        self.lat, self.lon, self.nx, self.ny = lat, lon, nx, ny
        now = datetime.now(self.tz)

        # force_check=True (버튼/다시읽기): 미신청 알림 기록 초기화
        # → 재신청 후 HA 알림이 다시 출력되도록
        if force_check:
            self._notified_unsubscribed.clear()

        async def _skip_coro(default):
            return default

        # 승인 여부 판단:
        # _approved_apis에 있음 → 실제 데이터 호출 (_get_* 내부 로직 정상 실행)
        # _pending_apis에 있음 → 승인 확인용 경량 호출 (_get_* 내부에서 resultCode만 확인)
        # 둘 다 없음 → 건너뜀 (승인 후 _pending 제거됐으나 _approved에도 없는 이상 상태)
        def _should_call(key: str) -> bool:
            # _pending_apis:  미확인/미신청/만료 → 매 업데이트마다 호출해서 확인
            # _approved_apis: 승인됨 → 데이터 호출
            return key in self._approved_apis or key in self._pending_apis

        tasks = [
            self._get_short_term(now),
            self._get_mid_term(now, reg_id_temp, reg_id_land),
            self._get_air_quality(lat, lon)
                if _should_call("air") or _should_call("station")
                else _skip_coro({}),
            self._get_address(lat, lon),
            self._get_warning(warn_area_code)
                if _should_call("warning")
                else _skip_coro(None),
            self._get_pollen(now, lat, lon)
                if _should_call("pollen")
                else _skip_coro({}),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        short_res, mid_res, air_data, address, warning, pollen_data = [
            r if not isinstance(r, Exception) else None for r in results
        ]
        return self._merge_all(now, short_res, mid_res, air_data, address, warning, pollen_data)

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
            return {
                "pm10Value": ai.get("pm10Value"),
                "pm10Grade": self._translate_grade(ai.get("pm10Grade") or ai.get("pm10Grade1h")),
                "pm25Value": ai.get("pm25Value"),
                "pm25Grade": self._translate_grade(ai.get("pm25Grade") or ai.get("pm25Grade1h")),
                "station": sn,
            }
        except Exception as e:
            _LOGGER.error("Air quality fetch error: %s", e)
            return {}

    def _translate_grade(self, g):
        return {"1": "좋음", "2": "보통", "3": "나쁨", "4": "매우나쁨"}.get(str(g), "정보없음")

    # ── 단기예보 ────────────────────────────────────────────────────────────
    async def _get_short_term(self, now: datetime) -> dict | None:
        adj = now - timedelta(minutes=10)
        hour = adj.hour
        valid_hours = [h for h in [2, 5, 8, 11, 14, 17, 20, 23] if h <= hour]
        if valid_hours:
            base_h = max(valid_hours)
            base_d = adj.strftime("%Y%m%d")
        else:
            base_h = 23
            base_d = (adj - timedelta(days=1)).strftime("%Y%m%d")

        data = await self._fetch(
            "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
            {"serviceKey": self.api_key, "dataType": "JSON",
             "base_date": base_d, "base_time": f"{base_h:02d}00",
             "nx": self.nx, "ny": self.ny, "numOfRows": 1500},
        )
        code = self._extract_result_code(data)
        if code and self._check_unsubscribed("short", code):
            return None
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
            return await asyncio.gather(
                self._fetch(
                    "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa",
                    {"serviceKey": self.api_key, "dataType": "JSON",
                     "regId": reg_id_temp, "tmFc": b},
                ),
                self._fetch(
                    "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst",
                    {"serviceKey": self.api_key, "dataType": "JSON",
                     "regId": reg_id_land, "tmFc": b},
                ),
                return_exceptions=True,
            )

        results = await _fetch_both(base)

        for res in results:
            if not isinstance(res, Exception):
                code = self._extract_result_code(res)
                if code and self._check_unsubscribed("mid", code):
                    return (None, None, tm_fc_dt)

        def _is_valid(res):
            if isinstance(res, Exception) or not res:
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
                "http://apis.data.go.kr/1360000/WthrWrnInfoService/getPwnCd",
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

            active = [
                item for item in items
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
            _LOGGER.error("기상특보 조회 오류: %s", e)
            return None

    # ── 꽃가루 농도 위험지수 ────────────────────────────────────────────────
    async def _get_pollen(self, now: datetime, lat: float, lon: float) -> dict:
        """
        꽃가루 농도 위험지수를 조회한다.

        areaNo에 읍면동 단위 코드를 직접 전달하여 정확한 동 단위 데이터를 얻는다.
        (pollen_area_map.json에서 현재 위경도에 가장 가까운 읍면동 코드를 룩업)

        API: LivingWthrIdxServiceV4/getPollenRiskIdxV4
        응답 필드: code(지수종류), areaNo, date, today, tomorrow, dayaftertomorrow, twodaysaftertomorrow
        단계: 0=낮음(좋음), 1=보통, 2=높음(나쁨), 3=매우높음(매우나쁨)

        비시즌에도 API 승인 여부가 미확인이면 한 번 호출하여 확인한다.
        """
        month = now.month
        in_season = {
            k: _POLLEN_SEASONS[k][0] <= month <= _POLLEN_SEASONS[k][1]
            for k in ("oak", "pine", "grass")
        }
        offseason = not any(in_season.values())

        # 현재 위치의 읍면동 areaNo 및 지역명 (JSON 룩업, 캐시 활용)
        area_no, area_name = await self._find_pollen_area(lat, lon)

        # 비시즌 + 이미 승인 확인됨 → API 호출 없이 좋음 반환
        if offseason and "pollen" in self._approved_apis:
            return {
                "oak": "좋음", "pine": "좋음", "grass": "좋음", "worst": "좋음",
                "area_name": area_name, "area_no": area_no,
            }

        # 발표 시각 결정
        # 문서 기준: 06시 발표(오늘/내일/모레), 18시 발표(내일/모레/글피)
        h = now.hour
        if h < 6:
            time_str = (now - timedelta(days=1)).strftime("%Y%m%d") + "18"
        elif h < 18:
            time_str = now.strftime("%Y%m%d") + "06"
        else:
            time_str = now.strftime("%Y%m%d") + "18"

        try:
            data = await self._fetch(
                "https://apis.data.go.kr/1360000/LivingWthrIdxServiceV4/getPollenRiskIdxV4",
                {
                    "serviceKey": self.api_key,
                    "dataType": "JSON",
                    "areaNo": area_no,
                    "time": time_str,
                    "numOfRows": "10",
                    "pageNo": "1",
                },
            )
            code = self._extract_result_code(data)
            if code and self._check_unsubscribed("pollen", code):
                # 미신청: 빈 dict 반환 → 센서 미생성
                return {}

            # resultCode=00 확인 시에만 승인 처리
            # code가 None(응답 없음/파싱 실패)이면 승인 상태 변경 없이 유지
            if code != "00":
                return {}
            self._mark_approved("pollen")

            # 비시즌: 데이터 파싱 없이 좋음 반환
            if offseason:
                return {
                    "oak": "좋음", "pine": "좋음", "grass": "좋음", "worst": "좋음",
                    "area_name": area_name, "area_no": area_no,
                }

            items = (
                (data or {})
                .get("response", {})
                .get("body", {})
                .get("items", {})
                .get("item", [])
            )
            if not items:
                return {
                    "oak": "좋음", "pine": "좋음", "grass": "좋음", "worst": "좋음",
                    "area_name": area_name, "area_no": area_no,
                }

            if not isinstance(items, list):
                items = [items]

            # ── 응답 파싱 ────────────────────────────────────────────────────
            # V4 응답 구조: code(지수종류), areaNo, date, today, tomorrow, ...
            # areaNo에 읍면동 코드를 전달하면 해당 동 1건만 반환됨
            # code별로 분리: D07=참나무, D08=소나무, D09=잡초류
            code_map: dict[str, dict] = {}
            for item in items:
                c = str(item.get("code", ""))
                code_map[c] = item

            def _grade(item: dict | None) -> str:
                if not item:
                    return "좋음"
                val = str(item.get("today") or "")
                return _POLLEN_GRADE.get(val, "좋음")

            # code가 있으면 code별로, 없으면 첫 번째 item에서 직접 읽기
            if "D07" in code_map or "D08" in code_map or "D09" in code_map:
                oak_item   = code_map.get("D07")
                pine_item  = code_map.get("D08")
                grass_item = code_map.get("D09")
            else:
                # code 구분 없이 단일 item인 경우
                oak_item = pine_item = grass_item = items[0]

            result: dict = {
                "oak":   _grade(oak_item)   if in_season["oak"]   else "좋음",
                "pine":  _grade(pine_item)  if in_season["pine"]  else "좋음",
                "grass": _grade(grass_item) if in_season["grass"] else "좋음",
            }
            result["worst"] = max(
                (result[k] for k in ("oak", "pine", "grass")),
                key=lambda g: _POLLEN_GRADE_RANK.get(g, 1),
                default="좋음",
            )
            result["area_name"] = area_name
            result["area_no"]   = area_no
            return result

        except Exception as e:
            _LOGGER.error("꽃가루 조회 오류: %s", e)
            if "pollen" in self._approved_apis:
                return {
                    "oak": "좋음", "pine": "좋음", "grass": "좋음", "worst": "좋음",
                    "area_name": area_name, "area_no": area_no,
                }
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
        wf_am = rep_slot(am_hours) or "맑음"
        wf_pm = rep_slot(pm_hours) or wf_am
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
        if short_res and "response" in short_res:
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

        for d_str in sorted(forecast_map.keys()):
            rain_times = [
                t_str for t_str in sorted(forecast_map[d_str].keys())
                if _safe_float(forecast_map[d_str][t_str].get("PTY", "0")) > 0
            ]
            if rain_times:
                t = rain_times[0]
                month, day = int(d_str[4:6]), int(d_str[6:8])
                hour, minute = int(t[:2]), int(t[2:])
                if minute > 0:
                    weather_data["rain_start_time"] = f"{month}월 {day}일 {hour}시 {minute}분"
                else:
                    weather_data["rain_start_time"] = f"{month}월 {day}일 {hour}시"
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

                    am_slot = "0900" if "0900" in forecast_map[d_str] else next(
                        (t for t in sorted(forecast_map[d_str].keys()) if t < "1200"), None)
                    pm_slot = next(
                        (t for t in ["1500", "1200", "1800"] if t in forecast_map[d_str]), None)

                    if am_slot:
                        wf_am = self._get_sky_kor(
                            forecast_map[d_str][am_slot].get("SKY"),
                            forecast_map[d_str][am_slot].get("PTY"))
                    if pm_slot:
                        wf_pm = self._get_sky_kor(
                            forecast_map[d_str][pm_slot].get("SKY"),
                            forecast_map[d_str][pm_slot].get("PTY"))
                    else:
                        wf_pm = wf_am

                _LOGGER.debug("단기예보 i=%d date=%s t_max=%s t_min=%s", i, d_str, t_max, t_min)

            else:
                mid_day_idx = (target_date.date() - tm_fc_dt.date()).days
                t_max_mid = _safe_float(mid_ta.get(f"taMax{mid_day_idx}")) if mid_ta else None
                t_min_mid = _safe_float(mid_ta.get(f"taMin{mid_day_idx}")) if mid_ta else None
                wf_am_mid = self._translate_mid_condition_kor(
                    mid_land.get(f"wf{mid_day_idx}Am") or mid_land.get(f"wf{mid_day_idx}")
                ) if mid_land else None
                wf_pm_mid = self._translate_mid_condition_kor(
                    mid_land.get(f"wf{mid_day_idx}Pm") or mid_land.get(f"wf{mid_day_idx}")
                ) if mid_land else None

                if t_max_mid is not None and t_min_mid is not None:
                    t_max, t_min = t_max_mid, t_min_mid
                    wf_am = wf_am_mid or "맑음"
                    wf_pm = wf_pm_mid or "맑음"
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
        return {
            "weather": weather_data,
            "air": air_data or {},
            "pollen": pollen_data or {},
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
