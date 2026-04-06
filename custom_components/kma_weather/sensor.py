from homeassistant.components.sensor import SensorEntity, SensorDeviceClass # 피드백 2번 반영
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfTemperature, PERCENTAGE, UnitOfSpeed
from .const import DOMAIN

SENSOR_TYPES = {
    "TMP": ["기온", UnitOfTemperature.CELSIUS, "mdi:thermometer", None],
    "REH": ["습도", PERCENTAGE, "mdi:water-percent", None],
    "WSD": ["풍속", UnitOfSpeed.METERS_PER_SECOND, "mdi:weather-windy", None],
    "VEC_KOR": ["풍향", None, "mdi:compass-outline", None],
    "POP": ["강수확률", PERCENTAGE, "mdi:umbrella-outline", None],
    "rain_start_time": ["강수 시작 시각", None, "mdi:clock-outline", None],
    "current_condition_kor": ["현재 날씨", None, "mdi:weather-cloudy", None],
    "pm10Value": ["미세먼지 농도", "㎍/㎥", "mdi:blur", None],
    "pm10Grade": ["미세먼지 등급", None, "mdi:check-circle-outline", None],
    "pm25Value": ["초미세먼지 농도", "㎍/㎥", "mdi:blur-linear", None],
    "pm25Grade": ["초미세먼지 등급", None, "mdi:check-circle-outline", None],
    "last_updated": ["업데이트 시간", None, "mdi:update", SensorDeviceClass.TIMESTAMP], # 피드백 2번 반영
}

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([KMACustomSensor(coordinator, s_type, entry) for s_type in SENSOR_TYPES])

class KMACustomSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, sensor_type, entry):
        super().__init__(coordinator)
        self._type = sensor_type
        self._attr_name = f"{entry.title} {SENSOR_TYPES[sensor_type][0]}"
        self._attr_native_unit_of_measurement = SENSOR_TYPES[sensor_type][1]
        self._attr_icon = SENSOR_TYPES[sensor_type][2]
        self._attr_device_class = SENSOR_TYPES[sensor_type][3] # DeviceClass 할당
        self._attr_unique_id = f"{entry.entry_id}_{sensor_type}"

    @property
    def native_value(self):
        data = self.coordinator.data
        if not data: return None
        weather = data.get("weather", {})
        if self._type in weather:
            val = weather[self._type]
            # 수치형 데이터 형변환
            if self._type in ["TMP", "REH", "WSD", "POP"] and val is not None:
                return float(val)
            return val
        return data.get("air", {}).get(self._type)
