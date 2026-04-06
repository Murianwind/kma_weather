from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    UnitOfTemperature, UnitOfPercentage, UnitOfSpeed, UnitOfPrecipitation
)
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    # 이미지 목록 기반 센서 정의 (이름, API키, 단위, 디바이스클래스)
    sensor_map = [
        ("기온", "TMP", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("습도", "REH", UnitOfPercentage, SensorDeviceClass.HUMIDITY),
        ("풍속", "WSD", UnitOfSpeed.METERS_PER_SECOND, SensorDeviceClass.WIND_SPEED),
        ("강수량", "PCP", UnitOfPrecipitation.MILLIMETERS, SensorDeviceClass.PRECIPITATION),
        ("강수확률", "POP", UnitOfPercentage, None),
        ("풍향", "VEC", "deg", None),
        ("미세먼지", "pm10Value", "㎍/㎥", SensorDeviceClass.PM10),
        ("초미세먼지", "pm25Value", "㎍/㎥", SensorDeviceClass.PM25),
    ]
    
    entities = [KMASensor(coordinator, entry, *s) for s in sensor_map]
    entities.append(APIExpirationSensor(entry))
    
    async_add_entities(entities)

class KMASensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, name, key, unit, dev_class):
        self.coordinator = coordinator
        self._key = key
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = dev_class
        # unique_id에 entry_id를 포함하여 여러 기기 등록 시 중복 방지
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        # 같은 기기끼리 묶이도록 설정
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "기상청",
        }

    @property
    def native_value(self):
        data = self.coordinator.data
        if "pm" in self._key:
            return data["air"].get(self._key)
        return data["weather"].get(self._key)
