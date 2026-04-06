"""Button platform for KMA Weather."""
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN, CONF_PREFIX, CONF_LOCATION_ENTITY

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up KMA Weather button."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    location_entity = entry.data.get(CONF_LOCATION_ENTITY, "")
    
    if location_entity.startswith("device_tracker."):
        async_add_entities([KMAUpdateButton(coordinator, entry)])

class KMAUpdateButton(ButtonEntity):
    """Manual update button for mobile devices."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator, entry):
        """Initialize the button."""
        self.coordinator = coordinator
        prefix = entry.data.get(CONF_PREFIX, "kma").lower()
        
        self.entity_id = f"button.{prefix}_manual_update"
        self._attr_unique_id = f"{entry.entry_id}_manual_update"
        self._attr_name = "수동 업데이트"
        
        # [수정] 기기 정보에 제조사와 모델명 추가
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Murianwind",
            "model": "integration"
        }

    async def async_press(self) -> None:
        """버튼을 누를 때 즉시 데이터 업데이트 실행."""
        await self.coordinator.async_request_refresh()
