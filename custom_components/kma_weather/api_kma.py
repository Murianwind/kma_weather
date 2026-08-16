from __future__ import annotations
import logging
import asyncio
import math
import re
import json
import hashlib
import aiohttp
from datetime import datetime, timedelta, date
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo
from homeassistant.core import HomeAssistant
from .const import haversine as _haversine_fn, safe_float as _safe_float

_LOGGER = logging.getLogger(__name__)
# 에어코리아 실시간 조회 보완(realSearch+getRealChart) 전용 하위 로거.
# api_kma 전체를 debug로 켜면 날씨/특보/꽃가루 등 다른 로그까지 전부 쏟아지므로,
# 이 흐름만 따로 진단하고 싶을 때는 아래 로거 이름 하나만 debug로 켜면 된다:
#   logger:
#     logs:
#       custom_components.kma_weather.api_kma.airkorea_fallback: debug
_AIRKOREA_LOGGER = _LOGGER.getChild("airkorea_fallback")

from .const import (
    WARN_TYPE_MAP        as _WARN_TYPE_MAP,
    API_SERVICES         as _API_SERVICES,
    UNSUBSCRIBED_CODES   as _UNSUBSCRIBED_CODES,
    POLLEN_GRADE         as _POLLEN_GRADE,
    POLLEN_SEASONS       as _POLLEN_SEASONS,
    UV_GRADES            as _UV_GRADES,
    UV_GRADE_MAX         as _UV_GRADE_MAX,
)
_POLLEN_KINDS: tuple[str, ...] = tuple(_POLLEN_SEASONS.keys())
_POLLEN_GRADE_RANK = {"좋음": 1, "보통": 2, "나쁨": 3, "매우나쁨": 4}

# ── 특보 기본명(호우/폭염 등) → warnVar 역매핑 ─────────────────────────────
# weather.go.kr 실시간 페이지의 "특보" 칸(예: "호우")을 warnVar 키로 되돌리는 데 사용
_BASE_NAME_TO_WARNVAR: dict[str, str] = {}
for _wv, _names in _WARN_TYPE_MAP.items():
    for _n in _names:
        for _suffix in ("중대경보", "경보", "주의보"):
            if _n.endswith(_suffix):
                _BASE_NAME_TO_WARNVAR[_n[: -len(_suffix)]] = _wv
                break

# "폭염중대경보", "열대야주의보"처럼 등급까지 포함된 전체 이름 → (warnVar, 표시명).
# weather.go.kr의 "특보 발효현황" 요약 텍스트("o 열대야주의보 : 지역...")를
# 파싱할 때 사용한다 (그 텍스트는 표(등급 컬럼 분리)와 달리 이름에 등급이 붙어 있음).
_FULL_NAME_TO_WV: dict[str, tuple[str, str]] = {}
for _wv, _names in _WARN_TYPE_MAP.items():
    for _n in _names:
        _FULL_NAME_TO_WV[_n] = (_wv, _n)


def _split_top_level(s: str, sep: str = ",") -> list[str]:
    """괄호 안의 콤마는 무시하고, 최상위 레벨의 콤마로만 문자열을 나눈다."""
    parts: list[str] = []
    depth = 0
    cur = ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def _area_in_region_cell(area_name: str, region_cell: str, ancestors: list[str] | None = None) -> bool:
    """
    기상청 특보 요약의 지역 표기("서울(서울서북권 제외), 경상북도(구미, 영천)"
    같은 형식)에서 area_name이 포함되는지 판단한다.

    단순 문자열 포함 검사로는 안 되는 이유: 시/도 전역이 해당될 때는
    괄호 없이 시/도 이름만 적고 하위 지역명을 나열하지 않으며("서울" 단독),
    일부만 제외할 때는 "(제외될 지역 제외)"로 표기한다. 둘 다 area_name
    문자열이 그대로 텍스트에 나타나지 않으므로 별도 해석이 필요하다.

    ancestors: area_name의 상위(도/시) 이름 목록(가까운 부모 → 최상위 순,
    예: "청주동부" → ["청주", "충청북도"]). "서울동북권"처럼 하위지역명에
    상위 이름이 그대로 포함된 경우는 문자열 startswith로도 잡히지만,
    "청주동부"처럼 상위(충청북도)와 하위 이름이 문자열로 이어지지 않는
    경우는 ancestors 없이는 "충청북도" 전역 표기를 못 알아본다.
    """
    ancestors = ancestors or []

    def _is_area_or_descendant_of(name: str) -> bool:
        return area_name == name or name in ancestors

    for segment in _split_top_level(region_cell, ","):
        idx = segment.find("(")
        if idx == -1:
            # 괄호 없이 시/도 이름만 있으면 그 시/도 전역이 해당됨
            city = segment.strip()
            if _is_area_or_descendant_of(city) or area_name.startswith(city):
                return True
            continue

        city = segment[:idx].strip()
        inner = segment[idx + 1:]
        if inner.endswith(")"):
            inner = inner[:-1]
        items = _split_top_level(inner, ",")
        has_exclude = any("제외" in it for it in items)
        cleaned = [it.replace("제외", "").strip() for it in items]

        if has_exclude:
            # "시/도(A, B 제외)" → A, B를 제외한 시/도 전역이 해당됨
            excluded_hit = any(
                c and (_is_area_or_descendant_of(c) or c in area_name or area_name in c)
                for c in cleaned
            )
            if (_is_area_or_descendant_of(city) or area_name.startswith(city)) and not excluded_hit:
                return True
        else:
            # "시/도(A, B)" → A, B에 해당하는 지역만 포함됨
            if any(
                c and (_is_area_or_descendant_of(c) or c in area_name or area_name in c)
                for c in cleaned
            ):
                return True
    return False



# ── 특보구역코드(L코드) → 표시명(예: "제주시북부") 매핑 ─────────────────────
# weather.go.kr 실시간 페이지의 "해당지역" 칸과 대조하기 위해 사용.
# 기상청_기상특보구역정보_20260601.csv 기준.
_WARN_AREA_NAMES: dict[str, str] = {}


def _load_warn_area_names() -> None:
    global _WARN_AREA_NAMES
    _WARN_AREA_NAMES = json.loads(
        (Path(__file__).parent / "warn_area_names.json").read_text(encoding="utf-8")
    )


# ── 특보구역코드(L코드) → 상위(도/시) 이름 목록 ─────────────────────────────
# "청주동부"의 상위는 ["청주", "충청북도"]처럼, 가까운 부모부터 최상위(시/도)
# 순으로 담긴다. weather.go.kr 요약이 "충청북도"처럼 광역 단위를 괄호 없이
# 통째로 쓸 때, 그 안에 속한 하위지역인지 판별하는 데 쓴다.
# 기상청 특보구역코드 안내(260601 기준, REG_UP 계층) 기반으로 생성.
_WARN_AREA_ANCESTORS: dict[str, list[str]] = {}


def _load_warn_area_ancestors() -> None:
    global _WARN_AREA_ANCESTORS
    _WARN_AREA_ANCESTORS = json.loads(
        (Path(__file__).parent / "warn_area_ancestors.json").read_text(encoding="utf-8")
    )


