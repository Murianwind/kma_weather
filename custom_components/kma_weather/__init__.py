"""Initialize the KMA Weather integration."""
import logging
from datetime import datetime, timedelta, date

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

_SERVICE_SCHEMA = vol.Schema({
    vol.Required("address"): str,
    vol.Required("date"): cv.date,
})

_KST = ZoneInfo("Asia/Seoul")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up KMA Weather from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    coordinator = KMAWeatherUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 서비스 등록 (동일 도메인 내 중복 등록 방지)
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
    # 마지막 항목 제거 시 서비스도 등록 해제
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_GET_ASTRONOMICAL_INFO)
    return unload_ok


async def _geocode_address_ko(hass: HomeAssistant, address: str) -> tuple[float | None, float | None]:
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
                return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as e:
        _LOGGER.error("주소 지오코딩 실패 (%s): %s", address, e)
    return None, None


async def _handle_get_astronomical_info(call: ServiceCall) -> dict:
    """
    HA 액션: 읍면동 주소와 날짜를 받아 천문 관측 정보를 반환한다.
    날짜는 오늘~오늘+4일 이내여야 한다.
    """
    hass: HomeAssistant = call.hass
    address: str = call.data["address"]
    target_date: date = call.data["date"]

    # 날짜 범위 검증
    today = datetime.now(_KST).date()
    max_date = today + timedelta(days=4)
    if not (today <= target_date <= max_date):
        raise HomeAssistantError(
            f"날짜는 오늘({today})부터 4일 이내({max_date})여야 합니다. 입력값: {target_date}"
        )

    # 주소 → 위경도 변환
    lat, lon = await _geocode_address_ko(hass, address)
    if lat is None:
        raise HomeAssistantError(
            f"주소 '{address}'를 찾을 수 없습니다. 더 자세한 주소(시/군/구 + 읍/면/동)를 입력해주세요."
        )

    # 등록된 coordinator에서 skyfield 사용
    coordinators = list(hass.data.get(DOMAIN, {}).values())
    if not coordinators:
        raise HomeAssistantError("KMA Weather 통합이 등록되지 않았습니다.")
    coordinator: KMAWeatherUpdateCoordinator = coordinators[0]

    astro = coordinator.calc_astronomical_for_date(lat, lon, target_date)
    if "error" in astro:
        raise HomeAssistantError(f"천문 계산 실패: {astro['error']}")

    return {
        "address": address,
        "date": str(target_date),
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        **astro,
    }
