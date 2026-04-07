import logging
from datetime import datetime
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfTemperature, UnitOfSpeed, PERCENTAGE, UnitOfPressure, UnitOfLength
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, CONF_PREFIX, CONF_EXPIRE_DATE

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    prefix = entry.data.get(CONF_PREFIX, "kma")
    
    sensors = [
        KMASensor(coordinator, entry, prefix, "temp", "현재 기온", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        KMASensor(coordinator, entry, prefix, "humidity", "현재 습도", PERCENTAGE, SensorDeviceClass.HUMIDITY),
        KMASensor(coordinator, entry, prefix, "wind_speed", "풍속", UnitOfSpeed.METERS_PER_SECOND, SensorDeviceClass.WIND_SPEED),
        KMASensor(coordinator, entry, prefix, "wind_dir", "풍향", None, None),
        KMASensor(coordinator, entry, prefix, "pressure", "현지기압", UnitOfPressure.HPA, SensorDeviceClass.ATMOSPHERIC_PRESSURE),
        KMASensor(coordinator, entry, prefix, "rain_1h", "시간당 강수량", UnitOfLength.MILLIMETERS, SensorDeviceClass.PRECIPITATION),
        KMASensor(coordinator, entry, prefix, "pm10", "미세먼지(PM10)", "㎍/㎥", None),
        KMASensor(coordinator, entry, prefix, "pm25", "초미세먼지(PM25)", "㎍/㎥", None),
        KMASensor(coordinator, entry, prefix, "station", "측정소", None, None),
        KMASensor(coordinator, entry, prefix, "address", "현재 주소", None, None),
        KMASensor(coordinator, entry, prefix, "api_expire", "API 만료 잔여일", "일", None),
    ]
    async_add_entities(sensors)

class KMASensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry, prefix, sensor_type, name, unit, device_class):
        super().__init__(coordinator)
        self.entry = entry
        self._sensor_type = sensor_type
        self._attr_name = f"{prefix}_{sensor_type}"
        self._attr_unique_id = f"{entry.entry_id}_{sensor_type}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = SensorStateClass.MEASUREMENT if unit else None

    @property
    def native_value(self):
        """Return the state of the sensor with None-safe access."""
        if not self.coordinator.data:
            return None
            
        weather = self.coordinator.data.get("weather", {})
        air = self.coordinator.data.get("air", {})

        # ★ 핵심 보완: 모든 접근에 .get() 사용 및 타입 체크
        if self._sensor_type == "temp": return weather.get("TMP")
        if self._sensor_type == "humidity": return weather.get("REH")
        if self._sensor_type == "wind_speed": return weather.get("WSD")
        if self._sensor_type == "wind_dir": return weather.get("VEC_KOR")
        if self._sensor_type == "pressure": return weather.get("PCP") # 기압 데이터 필드 확인 필요
        if self._sensor_type == "rain_1h": return weather.get("PCP")
        if self._sensor_type == "pm10": return air.get("pm10Value")
        if self._sensor_type == "pm25": return air.get("pm25Value")
        if self._sensor_type == "station": return air.get("station")
        if self._sensor_type == "address": return weather.get("address")
        
        if self._sensor_type == "api_expire":
            expire_str = self.entry.options.get(CONF_EXPIRE_DATE) or self.entry.data.get(CONF_EXPIRE_DATE)
            if expire_str:
                try:
                    expire_date = datetime.strptime(expire_str, "%Y-%m-%d").date()
                    days_left = (expire_date - datetime.now().date()).days
                    return max(0, days_left)
                except Exception: return None
        return None

    @property
    def extra_state_attributes(self):
        """Return entity specific state attributes with None-safe access."""
        if not self.coordinator.data:
            return {}
            
        weather = self.coordinator.data.get("weather", {})
        air = self.coordinator.data.get("air", {})

        attrs = {}
        if self._sensor_type == "temp":
            attrs.update({
                "체감온도": weather.get("apparent_temp"),
                "오늘_최고": weather.get("TMX_today"),
                "오늘_최저": weather.get("TMN_today"),
                "내일_최고": weather.get("TMX_tomorrow"),
                "내일_최저": weather.get("TMN_tomorrow"),
            })
        elif self._sensor_type in ["pm10", "pm25"]:
            attrs.update({
                "미세먼지_등급": air.get("pm10Grade"),
                "초미세먼지_등급": air.get("pm25Grade"),
                "측정소": air.get("station"),
            })
        
        # 공통 속성
        attrs["강수_시작_예상"] = weather.get("rain_start_time")
        attrs["마지막_업데이트"] = weather.get("last_updated")
        
        return attrs
