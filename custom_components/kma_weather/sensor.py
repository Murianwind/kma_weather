"""Sensor platform for KMA Weather."""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import UnitOfTemperature
from datetime import datetime
from .const import DOMAIN, CONF_PREFIX

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    sensor_map = [
        ("현재온도", "TMP", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, "current_temperature"),
        ("현재습도", "REH", "%", SensorDeviceClass.HUMIDITY, "current_humidity"),
        ("강수확률", "POP", "%", None, "precipitation_probability"),
        ("내일오전날씨", "weather_am_tomorrow", None, None, "tomorrow_am_weather"),
        ("내일오후날씨", "weather_pm_tomorrow", None, None, "tomorrow_pm_weather"),
        ("내일최고온도", "TMX_tomorrow", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, "tomorrow_high_temperature"),
        ("내일최저온도", "TMN_tomorrow", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, "tomorrow_low_temperature"),
        ("최고온도", "TMX_today", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, "today_high_temperature"),
        ("최저온도", "TMN_today", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, "today_low_temperature"),
        ("미세먼지", "pm10Value", "µg/m³", SensorDeviceClass.PM10, "pm10"),
        ("미세먼지등급", "pm10Grade", None, None, "pm10_grade"),
        ("비시작시간오늘내일", "rain_start_time", None, None, "rain_start_time"),
        ("현재위치", "location_weather", None, None, "current_location"),
        ("초미세먼지", "pm25Value", "µg/m³", SensorDeviceClass.PM25, "pm25"),
        ("초미세먼지등급", "pm25Grade", None, None, "pm25_grade"),
        ("현재날씨", "current_condition_kor", None, None, "current_weather"),
        # [수정] 풍속 자동 변환을 막기 위해 DeviceClass를 제거하고 순수 m/s 문자열 사용
        ("현재풍속", "WSD", "m/s", None, "current_wind_speed"), 
        ("현재풍향", "VEC_KOR", None, None, "current_wind_direction"),
        ("업데이트 시간", "updated_at", None, SensorDeviceClass.TIMESTAMP, "last_updated"),
    ]
    
    entities = [KMACustomSensor(coordinator, entry, *s) for s in sensor_map]
    entities.append(APIExpirationSensor(entry))
    async_add_entities(entities)

class KMACustomSensor(SensorEntity):
    _attr_has_entity_name = True
    def __init__(self, coordinator, entry, name, key, unit, dev_class, intuitive_id):
        self.coordinator = coordinator
        self._key = key
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = dev_class
        
        prefix = entry.data.get(CONF_PREFIX, "kma").lower()
        self.entity_id = f"sensor.{prefix}_{intuitive_id}"
        self._attr_unique_id = f"{entry.entry_id}_{intuitive_id}"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title}
        
        if dev_class in [SensorDeviceClass.TEMPERATURE, SensorDeviceClass.HUMIDITY]:
            self._attr_suggested_display_precision = 0

    @property
    def native_value(self):
        d = self.coordinator.data
        if not d: return None
        
        if self._key in ["pm10Value", "pm10Grade", "pm25Value", "pm25Grade"]:
            val = d.get("air", {}).get(self._key)
        else:
            val = d.get("weather", {}).get(self._key)
            
        if val is None or str(val).strip() == "" or str(val).strip() == "-":
            return None
            
        if self._attr_device_class == SensorDeviceClass.TIMESTAMP:
            try: return datetime.fromisoformat(str(val))
            except Exception: return val
            
        if self._attr_device_class in [SensorDeviceClass.TEMPERATURE, SensorDeviceClass.HUMIDITY]:
            try: return int(float(val))
            except Exception: return None
            
        if self._attr_device_class in [SensorDeviceClass.PM10, SensorDeviceClass.PM25]:
            try: return float(val)
            except Exception: return None

        return val

    @property
    def extra_state_attributes(self):
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
        prefix = entry.data.get(CONF_PREFIX, "kma").lower()
        self.entity_id = f"sensor.{prefix}_api_expiration_days"
        self._attr_name = "API 인증키 남은 일수"
        self._attr_unique_id = f"{entry.entry_id}_api_expiry"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title}
    @property
    def native_value(self): return 730
