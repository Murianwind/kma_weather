from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfPercentage, UnitOfSpeed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]

    # 요구하신 16개 센서 리스트 (이름, 데이터키, 단위, 클래스)
    sensors = [
        ("강수확률", "POP", UnitOfPercentage, None),
        ("내일오전날씨", "weather_am_tomorrow", None, None),
        ("내일오후날씨", "weather_pm_tomorrow", None, None),
        ("내일최고온도", "TMX_tomorrow", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("내일최저온도", "TMN_tomorrow", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("미세먼지", "pm10Value", "㎍/㎥", SensorDeviceClass.PM10),
        ("미세먼지등급", "pm10Grade", None, None),
        ("비시작시간오늘내일", "rain_start_time", None, None),
        ("현재위치 날씨", "location_weather", None, None),
        ("초미세먼지", "pm25Value", "㎍/㎥", SensorDeviceClass.PM25),
        ("초미세먼지등급", "pm25Grade", None, None),
        ("최고온도", "TMX_today", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("최저온도", "TMN_today", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("현재날씨", "current_condition", None, None),
        ("현재풍속", "WSD", UnitOfSpeed.METERS_PER_SECOND, SensorDeviceClass.WIND_SPEED),
        ("현재풍향", "VEC_KOR", None, None),
    ]

    entities = [KMACustomSensor(coordinator, entry, *s) for s in sensors]
    entities.append(APIExpirationSensor(entry))
    async_add_entities(entities)

class KMACustomSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, name, key, unit, dev_class):
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = dev_class
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title}

    @property
    def native_value(self):
        data = self.coordinator.data
        if not data: return None
        return data["air"].get(self._key) if "pm" in self._key else data["weather"].get(self._key)

class APIExpirationSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "days"
    def __init__(self, entry):
        self._attr_name = "API 인증키 남은 일수"
        self._attr_unique_id = f"{entry.entry_id}_api_expiry"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title}
    @property
    def native_value(self): return 730