# ── 에어코리아(airkorea.or.kr) 대기질 상세 조회 페이지의 시/도 코드 ──────────
# https://www.airkorea.or.kr/web/getDynamicDistrictList 응답 기준.
# API(getMsrstnAcctoRltmMesureDnsty)가 실패했을 때, 측정소별 시간대 실측값을
# 보완용으로 긁어오기 위해 사용한다. 전남/광주는 이 사이트에서 "065" 코드를
# 같이 쓴다(사이트 자체 구분).
_AIRKOREA_DISTRICT_CODES: list[tuple[str, str]] = [
    ("세종", "044"),   # "서울"보다 먼저 검사해야 하는 것은 아니지만,
    ("서울", "02"), ("부산", "051"), ("대구", "053"), ("인천", "032"),
    ("대전", "042"), ("울산", "052"), ("경기", "031"), ("강원", "033"),
    ("충북", "043"), ("충청북", "043"), ("충남", "041"), ("충청남", "041"),
    ("전북", "063"), ("전라북", "063"), ("전남", "065"), ("전라남", "065"),
    ("광주", "065"), ("경북", "054"), ("경상북", "054"),
    ("경남", "055"), ("경상남", "055"), ("제주", "064"),
]


def _airkorea_district_code(city_name: str) -> str | None:
    """주소의 시/도 부분(예: '서울특별시', '전라남도')으로 에어코리아
    district 코드를 찾는다. 접두어 매칭이라 신구 명칭(강원도/강원특별자치도,
    전북특별자치도 등) 변화에 영향받지 않는다."""
    if not city_name:
        return None
    for prefix, code in _AIRKOREA_DISTRICT_CODES:
        if city_name.startswith(prefix):
            return code
    return None


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
        self._raw_api_key = api_key
        self.api_key = unquote(api_key)
        self.hass = hass
        self.tz = ZoneInfo("Asia/Seoul")
        self.lat = self.lon = self.nx = self.ny = None

        self._cached_station: str | None = None
        self._cached_station_code: str | None = None
        # 에어코리아 실시간 조회(key 발급 → 조회) 전용, 이 인스턴스만 쓰는 세션.
        # HA 공유 세션(self.session)을 쓰면 같은 위치의 다른 기기(다른 config
        # entry)와 쿠키(로그인 세션)를 공유하게 되어, 두 기기가 거의 동시에
        # 갱신될 때 한쪽이 발급받은 key가 다른 쪽이 방금 갱신한 쿠키와 섞여
        # 실패하는 레이스 컨디션이 생긴다. 그래서 이 흐름만 별도 세션으로 분리한다.
        self._airkorea_session: aiohttp.ClientSession | None = None
        self._cached_station_lat: float | None = None
        self._cached_station_lon: float | None = None
        self._warn_page_fetch_failed_notified = False  # 페이지 실패 경고 중복 방지용

        self._nominatim_user_agent = self._build_nominatim_user_agent()

        self._cache_forecast_map: dict = {}
        self._cache_mid_ta: dict = {}
        self._cache_mid_land: dict = {}
        self._cache_mid_tm_fc_dt: datetime | None = None

        self._notified_unsubscribed: set[str] = set()
        self._approved_apis: set[str] = set()
        self._pending_apis: set[str] = {"air", "station", "warning", "pollen", "uv"}
        self._call_counter_ref = None

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

    def _check_unsubscribed(self, service_key: str, result_code: str) -> bool:
        if result_code not in _UNSUBSCRIBED_CODES:
            return False

        if service_key in self._approved_apis:
            _LOGGER.warning("API 만료/중지 감지 [%s]: resultCode=%s → _approved_apis에서 제거", service_key, result_code)
            self._approved_apis.discard(service_key)

        if service_key == "pollen":
            for _k in _POLLEN_KINDS:
                self._pollen_cache[_k] = {"today": None, "tomorrow": None,
                                           "today_date": None, "tomorrow_date": None}

        if service_key not in self._pending_apis:
            self._pending_apis.add(service_key)

        name, url = _API_SERVICES.get(service_key, (service_key, ""))
        _LOGGER.warning("API 미신청 감지 [%s]: resultCode=%s → %s", service_key, result_code, url)

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
        self._pending_apis.discard(service_key)
        self._notified_unsubscribed.discard(service_key)

    def _mask_key(self, msg: str) -> str:
        msg_str = str(msg)
        if "serviceKey=" in msg_str:
            msg_str = re.sub(r"serviceKey=[^&'\" ]*", "serviceKey=********", msg_str)
        for key in (self._raw_api_key, self.api_key):
            if key and len(key) > 5 and key in msg_str:
                msg_str = msg_str.replace(key, "********")
        return msg_str

    _CALL_COUNT_KEY: dict[str, str] = {
        "VilageFcstInfoService_2.0": "단기예보",
        "MidFcstInfoService":        "중기예보",
        "MsrstnInfoInqireSvc":       "에어코리아_측정소",
        "ArpltnInforInqireSvc":      "에어코리아_대기",
        "WthrWrnInfoService":        "기상특보",
        "HealthWthrIdxServiceV3":    "꽃가루",
        "LivingWthrIdxServiceV5":    "자외선지수",
    }

    async def _fetch(self, url, params, headers=None, timeout=15, retry_log_level=logging.WARNING):
        if self.hass is not None:
            for fragment, key in self._CALL_COUNT_KEY.items():
                if fragment in url:
                    if hasattr(self, "_call_counter_ref") and self._call_counter_ref is not None:
                        self._call_counter_ref(key)
                    break

        for attempt in range(2):
            try:
                async with self.session.get(
                    url, params=params, headers=headers, timeout=timeout
                ) as response:
                    if response.status in (429, 500, 502, 503, 504):
                        _desc = "요청 한도 초과" if response.status == 429 else "일시적 서버 오류"
                        if attempt == 0:
                            _LOGGER.log(retry_log_level, "API HTTP %s 발생 (%s). 10초 후 재시도합니다. (%s)", response.status, _desc, self._mask_key(url))
                            await asyncio.sleep(10.0)
                            continue
                        _LOGGER.log(
                            retry_log_level,
                            "API HTTP %s 재시도 실패 (%s) → 이번 주기 데이터 생략, 캐시로 대체합니다. (%s)",
                            response.status, _desc, self._mask_key(url),
                        )
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
                        parsed = json.loads(text)
                    except (json.JSONDecodeError, ValueError):
                        _LOGGER.error("API 응답 파싱 실패 (%s): 알 수 없는 형식", self._mask_key(url))
                        return None
                    if not isinstance(parsed, dict):
                        _LOGGER.error(
                            "API 응답이 JSON 객체가 아닙니다 (%s): %s 수신 → 무시",
                            self._mask_key(url), type(parsed).__name__,
                        )
                        return None
                    return parsed
            except Exception as err:
                is_retryable = any(code in str(err) for code in ("429", "500", "502", "503", "504"))
                if attempt == 1 or not is_retryable:
                    _LOGGER.error("API 호출 실패 (%s): %s", self._mask_key(url), self._mask_key(err))
                    break
                await asyncio.sleep(3.0)
        return None

    def _extract_result_code(self, data: dict | None) -> str | None:
        if not data:
            return None
        if "_http_error" in data:
            err = data["_http_error"]
            if err == "429":
                return "429"
            if err in ("401", "403", "404"):
                # 인증 실패/미존재 서비스 → 실제 미신청·권한 문제로 취급
                return "30"
            # 500/502/503/504 등 일시적 서버 오류 → 미신청 판정에서 제외
            return None
        return (
            data.get("response", {})
                .get("header", {})
                .get("resultCode")
        )

    async def fetch_data(
        self,
        lat: float, lon: float,
        nx: int, ny: int,
        reg_id_temp: str, reg_id_land: str,
        warn_area_code: str | None,
        pollen_area_no: str,
        pollen_area_name: str,
        uv_area_no: str = "",
        uv_area_name: str = "",
    ) -> dict | None:
        self.lat, self.lon, self.nx, self.ny = lat, lon, nx, ny
        now = datetime.now(self.tz)

        async def _skip_coro(default):
            return default

        def _should_call(key: str) -> bool:
            return key in self._approved_apis or key in self._pending_apis

        short_res = mid_res = air_data = address = warning = pollen_data = uv_data = None

        try:
            short_res = await self._get_short_term(now)
            await asyncio.sleep(2.0)

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

            if _should_call("uv"):
                await asyncio.sleep(1.2)
                uv_data = await self._get_uv_index(now, uv_area_no, uv_area_name)
            else:
                uv_data = None
        except Exception as e:
            _LOGGER.error("데이터 수집 중 오류 발생: %s", self._mask_key(e))
            return None

        merged = self._merge_all(now, short_res, mid_res, air_data, address, warning, pollen_data, uv_data)
        if short_res == "UNSUBSCRIBED":
            merged["_short_unsubscribed"] = True
        if isinstance(mid_res, tuple) and mid_res[0] == "UNSUBSCRIBED":
            merged["_mid_unsubscribed"] = True
        return merged

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
                self._cached_station_code = None
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
                    _LOGGER.warning("에어코리아 측정소 재조회 실패: 조회 결과가 비어있습니다. 다음 주기에 재시도합니다.")
                    return {}
                sn = items[0].get("stationName")
                self._cached_station = sn
                self._cached_station_code = items[0].get("stationCode")
                self._cached_station_lat = lat
                self._cached_station_lon = lon
                _LOGGER.info(
                    "에어코리아 측정소 재조회 완료: '%s' (측정소코드: %s)",
                    sn, self._cached_station_code,
                )

            air_json = await self._fetch(
                "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty",
                {"serviceKey": self.api_key, "returnType": "json",
                 "stationName": sn, "dataTerm": "daily", "ver": "1.5"},
                # 대기질은 실패해도 에어코리아 실시간 조회로 보완할 수 있으므로,
                # 재시도 단계의 5xx 로그는 굳이 경고로 남기지 않는다(debug).
                # 진짜 경고는 보완까지 실패했을 때만 아래에서 별도로 남긴다.
                retry_log_level=logging.DEBUG,
            )
            code = self._extract_result_code(air_json)
            if code and self._check_unsubscribed("air", code):
                return {"station": sn}

            ai_list = (air_json.get("response", {}).get("body", {}).get("items", [])
                       if air_json else [])
            if not ai_list:
                page_result = {}
                try:
                    city_name = (await self._get_address(lat, lon)).split()[0] if lat and lon else ""
                    page_result = await self._fetch_page_air_quality(
                        self._cached_station_code, city_name
                    )
                except Exception as e:
                    _LOGGER.debug("대기질 페이지 보완 확인 실패 (무시): %s", self._mask_key(e))
                if page_result:
                    _LOGGER.info(
                        "대기질 보완: API 응답이 비어있어 에어코리아 실시간 조회에서 "
                        "측정소 '%s'의 실측값을 가져와 채웁니다.", sn,
                    )
                    p10v = page_result.get("pm10Value")
                    p25v = page_result.get("pm25Value")
                    o3v = page_result.get("o3Value")
                    return {
                        "pm10Value": p10v,
                        "pm10Grade": self._get_air_grade(p10v, "pm10") if p10v else None,
                        "pm25Value": p25v,
                        "pm25Grade": self._get_air_grade(p25v, "pm25") if p25v else None,
                        "o3Value": o3v,
                        "o3Grade": self._get_air_grade(o3v, "o3") if o3v else None,
                        "station": sn,
                    }
                # API도, 보완용 실시간 조회도 둘 다 실패했을 때만 실제로 경고한다.
                _LOGGER.warning(
                    "대기질 조회 실패: API와 에어코리아 실시간 조회(보완) 둘 다 "
                    "값을 가져오지 못했습니다 (측정소 '%s'). 다음 주기에 재시도합니다.", sn,
                )
                return {"station": sn}

            ai = ai_list[0]
            self._mark_approved("air")
            p10v = ai.get("pm10Value")
            p25v = ai.get("pm25Value")
            o3v = ai.get("o3Value")

            return {
                "pm10Value": p10v,
                "pm10Grade": self._get_air_grade(p10v, "pm10"),
                "pm25Value": p25v,
                "pm25Grade": self._get_air_grade(p25v, "pm25"),
                "o3Value": o3v,
                "o3Grade": self._get_air_grade(o3v, "o3"),
                "station": sn,
            }
        except Exception as e:
            _LOGGER.error("에어코리아 데이터 호출 실패: %s", self._mask_key(e))
            return {"station": sn} if sn else {}

    def _get_air_grade(self, val: object, p_type: str) -> str:
        v = _safe_float(val)
        if v is None:
            return "정보없음"

        if p_type == "pm10":
            if v <= 30: return "좋음"
            if v <= 80: return "보통"
            if v <= 150: return "나쁨"
            return "매우나쁨"

        if p_type == "o3":
            if v <= 0.030: return "좋음"
            if v <= 0.090: return "보통"
            if v <= 0.150: return "나쁨"
            return "매우나쁨"

        if v <= 15: return "좋음"
        if v <= 35: return "보통"
        if v <= 75: return "나쁨"
        return "매우나쁨"

    async def _get_short_term(self, now: datetime) -> dict | None:
        adj = now - timedelta(minutes=10)
        hour = adj.hour

        if hour >= 23 or hour < 2:
            base_h = 20
            base_d = (adj - timedelta(days=1)).strftime("%Y%m%d") if hour < 2 else adj.strftime("%Y%m%d")
        else:
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
            return "UNSUBSCRIBED"
        items = (data or {}).get("response", {}).get("body", {}).get("items", {}).get("item", [])
        if items:
            self._mark_approved("short")
        return data

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
                    return ("UNSUBSCRIBED", None, tm_fc_dt)

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

    async def _get_warning(self, warn_area_code: str | None) -> str | None:
        if not warn_area_code:
            return None
        try:
            # ── 1단계: API로 특보현황 파악 ───────────────────────────────
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

            if code in ("00", "03"):
                self._mark_approved("warning")

            items = (
                data.get("response", {})
                    .get("body", {})
                    .get("items", {})
                    .get("item", [])
            )

            latest: dict[str, dict] = {}
            for item in items:
                key = str(item.get("warnVar", ""))
                tfc = item.get("tmFc", 0)
                if key not in latest or tfc > latest[key].get("tmFc", 0):
                    latest[key] = item

            active = [
                item for item in latest.values()
                # 1=발표, 3=연장, 6=정정, 7=변경발표 → 전부 "지금 유효한 특보"임
                # 2=해제, 8=변경해제는 제외
                if str(item.get("command", "")) in ("1", "3", "6", "7")
                and str(item.get("cancel", "1")) == "0"
                and str(item.get("endTime", "1")) == "0"
            ]

            # warnVar별로 표시할 이름을 결정해 딕셔너리에 담는다 (병합의 기준)
            api_result: dict[str, str] = {}  # warnVar -> 표시명
            for item in active:
                wv = str(item.get("warnVar", ""))
                pair = _WARN_TYPE_MAP.get(wv)
                if pair:
                    stress = str(item.get("warnStress", "0"))
                    if stress == "2" and len(pair) > 2:
                        api_result[wv] = pair[2]
                    elif stress == "1":
                        api_result[wv] = pair[1]
                    else:
                        api_result[wv] = pair[0]

            # ── 2·3단계: 페이지로 검증하고, API에 없는 미해제 특보를 병합 ──
            area_name = await self._get_warn_area_display_name(warn_area_code)
            if area_name:
                try:
                    ancestors = await self._get_warn_area_ancestors(warn_area_code)
                    page_result = await self._fetch_page_warnings_for_area(area_name, ancestors)
                    for wv, name in page_result.items():
                        if wv not in api_result:
                            _LOGGER.info(
                                "특보 보완: '%s'(%s)이 API 6일 조회에는 없지만 "
                                "기상청 실시간 특보 현황 페이지에서 확인되어 추가합니다.",
                                name, area_name,
                            )
                            api_result[wv] = name
                except Exception as e:
                    _LOGGER.debug("특보 페이지 보완 확인 실패 (무시): %s", self._mask_key(e))

            if not api_result:
                return "특보없음"
            return ", ".join(api_result.values())

        except Exception as e:
            _LOGGER.error("기상특보 조회 오류: %s", self._mask_key(e))
            return None

    async def _get_warn_area_display_name(self, warn_area_code: str) -> str | None:
        """
        특보구역코드(L코드)를 weather.go.kr 표시명으로 변환한다.
        JSON 파일 읽기는 블로킹 호출이라, 이벤트 루프를 막지 않도록
        hass.async_add_executor_job으로 감싸서 실행한다.
        (hass가 없는 테스트 환경 등에서는 동기 로드로 폴백한다.)
        """
        if not _WARN_AREA_NAMES:
            try:
                if self.hass is not None:
                    await self.hass.async_add_executor_job(_load_warn_area_names)
                else:
                    _load_warn_area_names()
            except Exception as e:
                _LOGGER.debug("특보구역명 매핑 로드 실패: %s", e)
                return None
        return _WARN_AREA_NAMES.get(warn_area_code)

    async def _get_warn_area_ancestors(self, warn_area_code: str) -> list[str]:
        """
        특보구역코드(L코드)의 상위(도/시) 이름 목록을 반환한다
        (가까운 부모 → 최상위 순, 예: "청주동부" → ["청주", "충청북도"]).
        weather.go.kr 요약이 "충청북도"처럼 광역 단위를 통째로 표기할 때,
        area_name 문자열만으로는 하위지역 소속을 알 수 없는 경우를 위해 사용.
        """
        if not _WARN_AREA_ANCESTORS:
            try:
                if self.hass is not None:
                    await self.hass.async_add_executor_job(_load_warn_area_ancestors)
                else:
                    _load_warn_area_ancestors()
            except Exception as e:
                _LOGGER.debug("특보구역 상위지역 매핑 로드 실패: %s", e)
                return []
        return _WARN_AREA_ANCESTORS.get(warn_area_code, [])

    def _get_airkorea_session(self) -> aiohttp.ClientSession:
        """에어코리아 실시간 조회(key 발급 → 조회) 전용 세션을 반환한다.
        없으면 새로 만든다. HA 공유 세션과 분리하는 이유는 클래스 위
        _airkorea_session 필드 주석 참고."""
        if self._airkorea_session is None or self._airkorea_session.closed:
            self._airkorea_session = aiohttp.ClientSession()
        return self._airkorea_session

    async def async_close(self) -> None:
        """통합구성요소 언로드 시 호출 — 전용 세션이 열려 있으면 닫는다."""
        if self._airkorea_session is not None and not self._airkorea_session.closed:
            await self._airkorea_session.close()

    async def _fetch_page_air_quality(self, station_code: str, city_name: str) -> dict:
        """
        API(getMsrstnAcctoRltmMesureDnsty)가 실패했을 때 보완용으로,
        airkorea.or.kr의 실시간 차트 조회(getRealChart)에서 오늘자
        PM10/PM2.5/오존 최신 실측값을 가져온다.

        이 엔드포인트는 측정소코드(station_code) 기준으로 정확히 조회되고,
        미세먼지·초미세먼지·오존을 한 번에 JSON으로 준다(표 긁기보다 정확·단순).
        다만 호출 전에 "key"라는 값이 있어야 하는데, 이건 로그인이나 특정
        세션에 묶인 게 아니라 실시간 조회 화면(realSearch)에 아무 값이나
        채워서 한 번 들어가면 그 응답 HTML 안에 `var key = '...'`로 새로
        발급되는 값이다 — 측정소와도 무관하게 항상 새로 발급되므로, 여기선
        더미 값으로 그 페이지를 한 번 "방문"해서 key만 뽑아 쓴다.

        HA 재시작 직후처럼 네트워크/DNS가 아직 안정화되기 전이면 이 2단계
        요청이 일시적으로 실패할 수 있어, 한 번 더 시도해본다(약 5초 간격).
        그래도 안 되면 포기하고 다음 폴링 주기에 다시 시도한다.

        station_code: getNearbyMsrstnList가 돌려준 측정소코드(예: "111312").
        city_name: 시/도명(예: "서울특별시"). 실패하거나 매핑 안 되는
        시/도면 빈 딕셔너리를 반환한다(API 결과 그대로 유지).

        진단 로그는 전부 별도 로거(_AIRKOREA_LOGGER, 이름:
        custom_components.kma_weather.api_kma.airkorea_fallback)로 남긴다.
        api_kma 전체를 debug로 켜지 않고 이 흐름만 targeting해서 볼 수 있다.
        """
        if not station_code:
            _AIRKOREA_LOGGER.debug("캐시된 측정소코드가 없어 건너뜀")
            return {}
        code = _airkorea_district_code(city_name)
        if not code:
            _AIRKOREA_LOGGER.debug("시/도(%s)를 district 코드로 매핑 못함", city_name)
            return {}

        async def _attempt() -> dict | None:
            now = datetime.now(self.tz)
            today = now.strftime("%Y-%m-%d")
            today_compact = now.strftime("%Y%m%d")
            try:
                session = self._get_airkorea_session()
                # 1단계: key 발급 — 측정소 정보는 더미로 채워도 무방(실측 확인됨)
                async with session.post(
                    "https://www.airkorea.or.kr/web/realSearch",
                    data={
                        "pMENU_NO": "97", "schFlag": "1", "myDistrict": code,
                        "dateDiv": "1", "from_date": today, "from_date_hour": "00",
                        "to_date": today, "to_date_hour": "23",
                        "key": "0", "tm_x": "0", "tm_y": "0", "tm": "0.0",
                        "stationId": "stationCode0", "station": "0",
                        "station_mang": "3", "station_code": " ",
                        "loading": "yes", "leftShow": "realTime",
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=10,
                ) as resp:
                    if resp.status != 200:
                        _AIRKOREA_LOGGER.debug("realSearch HTTP %s (key 발급 실패)", resp.status)
                        return None
                    html = await resp.text()
                m = re.search(r"var key = '([^']+)'", html)
                if not m:
                    _AIRKOREA_LOGGER.debug(
                        "realSearch 응답(길이=%d)에서 key를 못 찾음 "
                        "(사이트 마크업이 바뀌었을 수 있음)", len(html),
                    )
                    return None
                key = m.group(1)

                # 2단계: 그 key로 실제 측정소의 실시간 값 조회 (같은 전용 세션 →
                # 같은 쿠키로 이어서 요청해야 key와 세션이 일치한다)
                async with session.post(
                    "https://www.airkorea.or.kr/web/pollution/getRealChart",
                    data={
                        "dateDiv": "1", "stationCode": station_code,
                        "from_date": f"{today_compact}00", "to_date": f"{today_compact}23",
                        "key": key, "token": "",
                    },
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Referer": "https://www.airkorea.or.kr/web/realSearch",
                    },
                    timeout=10,
                ) as resp:
                    if resp.status != 200:
                        _AIRKOREA_LOGGER.debug("getRealChart HTTP %s", resp.status)
                        return None
                    return await resp.json(content_type=None)
            except Exception as e:
                _AIRKOREA_LOGGER.debug("요청 중 예외 발생 (%s): %s", type(e).__name__, e)
                return None

        data = await _attempt()
        if data is None:
            _AIRKOREA_LOGGER.debug("1차 시도 실패 → 5초 후 재시도")
            await asyncio.sleep(5.0)
            data = await _attempt()
            if data is None:
                _AIRKOREA_LOGGER.debug("재시도도 실패 → 이번 주기 포기")
                return {}

        charts = data.get("charts") or []
        if not charts:
            _AIRKOREA_LOGGER.debug("getRealChart 응답에 charts가 비어있음")
        result: dict = {}

        def _valid(v) -> bool:
            return v is not None and str(v).strip() not in ("", "-")

        # charts는 최신 시각이 먼저 오지만, 혹시 몰라 안전하게 값이 있는
        # 첫 항목(가장 최근 관측치)을 그대로 채택한다.
        for c in charts:
            if "pm10Value" not in result and _valid(c.get("VALUE_10007")):
                result["pm10Value"] = str(c["VALUE_10007"])
            if "pm25Value" not in result and _valid(c.get("VALUE_10008")):
                result["pm25Value"] = str(c["VALUE_10008"])
            if "o3Value" not in result and _valid(c.get("VALUE_10003")):
                result["o3Value"] = str(c["VALUE_10003"])
            if all(k in result for k in ("pm10Value", "pm25Value", "o3Value")):
                break
        if not result:
            _AIRKOREA_LOGGER.debug(
                "charts %d건 다 훑었지만 유효한 값이 없음 "
                "(측정소코드 '%s'가 이 charts에 없거나 전부 결측치)",
                len(charts), station_code,
            )
        return result

    def _notify_warn_page_failure(self, reason: str) -> None:
        """
        특보 보완 확인용 페이지 조회 실패를 알린다. 매 폴링마다 반복 실패해도
        로그가 도배되지 않도록, 이미 알린 상태면 WARNING 대신 DEBUG로만 남긴다.
        """
        if self._warn_page_fetch_failed_notified:
            _LOGGER.debug("특보 현황 페이지 조회 계속 실패 중: %s", self._mask_key(reason))
            return
        self._warn_page_fetch_failed_notified = True
        _LOGGER.warning(
            "기상특보 보완 확인용 페이지(weather.go.kr) 조회에 실패했습니다: %s. "
            "API(getPwnCd) 결과만으로 특보를 표시합니다. "
            "(이 경고는 반복 실패 중 최초 1회만 표시되며, 페이지 조회가 다시 "
            "성공하면 이후 실패 시 재알림됩니다.)",
            self._mask_key(reason),
        )

    async def _fetch_page_warnings_for_area(
        self, area_name: str, ancestors: list[str] | None = None
    ) -> dict[str, str]:
        """
        weather.go.kr에서 area_name에 해당하는 "현재 유효한" 특보를
        {warnVar: 표시명} 형태로 반환한다.

        주의: 이 페이지에는 성격이 다른 두 영역이 있다.
        - "특보 상세내역 보기" 표(id="current-warnings")는 최근 며칠~2주 치
          발표 이력을 시각/지역 컬럼과 함께 나열한 로그이며, "지금 유효함"을
          뜻하지 않는다. 예전에는 이 표를 잘못 긁어와서, 오래된 이력이 최신
          상태를 덮어쓰거나 아직 유효한 특보가 지역 매칭 실패로 누락되는
          문제가 있었다.
        - "특보 발효현황" 제목 아래 "특보 내용" 블록("o 열대야주의보 : 지역...")이
          실제로 지금 유효한 특보만 정리한 요약이다. 이 함수는 이 블록만 파싱한다.

        페이지 조회/파싱에 실패하면 빈 딕셔너리를 반환한다(API 결과는 그대로 유지됨).
        실패는 매 폴링(1시간)마다 반복될 수 있어 로그 도배를 피하려고
        "처음 실패했을 때 한 번만 경고"하고, 다시 성공하면 알림 상태를 초기화해서
        다음에 또 실패하면 재차 경고할 수 있게 한다.
        """
        try:
            async with self.session.get(
                "https://www.weather.go.kr/w/wnuri-fct2021/weather/warning.do",
                timeout=10,
            ) as resp:
                if resp.status != 200:
                    self._notify_warn_page_failure(f"HTTP {resp.status}")
                    return {}
                html = await resp.text()
        except Exception as e:
            self._notify_warn_page_failure(str(e))
            return {}

        # 조회 성공 → 다음 실패 시 다시 경고할 수 있도록 알림 상태 초기화
        self._warn_page_fetch_failed_notified = False

        result: dict[str, str] = {}

        m = re.search(r"특보 발효현황.*?<p class=\"tit\">(.*?)</p>", html, re.DOTALL)
        if not m:
            _LOGGER.debug("특보 페이지에서 '특보 발효현황' 요약 블록을 찾지 못함 (마크업 변경 가능성)")
            return result

        # "o 열대야주의보 : 지역목록" 형태의 줄들이 <br />로 구분되어 있다.
        for line in re.split(r"<br\s*/?>", m.group(1)):
            line = re.sub(r"<[^>]+>", "", line).strip()
            if not line.startswith("o "):
                continue
            line = line[2:].strip()
            parts = re.split(r"[:：]", line, maxsplit=1)
            if len(parts) != 2:
                continue
            name, region_cell = parts[0].strip(), parts[1].strip()

            if not _area_in_region_cell(area_name, region_cell, ancestors):
                continue

            pair = _FULL_NAME_TO_WV.get(name)
            if not pair:
                continue
            wv, display_name = pair
            result[wv] = display_name

        return result

    async def _get_pollen(self, now: datetime, area_no: str, area_name: str) -> dict | None:
        month = now.month
        in_season = {
            k: _POLLEN_SEASONS[k][0] <= month <= _POLLEN_SEASONS[k][1]
            for k in ("oak", "pine", "grass")
        }
        offseason = not any(in_season.values())
        today_str = now.strftime("%Y%m%d")
        h = now.hour
        prev_str = (now - timedelta(days=1)).strftime("%Y%m%d")

        for k in _POLLEN_KINDS:
            c = self._pollen_cache[k]
            if c["today_date"] and c["today_date"] != today_str:
                c["today"] = None
                c["today_date"] = None

        if offseason and "pollen" in self._approved_apis:
            return {
                "oak": "좋음", "pine": "좋음", "grass": "좋음", "worst": "좋음",
                "area_name": area_name, "area_no": area_no, "announcement": "비시즌",
            }

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
                # 미신청 코드는 아니므로 구독 자체는 유효하다고 판단.
                # 승인 처리를 안 하면 이 지역은 영원히 _pending 상태에 머물러
                # 센서 자체가 생성되지 않는 사각지대가 발생하므로 여기서도 승인 처리한다.
                self._mark_approved("pollen")
                grades = {k: (None if in_season[k] else "좋음") for k in _POLLEN_KINDS}
                season_known = [g for k, g in grades.items() if in_season[k] and g is not None]
                worst = None
                return {**grades, "worst": worst,
                        "area_name": area_name, "area_no": area_no,
                        "announcement": "데이터없음"}
            if check_code == "00":
                self._mark_approved("pollen")

        def _ann(date_str: str, hour: int) -> str:
            return f"{date_str[:4]}년 {date_str[4:6]}월 {date_str[6:]}일 {hour:02d}시 발표"

        base_url = "https://apis.data.go.kr/1360000/HealthWthrIdxServiceV3"
        endpoints = {
            "pine":  f"{base_url}/getPinePollenRiskIdxV3",
            "oak":   f"{base_url}/getOakPollenRiskIdxV3",
            "grass": f"{base_url}/getWeedsPollenRiskndxV3",
        }

        async def _fetch_one(kind: str, time_str: str, fetch_key: str) -> str | None:
            if not in_season[kind]:
                return "좋음"
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
                return None
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
            if code == "99": return None
            return _POLLEN_GRADE.get(str(val)) if val else None

        async def _get_grade(kind: str) -> str | None:
            if not in_season[kind]:
                return "좋음"

            c = self._pollen_cache[kind]
            ann_today = _ann(today_str, 6)
            ann_18    = _ann(today_str, 18)
            ann_prev  = _ann(prev_str, 18)

            if h < 7:
                if c["tomorrow"] is None:
                    g = await _fetch_one(kind, prev_str + "18", "tomorrow")
                    if g == "UNSUB": return "UNSUB"
                    if g is not None:
                        c["tomorrow"] = g
                        c["tomorrow_date"] = today_str
                return c["tomorrow"]

            else:
                if c["today"] is not None:
                    if h >= 19 and c["tomorrow_date"] != today_str:
                        g = await _fetch_one(kind, today_str + "18", "tomorrow")
                        if g == "UNSUB": return "UNSUB"
                        if g is not None:
                            c["tomorrow"] = g
                            c["tomorrow_date"] = today_str
                        else:
                            c["tomorrow"] = None
                            c["tomorrow_date"] = None
                    return c["today"]

                if c["today_date"] != today_str:
                    g = await _fetch_one(kind, today_str + "06", "today")
                    if g == "UNSUB": return "UNSUB"
                    if g is not None:
                        c["today"] = g
                        c["today_date"] = today_str
                        if h >= 19 and c["tomorrow_date"] != today_str:
                            tg = await _fetch_one(kind, today_str + "18", "tomorrow")
                            if tg is not None and tg != "UNSUB":
                                c["tomorrow"] = tg
                                c["tomorrow_date"] = today_str
                        return c["today"]

                if c["tomorrow"] is None:
                    g = await _fetch_one(kind, prev_str + "18", "tomorrow")
                    if g == "UNSUB": return "UNSUB"
                    if g is not None:
                        c["tomorrow"] = g
                        c["tomorrow_date"] = today_str
                return c["tomorrow"]

        try:
            pine_g = await _get_grade("pine")
            await asyncio.sleep(1.2)
            oak_g = await _get_grade("oak")
            await asyncio.sleep(1.2)
            grass_g = await _get_grade("grass")

            # 시즌 중인 항목의 실제 API 조회에서 미신청/구독 해지가 확인되면
            # (자잘한 "이 시점 데이터 없음"과 구분되는 진짜 해지 신호이므로)
            # 특정 항목만 빈 값으로 두지 않고 센서 전체를 unavailable로 처리한다.
            if any(
                in_season[k] and g == "UNSUB"
                for k, g in (("pine", pine_g), ("oak", oak_g), ("grass", grass_g))
            ):
                for kk in _POLLEN_KINDS:
                    self._pollen_cache[kk] = {"today": None, "tomorrow": None,
                                               "today_date": None, "tomorrow_date": None}
                return None

            pine_g = None if pine_g == "UNSUB" else pine_g
            oak_g = None if oak_g == "UNSUB" else oak_g
            grass_g = None if grass_g == "UNSUB" else grass_g

            if pine_g is None and oak_g is None and grass_g is None:
                return {} if not any(in_season.values()) else {}

            order = ["좋음", "보통", "나쁨", "매우나쁨"]
            season_known = [
                g for k, g in [("pine", pine_g), ("oak", oak_g), ("grass", grass_g)]
                if in_season[k] and g is not None
            ]
            worst = max(season_known, key=lambda g: order.index(g)) if season_known else None

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

    @staticmethod
    def _get_uv_grade(val: object) -> str | None:
        """자외선지수(정수/실수 문자열)를 등급으로 변환한다.
        낮음 0~2, 보통 3~5, 높음 6~7, 매우높음 8~10, 위험 11이상."""
        v = _safe_float(val)
        if v is None:
            return None
        for threshold, grade in _UV_GRADES:
            if v < threshold:
                return grade
        return _UV_GRADE_MAX

    @staticmethod
    def _calc_uv_base_time(now: datetime) -> str:
        """자외선지수는 00,03,06,...,21시(KST)에 하루 8회 발표된다.
        지금 시각 기준으로 가장 최근에 발표됐을 시각을 YYYYMMDDHH로 반환한다."""
        today_str = now.strftime("%Y%m%d")
        h = now.hour
        base_h = (h // 3) * 3
        return f"{today_str}{base_h:02d}"

    async def _get_uv_index(self, now: datetime, area_no: str, area_name: str) -> dict | None:
        """
        자외선지수를 조회한다. 대기질/꽃가루와 달리 별도 보완 수단(웹 스크래핑
        폴백)은 없다 — API가 실패하면 그냥 이번 주기는 빈 값이 된다. 기상청
        API는 5xx 재시도로 대부분의 일시 장애가 이미 흡수되기 때문에, 별도
        폴백 없이도 실사용에 큰 문제는 없을 것으로 판단해 우선 API 단독으로
        구현한다.
        """
        if not area_no:
            return None

        base_time = self._calc_uv_base_time(now)

        try:
            r = await self._fetch(
                "https://apis.data.go.kr/1360000/LivingWthrIdxServiceV5/getUVIdxV5",
                {"serviceKey": self.api_key, "dataType": "JSON",
                 "areaNo": area_no, "time": base_time,
                 "numOfRows": "1", "pageNo": "1"},
            )
            code = self._extract_result_code(r)
            if code and self._check_unsubscribed("uv", code):
                return None
            if code and code not in ("00", "03"):
                # 03(NODATA)은 아직 그 시각 자료가 안 올라온 경우로 흔히
                # 있을 수 있어 오류로 취급하지 않고 그냥 빈 값으로 넘어간다.
                return {}

            self._mark_approved("uv")

            items = (r.get("response", {}).get("body", {}).get("items", {})
                     if r else {})
            if isinstance(items, dict):
                items = items.get("item", [])
            if not items:
                return {}
            item = items[0] if isinstance(items, list) else items

            val = item.get("h0")
            grade = self._get_uv_grade(val)
            date_raw = item.get("date", "")
            ann = (
                f"{date_raw[:4]}년 {date_raw[4:6]}월 {date_raw[6:8]}일 {date_raw[8:10]}시 발표"
                if len(date_raw) >= 10 else base_time
            )

            # h0(지금)은 상태값으로 이미 쓰고, h3~h75(다음 시간부터 78시간 후까지
            # 3시간 간격)를 강수량 센서(hourly_precipitation_mm)와 같은 방식으로
            # 속성에 펼쳐 담는다. 하루를 넘어가는 범위라 "13시"처럼 시각만 쓰면
            # 날짜가 헷갈리므로 "월/일 시" 형식으로 표기한다.
            try:
                base_dt = datetime.strptime(base_time, "%Y%m%d%H").replace(tzinfo=self.tz)
            except ValueError:
                base_dt = now
            hourly: dict[str, float] = {}
            for offset in range(3, 76, 3):
                v = item.get(f"h{offset}")
                fv = _safe_float(v)
                if fv is None:
                    continue
                label = (base_dt + timedelta(hours=offset)).strftime("%m/%d %H시")
                hourly[label] = fv

            return {
                "value": val, "grade": grade,
                "area_name": area_name, "area_no": area_no, "announcement": ann,
                "hourly": hourly,
            }
        except Exception as e:
            _LOGGER.error("자외선지수 조회 중 오류 발생: %s", self._mask_key(e))
            return {}

    # ── 유틸리티 ────────────────────────────────────────────────────────────

    def _calculate_apparent_temp(self, temp, reh, wsd):
        """
        기상청 공식 체감온도 계산.

        - 기온 ≤ 10°C + 풍속 ≥ 4.8km/h : 겨울철 체감온도(Wind Chill)
        - 기온 ≥ 25°C + 습도 데이터 있음 : 여름철 체감온도
          2022.6.2.부터 적용된 기상청 공식(Steadman 1979 한국형 변형식).
          습구온도(Tw)는 Stull(2011) 추정식으로 계산.
          (예전에 쓰던 미국 NWS Rothfusz Heat Index는 기상청이 실제
          쓰는 공식이 아니었고, 고온 구간에서 실제보다 크게 높게
          나오는 문제가 있어 이 공식으로 교체함)
        - 그 외                          : 기온 그대로 반환
        """
        t, rh, v = _safe_float(temp), _safe_float(reh), _safe_float(wsd)
        if t is None:
            return temp
        v_kmh = v * 3.6 if v is not None else 0

        # 겨울철 체감온도(Wind Chill): 10°C 이하 + 풍속 4.8km/h 이상
        if t <= 10 and v_kmh >= 4.8:
            return round(
                13.12 + 0.6215 * t
                - 11.37 * (v_kmh ** 0.16)
                + 0.3965 * t * (v_kmh ** 0.16),
                1,
            )

        # 여름철 체감온도: 25°C 이상 + 습도 데이터 있음
        # 기상청 공식(2022.6.2.~), Stull 습구온도 추정식 사용
        if t >= 25 and rh is not None:
            rh_c = max(0.0, min(100.0, rh))  # 추정식이 0~100% 범위를 전제로 함
            tw = (
                t * math.atan(0.151977 * (rh_c + 8.313659) ** 0.5)
                + math.atan(t + rh_c)
                - math.atan(rh_c - 1.67633)
                + 0.00391838 * (rh_c ** 1.5) * math.atan(0.023101 * rh_c)
                - 4.686035
            )
            at = -0.2442 + 0.55399 * tw + 0.45535 * t - 0.0022 * tw * tw + 0.00278 * tw * t + 3.0
            return round(at, 1)

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

    def _merge_all(self, now, short_res, mid_res, air_data, address=None, warning=None, pollen_data=None, uv_data=None):
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
        _best_slot_dt = None  # 상태값(precip_amount)이 참조한 실제 슬롯 시각 → 속성 시작점 기준
        if today_str in forecast_map:
            times = sorted(forecast_map[today_str].keys())
            best_t = next((t for t in times if t >= curr_h), times[-1] if times else None)
            if best_t:
                weather_data.update(forecast_map[today_str][best_t])
                _best_slot_dt = datetime(
                    int(today_str[:4]), int(today_str[4:6]), int(today_str[6:]),
                    int(best_t[:2]), int(best_t[2:]), tzinfo=ZoneInfo("Asia/Seoul"),
                )
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

        def _parse_precip(val, no_val):
            if not val or val in (no_val, "-"): return 0.0
            if "미만" in str(val): return 0.5
            s = str(val)
            if "~" in s:
                # 기상청이 강수량이 불확실할 때 "30.0~50.0mm"처럼 범위로 주는 경우.
                # 최소 이만큼은 온다는 보수적 기준으로 하한값을 대표값으로 사용한다.
                lo_str, hi_str = s.split("~", 1)
                lo = _safe_float("".join(c for c in lo_str if c.isdigit() or c == "."))
                hi = _safe_float("".join(c for c in hi_str if c.isdigit() or c == "."))
                if lo is not None:
                    return lo
                if hi is not None:
                    return hi
                _LOGGER.warning("강수량 범위 값 파싱 실패(API 데이터 이상): %r → 0 처리", val)
                return 0.0
            digits = "".join(c for c in s if c.isdigit() or c == ".")
            parsed = _safe_float(digits)
            if parsed is None and digits:
                _LOGGER.warning("강수량 값 파싱 실패(API 데이터 이상): %r → 0 처리", val)
            return parsed if parsed is not None else 0.0

        def _fmt_num(n):
            return str(int(n)) if n == int(n) else str(n)

        def _precip_display(val, no_val):
            """속성(hourly_precipitation_mm)에 표시할 값.
            일반 수치는 기존처럼 숫자 그대로, "1mm 미만"은 "~1",
            범위("30.0~50.0mm")는 "30~50"처럼 사람이 읽기 쉬운 형태로 반환한다."""
            if not val or val in (no_val, "-"): return 0
            s = str(val)
            if "미만" in s: return "~1"
            if "~" in s:
                lo_str, hi_str = s.split("~", 1)
                lo = _safe_float("".join(c for c in lo_str if c.isdigit() or c == "."))
                hi = _safe_float("".join(c for c in hi_str if c.isdigit() or c == "."))
                if lo is None and hi is None:
                    return 0
                lo_disp = _fmt_num(lo) if lo is not None else "?"
                hi_disp = _fmt_num(hi) if hi is not None else "?"
                return f"{lo_disp}~{hi_disp}"
            digits = "".join(c for c in s if c.isdigit() or c == ".")
            parsed = _safe_float(digits)
            return parsed if parsed is not None else 0

        now_str = now.strftime("%Y%m%d")
        now_time_str = f"{now.hour:02d}00"
        today = now.date()
        for d_str in sorted(forecast_map.keys()):
            target_day = date(int(d_str[:4]), int(d_str[4:6]), int(d_str[6:]))
            diff = (target_day - today).days
            if diff < 0 or diff > 2:
                continue
            rain_times = [
                t_str for t_str in sorted(forecast_map[d_str].keys())
                if _safe_float(forecast_map[d_str][t_str].get("PTY", "0")) > 0
                and (d_str > now_str or (d_str == now_str and t_str >= now_time_str))
            ]
            if rain_times:
                t = rain_times[0]
                if diff == 0:
                    day_label = "오늘"
                elif diff == 1:
                    day_label = "내일"
                else:
                    day_label = f"모레({target_day.month}/{target_day.day})"
                hour, minute = int(t[:2]), int(t[2:])
                pty_val = str(forecast_map[d_str][t].get("PTY", "0"))
                label = _PTY_LABEL.get(pty_val, "강수")
                if minute > 0:
                    weather_data["rain_start_time"] = f"{day_label} {hour}시 {minute}분 {label}"
                else:
                    weather_data["rain_start_time"] = f"{day_label} {hour}시 {label}"
                break

        twice_daily, daily_forecast = [], []

        def _max_pop(day_slots: dict, time_keys: list[str]) -> float | None:
            """주어진 시간대(time_keys) 슬롯들 중 강수확률(POP) 최댓값. 데이터 없으면 None."""
            pops = [
                _safe_float(day_slots[t].get("POP"))
                for t in time_keys if t in day_slots and day_slots[t].get("POP") is not None
            ]
            pops = [p for p in pops if p is not None]
            return max(pops) if pops else None


        for i in range(10):
            target_date = now + timedelta(days=i)
            d_str = target_date.strftime("%Y%m%d")
            t_max = t_min = wf_am = wf_pm = pop_am = pop_pm = None

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

                        if h_now < 12:
                            am_range = [t for t in all_times if h_now <= int(t[:2]) < 12]
                            wf_am = get_range_freq(am_range, "1100")
                        else:
                            am_range = []
                            wf_am = get_range_freq([], "1100")

                        pm_start = max(12, h_now)
                        pm_range = [t for t in all_times if pm_start <= int(t[:2]) < 24]
                        wf_pm = get_range_freq(pm_range, "2300")

                        # 강수확률도 날씨상태와 동일한 시간 범위 사용 (범위 없으면 fallback 슬롯)
                        pop_am = _max_pop(today_slots, am_range or ["1100"])
                        pop_pm = _max_pop(today_slots, pm_range or ["2300"])
                    elif i == 1:
                        if "1200" in forecast_map[d_str]:
                            noon_slot = forecast_map[d_str]["1200"]
                            wf_noon = self._get_sky_kor(noon_slot.get("SKY"), noon_slot.get("PTY"))
                            wf_am = wf_pm = wf_noon
                            pop_noon = _safe_float(noon_slot.get("POP"))
                            pop_am = pop_pm = pop_noon
                        else:
                            wf_am, wf_pm = self._get_short_ampm(forecast_map[d_str])
                            pop_am = _max_pop(forecast_map[d_str], [f"{h:02d}00" for h in range(6, 12)])
                            pop_pm = _max_pop(forecast_map[d_str], [f"{h:02d}00" for h in range(12, 18)])
                    else:
                        wf_am, wf_pm = self._get_short_ampm(forecast_map[d_str])
                        pop_am = _max_pop(forecast_map[d_str], [f"{h:02d}00" for h in range(6, 12)])
                        pop_pm = _max_pop(forecast_map[d_str], [f"{h:02d}00" for h in range(12, 18)])
                else:
                    pop_am = pop_pm = None

                _LOGGER.debug("단기예보 i=%d date=%s t_max=%s t_min=%s", i, d_str, t_max, t_min)

            else:
                pop_am = pop_pm = None
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
                        # 중기예보도 강수확률(rnSt{n}Am/Pm) 필드를 API가 실제로 제공함
                        pop_am = _safe_float(mid_land.get(f"rnSt{mid_day_idx}Am"))
                        pop_pm = _safe_float(mid_land.get(f"rnSt{mid_day_idx}Pm"))
                elif i <= 5 and d_str in forecast_map:
                    short_temps = [
                        _safe_float(v.get("TMP"))
                        for v in forecast_map[d_str].values() if "TMP" in v
                    ]
                    valid_temps = [t for t in short_temps if t is not None]
                    t_max = max(valid_temps) if valid_temps else None
                    t_min = min(valid_temps) if valid_temps else None
                    wf_am, wf_pm = self._get_short_ampm(forecast_map[d_str])
                    pop_am = _max_pop(forecast_map[d_str], [f"{h:02d}00" for h in range(6, 12)])
                    pop_pm = _max_pop(forecast_map[d_str], [f"{h:02d}00" for h in range(12, 18)])

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
                    "precipitation_probability": int(pop_am) if is_am and pop_am is not None
                        else (int(pop_pm) if not is_am and pop_pm is not None else None),
                    "_day_index": i,
                })

            daily_forecast.append({
                "datetime": target_date.replace(
                    hour=12, minute=0, second=0, microsecond=0
                ).isoformat(),
                "native_temperature": t_max,
                "native_templow": t_min,
                "condition": self.kor_to_condition(wf_pm),
                "precipitation_probability": int(pop_pm) if pop_pm is not None else None,
                "_day_index": i,
            })

        _KST = ZoneInfo("Asia/Seoul")

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
                    "_raw_precip": raw_val,
                    "_no_precip": no_precip,
                })

        weather_data.update({"forecast_twice_daily": twice_daily, "forecast_daily": daily_forecast, "forecast_hourly": hourly_forecast})

        kor_now = self._get_sky_kor(weather_data.get("SKY"), weather_data.get("PTY"))

        # ── 현재 예상 강수량 ─────────────────────────────────────────────────
        # PTY=3(눈)이면 적설량(SNO), 그 외엔 강수량(PCP) 사용
        _pty_now = str(weather_data.get("PTY", "0"))
        _raw_now = weather_data.get("SNO") if _pty_now == "3" else weather_data.get("PCP")
        _no_precip_now = "적설없음" if _pty_now == "3" else "강수없음"
        weather_data["precip_amount"] = _parse_precip(_raw_now, _no_precip_now)
        # 기상청 원본 표기("30.0~50.0mm", "1mm 미만" 등)를 그대로 보여줄 수 있도록
        # 별도 속성으로 보존한다. 상태값은 통계/자동화 호환을 위해 숫자로 유지.
        weather_data["precip_amount_display"] = str(_raw_now) if _raw_now else _no_precip_now

        # ── 다음 24시간 시간대별 강수량 (현재 예상 강수량 센서의 속성용) ──────
        # wall-clock(now) 기준이 아니라, 상태값(precip_amount)이 실제로 참조한
        # _best_slot_dt 바로 다음 슬롯부터 이어지도록 계산한다.
        # (그렇지 않으면 발표 직후처럼 현재 시각 슬롯이 아직 없어 다음 슬롯을
        #  상태값으로 쓰는 상황에서, 속성이 현재 시각 기준으로 시작되어
        #  상태값과 속성의 시작 시점이 어긋나는 문제가 생긴다.)
        _anchor_dt = _best_slot_dt if _best_slot_dt is not None else now
        if _anchor_dt.tzinfo is None:
            _anchor_dt = _anchor_dt.replace(tzinfo=ZoneInfo("Asia/Seoul"))

        hourly_precip: dict[str, float | str] = {}

        # 현재 슬롯이 범위 값("30.0~50.0mm")이면, 상태값과 마찬가지로
        # 현재 시각부터 속성에 포함시킨다. 범위가 아니면 기존처럼 다음 시각부터.
        _anchor_is_range = bool(_raw_now) and "~" in str(_raw_now)
        if _anchor_is_range:
            hourly_precip[f"{_anchor_dt.hour:02d}시"] = _precip_display(_raw_now, _no_precip_now)

        for entry in hourly_forecast:
            entry_dt = datetime.fromisoformat(entry["datetime"])
            if entry_dt <= _anchor_dt:
                continue
            hourly_precip[f"{entry_dt.hour:02d}시"] = _precip_display(
                entry.get("_raw_precip"), entry.get("_no_precip")
            )
            if len(hourly_precip) >= 24:
                break
        weather_data["hourly_precipitation_mm"] = hourly_precip

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
            "pollen": pollen_data,
            "uv": uv_data,
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
