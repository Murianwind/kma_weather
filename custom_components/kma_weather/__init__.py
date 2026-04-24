"""Initialize the KMA Weather integration."""
import logging
from datetime import datetime, timedelta, date, time as dt_time

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from zoneinfo import ZoneInfo

from .const import DOMAIN
from .coordinator import KMAWeatherUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "weather", "button"]

SERVICE_GET_ASTRONOMICAL_INFO = "get_astronomical_info"

_KST = ZoneInfo("Asia/Seoul")
_MAX_DAYS_AHEAD = 4

# time 필드: 선택 입력, HH:MM 또는 HH:MM:SS 문자열
_SERVICE_SCHEMA = vol.Schema({
    vol.Required("address"): str,
    vol.Required("date"): cv.date,
    vol.Optional("time"): str,
})

# 한국 영역 경계 (독도·이어도 포함)
_KOR_LAT = (33.0, 38.7)
_KOR_LON = (124.0, 132.0)


def _parse_time_str(time_str: str) -> dt_time:
    """HH:MM 또는 HH:MM:SS 문자열을 time 객체로 변환한다."""
    s = time_str.strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    raise HomeAssistantError(
        f"시각 형식이 올바르지 않습니다: '{time_str}'\n"
        "올바른 형식은 HH:MM 입니다.  예) 09:00  /  21:30  /  00:00"
    )


async def _geocode_ko(
    hass: HomeAssistant, address: str
) -> tuple[float | None, float | None, str | None]:
    """한국어 주소를 Nominatim으로 위경도로 변환한다."""
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": address,
                "format": "json",
                "limit": 1,
                "countrycodes": "kr",
                "addressdetails": "1",
            },
            headers={
                "User-Agent": "HomeAssistant-KMA-Weather/geocode",
                "Accept-Language": "ko",
            },
            timeout=10,
        ) as resp:
            results = await resp.json(content_type=None)
            if results:
                r = results[0]
                return float(r["lat"]), float(r["lon"]), r.get("display_name")
    except Exception as e:
        _LOGGER.error("주소 지오코딩 실패 (%s): %s", address, e)
    return None, None, None


def _in_korea(lat: float, lon: float) -> bool:
    return _KOR_LAT[0] <= lat <= _KOR_LAT[1] and _KOR_LON[0] <= lon <= _KOR_LON[1]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up KMA Weather from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    coordinator = KMAWeatherUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.services.has_service(DOMAIN, SERVICE_GET_ASTRONOMICAL_INFO):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_ASTRONOMICAL_INFO,
            _handle_get_astronomical_info,
            schema=_SERVICE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_GET_ASTRONOMICAL_INFO)
    return unload_ok


