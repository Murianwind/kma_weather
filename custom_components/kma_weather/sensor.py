from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfTemperature, PERCENTAGE, UnitOfSpeed
from .const import DOMAIN, CONF_PREFIX

# [이름, 단위, 아이콘, DeviceClass, ID명]
SENSOR_TYPES = {
    "TMP": ["기온", UnitOfTemperature.CELSIUS, "mdi:thermometer", None, "temperature"],
    "REH": ["습도", PERCENTAGE, "mdi:water-percent", None, "humidity"],
    "WSD": ["풍속", UnitOfSpeed.METERS_PER_SECOND, "mdi:weather-windy", None, "wind_speed"],
    "VEC_KOR": ["풍향", None, "mdi:compass-outline", None, "wind_direction"],
    "POP": ["강수확률", PERCENTAGE, "mdi:umbrella-outline", None, "precipitation_prob"],
    "rain_start_time": ["강수 시작 시각", None, "mdi:clock-outline", None, "rain_start"],
    "current_condition_kor": ["현재 날씨", None, "mdi:weather-cloudy", None, "condition"],
    "pm10Value": ["미세먼지 농도", "㎍/㎥", "mdi:blur", None, "pm10"],
    "pm10Grade": ["미세먼지 등급", None, "mdi:check-circle-outline", None, "pm10_grade"],
    "pm25Value": ["초미세먼지 농도", "㎍/㎥", "mdi:blur-linear", None, "pm25"],
    "pm25Grade": ["초미세먼지 등급", None, "mdi:check-circle-outline", None, "pm25_grade"],
    "last_updated": ["업데이트 시간", None, "mdi:update", SensorDeviceClass.TIMESTAMP, "last_updated"],
}

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([KMACustomSensor(coordinator, s_type, entry) for s_type in SENSOR_TYPES])

class KMACustomSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, sensor_type, entry):
        super().__init__(coordinator)
        self._type = sensor_type
        prefix = entry.data.get(CONF_PREFIX, "kma")
        
        # 피드백 반영: 매핑된 ID명을 사용하여 소문자 기반 직관적 ID 생성
        id_name = SENSOR_TYPES[sensor_type][4]
        self.entity_id = f"sensor.{prefix}_{id_name}"
        
        details = SENSOR_TYPES[sensor_type]
        self._attr_name = f"{entry.title} {details[0]}"
        self._attr_native_unit_of_measurement = details[1]
        self._attr_icon = details[2]
        self._attr_device_class = details[3]
        self._attr_unique_id = f"{entry.entry_id}_{sensor_type}"

    @property
    def native_value(self):
        data = self.coordinator.data
        if not data: return None
        weather = data.get("weather", {})
        if self._type in weather:
            val = weather[self._type]
            if self._type in ["TMP", "REH", "WSD", "POP"] and val is not None:
                return float(val)
            return val
        return data.get("air", {}).get(self._type)
