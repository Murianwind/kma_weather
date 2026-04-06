"""The KMA Weather integration."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_kma import KMAApiClient
from .const import DOMAIN, CONF_API_KEY
from .coordinator import KMADataUpdateCoordinator

PLATFORMS = ["sensor", "weather"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up KMA Weather from a config entry."""
    api_key = entry.data[CONF_API_KEY]
    session = async_get_clientsession(hass)
    
    api = KMAApiClient(api_key, session)
    coordinator = KMADataUpdateCoordinator(hass, api, entry)
    
    await coordinator.async_config_entry_first_refresh()
    
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
