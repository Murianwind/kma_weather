"""Button platform for KMA Weather."""
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN, CONF_PREFIX, CONF_LOCATION_ENTITY

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up KMA Weather button."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    location_entity = entry.data.get(CONF_LOCATION_ENTITY, "")
    
    # 기존 로직 유지: 모바일 기기(device_tracker)인 경우에만 수동 업데이트 버튼 생성
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
        
        # 기존 entity_id 및 이름 유지
        self.entity_id = f"button.{prefix}_manual_update"
        self._attr_unique_id = f"{entry.entry_id}_manual_update"
        self._attr_name = "수동 업데이트"
        
        # 기기 정보 통합 (기존 제조사명 Murianwind 유지)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Murianwind",
            model="integration"
        )

    async def async_press(self) -> None:
        """버튼을 누를 때 즉시 데이터 업데이트 실행."""
        await self.coordinator.async_request_refresh()
