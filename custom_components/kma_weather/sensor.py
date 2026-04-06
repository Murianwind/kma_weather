from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfTemperature, UnitOfPercentage, UnitOfSpeed, UnitOfPrecipitation
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    # 이미지와 동일한 목록 구성
    sensors = [
        ("기온", "TMP", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("습도", "REH", UnitOfPercentage, SensorDeviceClass.HUMIDITY),
        ("풍속", "WSD", UnitOfSpeed.METERS_PER_SECOND, SensorDeviceClass.WIND_SPEED),
        ("강수량", "PCP", UnitOfPrecipitation.MILLIMETERS, SensorDeviceClass.PRECIPITATION),
        ("강수확률", "POP", UnitOfPercentage, None),
        ("미세먼지", "pm10", "㎍/㎥", SensorDeviceClass.PM10),
        ("초미세먼지", "pm25", "㎍/㎥", SensorDeviceClass.PM25),
    ]
    
    entities = [KMASensor(coordinator, entry, *s) for s in sensors]
    entities.append(APIExpirationSensor(entry))
    async_add_entities(entities)

class KMASensor(SensorEntity):
    def __init__(self, coordinator, entry, name, key, unit, dev_class):
        self.coordinator = coordinator
        self._key = key
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = dev_class
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        data = self.coordinator.data
        if self._key in ["pm10", "pm25"]:
            return data["air"].get(self._key)
        return data["weather"].get(self._key)

class APIExpirationSensor(SensorEntity):
    def __init__(self, entry):
        self._attr_name = "API 인증키 남은 일수"
        self._attr_native_unit_of_measurement = "days"
        self._attr_unique_id = f"{entry.entry_id}_api_expiry"

    @property
    def native_value(self):
        # 2년 만료 로직 (고정값 예시)
        return 730
