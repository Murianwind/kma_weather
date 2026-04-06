"""Sensor platform for KMA Weather."""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import UnitOfTemperature
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    sensor_map = [
        ("현재온도", "TMP", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("현재습도", "REH", "%", SensorDeviceClass.HUMIDITY),
        ("강수확률", "POP", "%", None),
        ("내일오전날씨", "weather_am_tomorrow", None, None),
        ("내일오후날씨", "weather_pm_tomorrow", None, None),
        ("내일최고온도", "TMX_tomorrow", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("내일최저온도", "TMN_tomorrow", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("최고온도", "TMX_today", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("최저온도", "TMN_today", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("미세먼지", "pm10Value", "㎍/㎥", SensorDeviceClass.PM10),
        ("미세먼지등급", "pm10Grade", None, None),
        ("비시작시간오늘내일", "rain_start_time", None, None),
        ("현재위치", "location_weather", None, None),
        ("초미세먼지", "pm25Value", "㎍/㎥", SensorDeviceClass.PM25),
        ("초미세먼지등급", "pm25Grade", None, None),
        ("현재날씨", "current_condition_kor", None, None),
        ("현재풍속", "WSD", "m/s", None), 
        ("현재풍향", "VEC_KOR", None, None),
    ]
    
    entities = [KMACustomSensor(coordinator, entry, *s) for s in sensor_map]
    entities.append(APIExpirationSensor(entry))
    async_add_entities(entities)

class KMACustomSensor(SensorEntity):
    _attr_has_entity_name = True
    def __init__(self, coordinator, entry, name, key, unit, dev_class):
        self.coordinator = coordinator
        self._key = key
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = dev_class
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title}

    @property
    def native_value(self):
        d = self.coordinator.data
        if not d: return None
        val = d.get("air", {}).get(self._key) if "pm" in self._key else d.get("weather", {}).get(self._key)
        
        if self._attr_device_class == SensorDeviceClass.TEMPERATURE and val is not None:
            try: return int(float(val))
            except: pass

        return val if val is not None else "데이터 대기중"

    @property
    def extra_state_attributes(self):
        """[수정] 현재위치 센서의 경우 Latitude와 Longitude를 속성으로 추가"""
        attrs = {}
        if self._key == "location_weather":
            data = self.coordinator.data
            if data and "weather" in data:
                attrs["Latitude"] = data["weather"].get("latitude")
                attrs["Longitude"] = data["weather"].get("longitude")
        return attrs

class APIExpirationSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "일"
    def __init__(self, entry):
        self._attr_name = "API 인증키 남은 일수"
        self._attr_unique_id = f"{entry.entry_id}_api_expiry"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title}
    @property
    def native_value(self): return 730
