import logging
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, CONF_KMA_API_KEY, CONF_AIR_API_KEY
from .api_kma import KMAApiClient
from .coordinator import KMADataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["weather", "sensor"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """설정 항목으로부터 컴포넌트 셋업."""
    
    kma_key = entry.data[CONF_KMA_API_KEY]
    air_key = entry.data[CONF_AIR_API_KEY]
    
    session = async_get_clientsession(hass)
    api = KMAApiClient(kma_key, air_key, session)
    
    coordinator = KMADataUpdateCoordinator(hass, api, entry)
    
    # 첫 데이터 로드
    await coordinator.async_config_entry_first_refresh()
    
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # weather 및 sensor 플랫폼 로드
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """컴포넌트 제거 시 호출."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