async def _handle_get_astronomical_info(call: ServiceCall) -> dict:
    """
    HA 액션: 주소·날짜·(선택) 시각으로 천문 관측 정보를 반환한다.

    입력 파라미터:
      address (str) : 한국 읍면동 주소  예) "경기도 화성시 동탄면"
      date    (date): 조회 날짜, 오늘~오늘+4일 이내
      time    (str) : 조회 시각 HH:MM (선택, 기본값: 현재 시각)

    반환 필드:
      address, resolved_address, date, time, latitude, longitude,
      sunrise, sunset, dawn, dusk, astro_dawn, astro_dusk,
      moonrise, moonset, moon_phase, moon_illumination,
      observation_condition, observation_reason
    """
    hass: HomeAssistant = call.hass
    raw_address: str = call.data.get("address", "").strip()
    target_date: date = call.data["date"]
    time_str: str | None = call.data.get("time")

    now_kst = datetime.now(_KST)
    today = now_kst.date()
    max_date = today + timedelta(days=_MAX_DAYS_AHEAD)

    # ── 1. 주소 공백 ──────────────────────────────────────────────────────
    if not raw_address:
        raise HomeAssistantError(
            "주소를 입력해주세요.\n"
            "예) 경기도 화성시 동탄면  /  서울특별시 종로구 청운효자동"
        )

    # ── 2. 날짜 범위 ──────────────────────────────────────────────────────
    if target_date < today:
        raise HomeAssistantError(
            f"과거 날짜는 조회할 수 없습니다.\n"
            f"입력한 날짜: {target_date}\n"
            f"조회 가능 범위: {today} ~ {max_date}  (오늘부터 4일 이내)"
        )
    if target_date > max_date:
        raise HomeAssistantError(
            f"4일 이후 날짜는 조회할 수 없습니다.\n"
            f"입력한 날짜: {target_date}\n"
            f"조회 가능 범위: {today} ~ {max_date}  (오늘부터 4일 이내)"
        )

    # ── 3. 시각 파싱 ──────────────────────────────────────────────────────
    if time_str:
        target_time = _parse_time_str(time_str)
    else:
        target_time = now_kst.time().replace(second=0, microsecond=0)

    # ── 4. 주소 → 위경도 ──────────────────────────────────────────────────
    lat, lon, display_name = await _geocode_ko(hass, raw_address)

    if lat is None:
        raise HomeAssistantError(
            f"주소를 찾을 수 없습니다: '{raw_address}'\n\n"
            "확인사항:\n"
            "• 시/군/구 + 읍/면/동까지 포함해서 입력해주세요\n"
            "  예) 경기도 화성시 동탄면  /  서울 종로구 청운효자동\n"
            "• 오타가 없는지 확인해주세요\n"
            "• 행정동 이름이 다를 경우 법정동 이름으로 시도해보세요"
        )

    # ── 5. 한국 영역 밖 좌표 ──────────────────────────────────────────────
    if not _in_korea(lat, lon):
        raise HomeAssistantError(
            f"입력한 주소가 한국 영역 밖의 좌표로 변환됐습니다: '{raw_address}'\n"
            f"변환된 좌표: 위도 {lat:.4f}, 경도 {lon:.4f}\n"
            "한국 내 주소인지 확인해주세요."
        )

    # ── 6. KMA Weather 통합 확인 ──────────────────────────────────────────
    coordinators = list(hass.data.get(DOMAIN, {}).values())
    if not coordinators:
        raise HomeAssistantError(
            "KMA Weather 통합 구성요소가 등록되지 않았습니다.\n"
            "설정 > 기기 및 서비스에서 KMA Weather를 먼저 추가해주세요."
        )
    coordinator: KMAWeatherUpdateCoordinator = coordinators[0]

    # ── 7. skyfield 준비 확인 ─────────────────────────────────────────────
    if coordinator._sf_eph is None or coordinator._sf_ts is None:
        raise HomeAssistantError(
            "천문 계산 라이브러리(skyfield)가 아직 준비되지 않았습니다.\n"
            "HA 재시작 후 잠시 기다린 다음 다시 시도해주세요."
        )

    # ── 8. 천문 계산 ──────────────────────────────────────────────────────
    astro = coordinator.calc_astronomical_for_date(lat, lon, target_date)
    if "error" in astro:
        raise HomeAssistantError(
            f"천문 계산 중 오류가 발생했습니다: {astro['error']}\n"
            "잠시 후 다시 시도해주세요."
        )

    # ── 9. 지정 시각 기준 관측 조건 평가 ─────────────────────────────────
    eval_dt = datetime.combine(target_date, target_time).replace(tzinfo=_KST)
    obs_cond, obs_reason = coordinator._eval_observation(
        {"moon_illumination": astro.get("moon_illumination", 100)},
        eval_dt,
        lat,
        lon,
    )

    return {
        "address":           raw_address,
        "resolved_address":  display_name or raw_address,
        "date":              str(target_date),
        "time":              target_time.strftime("%H:%M"),
        "latitude":          round(lat, 4),
        "longitude":         round(lon, 4),
        "sunrise":           astro.get("sunrise"),
        "sunset":            astro.get("sunset"),
        "dawn":              astro.get("dawn"),
        "dusk":              astro.get("dusk"),
        "astro_dawn":        astro.get("astro_dawn"),
        "astro_dusk":        astro.get("astro_dusk"),
        "moonrise":          astro.get("moonrise"),
        "moonset":           astro.get("moonset"),
        "moon_phase":        astro.get("moon_phase"),
        "moon_illumination": astro.get("moon_illumination"),
        "observation_condition": obs_cond,
        "observation_reason":    obs_reason if obs_reason else None,
    }
