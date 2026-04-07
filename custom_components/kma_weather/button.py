from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo
from datetime import datetime, timedelta
import logging
from .const import DOMAIN, CONF_PREFIX, CONF_LOCATION_ENTITY

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([KMAUpdateButton(coordinator, entry)])

class KMAUpdateButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name, _attr_icon = True, "mdi:refresh"
    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        prefix = entry.data.get(CONF_PREFIX, "kma").lower()
        self.entity_id, self._attr_unique_id, self._attr_name = f"button.{prefix}_manual_update", f"{entry.entry_id}_manual_update", "수동 업데이트"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title, "manufacturer": "Murianwind", "model": "integration"}
        self._last_press = None

    async def async_press(self) -> None:
        now = datetime.now()
        if self._last_press and (now - self._last_press) < timedelta(seconds=5):
            _LOGGER.info("수동 업데이트 버튼이 너무 자주 눌렸습니다.")
            return
        self._last_press = now
        await self.coordinator.async_request_refresh()
