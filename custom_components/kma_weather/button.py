import logging
from datetime import datetime, timedelta
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN, CONF_PREFIX

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([KMARefreshButton(coordinator, entry)])

class KMARefreshButton(CoordinatorEntity, ButtonEntity):
    _attr_icon = "mdi:refresh"
    _attr_device_class = None

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        prefix = entry.data.get(CONF_PREFIX, "kma").lower()
        self.entity_id = f"button.{prefix}_refresh"
        self._attr_name = f"{entry.title} 새로고침"
        self._attr_unique_id = f"{entry.entry_id}_refresh"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=entry.title, manufacturer="Murianwind", model="integration")
        self._last_press = None

    async def async_press(self) -> None:
        """Handle the button press with 5-second debounce."""
        now = datetime.now()
        if self._last_press and (now - self._last_press) < timedelta(seconds=5):
            _LOGGER.info("새로고침 버튼이 너무 짧은 시간에 여러 번 눌렸습니다. (무시됨)")
            return
        
        self._last_press = now
        _LOGGER.info("사용자 요청으로 날씨 데이터를 수동 갱신합니다.")
        await self.coordinator.async_request_refresh()
