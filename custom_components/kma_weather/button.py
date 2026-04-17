from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from datetime import datetime, timedelta
import logging
# CONF_LOCATION_ENTITY 추가 임포트
from .const import DOMAIN, CONF_PREFIX, CONF_LOCATION_ENTITY

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the KMA weather button."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    # 설정된 위치 엔티티가 device_tracker(모바일 기기 등)인 경우에만 버튼 생성
    location_entity = entry.data.get(CONF_LOCATION_ENTITY)
    if location_entity and location_entity.startswith("device_tracker."):
        async_add_entities([KMAUpdateButton(coordinator, entry)])

class KMAUpdateButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name, _attr_icon = True, "mdi:refresh"
    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        prefix = entry.data.get(CONF_PREFIX, "kma").lower()
        # 이름 변경: "수동 업데이트" -> "업데이트"
        self.entity_id, self._attr_unique_id, self._attr_name = f"button.{prefix}_manual_update", f"{entry.entry_id}_manual_update", "업데이트"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title, "manufacturer": "Murianwind", "model": "integration"}
        self._last_press = None

    async def async_press(self) -> None:
        now = datetime.now()
        # 로그 메시지 이름 변경 반영
        if self._last_press and (now - self._last_press) < timedelta(seconds=5):
            _LOGGER.info("업데이트가 너무 자주 요청되었습니다. (5초 제한)")
            return
        self._last_press = now
        await self.coordinator.async_request_refresh()
