from datetime import datetime, timedelta
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    entities = [
        KMAMisemeonjiSensor(coordinator, entry, "pm10"),
        KMAMisemeonjiSensor(coordinator, entry, "pm25"),
        APIExpirationSensor(entry) # 인증키 만료 센서
    ]
    async_add_entities(entities)

class APIExpirationSensor(SensorEntity):
    """API 인증키 만료일 센서 (2년 기준)."""
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.DURATION

    def __init__(self, entry):
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_api_expiry"
        self._attr_name = "API 인증키 남은 일수"
        self._attr_native_unit_of_measurement = "days"

    @property
    def native_value(self):
        """발급일(설정일 기준)로부터 2년(730일) 남은 날짜 계산."""
        # 실제 발급일을 알 수 없으므로, HA에 등록된 날짜를 기준으로 계산합니다.
        # 고도화 시 발급일을 직접 입력받게 수정 가능합니다.
        created_at = datetime.fromtimestamp(self._entry.entry_id.split('_')[0]) if '_' in self._entry.entry_id else datetime.now()
        expiry_date = created_at + timedelta(days=730)
        remaining = (expiry_date - datetime.now()).days
        return max(0, remaining)

class KMAMisemeonjiSensor(SensorEntity):
    """에어코리아 미세먼지 센서."""
    def __init__(self, coordinator, entry, sensor_type):
        self.coordinator = coordinator
        self.sensor_type = sensor_type
        self._attr_unique_id = f"{entry.entry_id}_{sensor_type}"
        self._attr_name = "미세먼지" if sensor_type == "pm10" else "초미세먼지"

    @property
    def native_value(self):
        return self.coordinator.data.get("air", {}).get(self.sensor_type)
