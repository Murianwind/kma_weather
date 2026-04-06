from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfTemperature, PERCENTAGE, UnitOfSpeed, EntityCategory
from homeassistant.helpers.entity import DeviceInfo
from datetime import date
import logging
from .const import DOMAIN, CONF_PREFIX, CONF_EXPIRE_DATE

_LOGGER = logging.getLogger(__name__)

# [이름, 단위, 아이콘, DeviceClass, ID명, EntityCategory]
SENSOR_TYPES = {
    "TMP":                  ["기온",         UnitOfTemperature.CELSIUS,          "mdi:thermometer",        None,                          "temperature",      None],
    "REH":                  ["습도",         PERCENTAGE,                          "mdi:water-percent",      None,                          "humidity",         None],
    "WSD":                  ["풍속",         UnitOfSpeed.METERS_PER_SECOND,       "mdi:weather-windy",      None,                          "wind_speed",       None],
    "VEC_KOR":              ["풍향",         None,                                "mdi:compass-outline",    None,                          "wind_direction",   None],
    "POP":                  ["강수확률",     PERCENTAGE,                          "mdi:umbrella-outline",   None,                          "precipitation_prob", None],
    "rain_start_time":      ["강수 시작 시각", None,                              "mdi:clock-outline",      None,                          "rain_start",       None],
    "current_condition_kor":["현재 날씨",    None,                                "mdi:weather-cloudy",     None,                          "condition",        None],
    "pm10Value":            ["미세먼지 농도", "㎍/㎥",                            "mdi:blur",               None,                          "pm10",             None],
    "pm10Grade":            ["미세먼지 등급", None,                               "mdi:check-circle-outline", None,                        "pm10_grade",       None],
    "pm25Value":            ["초미세먼지 농도", "㎍/㎥",                          "mdi:blur-linear",        None,                          "pm25",             None],
    "pm25Grade":            ["초미세먼지 등급", None,                             "mdi:check-circle-outline", None,                        "pm25_grade",       None],
    "last_updated":         ["업데이트 시간", None,                               "mdi:update",             SensorDeviceClass.TIMESTAMP,   "last_updated",     EntityCategory.DIAGNOSTIC],
    "api_expire":           ["API 잔여일수",  "일",                               "mdi:key-alert",          None,                          "api_expire",       EntityCategory.DIAGNOSTIC],
}

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [KMACustomSensor(coordinator, s_type, entry) for s_type in SENSOR_TYPES]
    entities.append(KMALocationDebugSensor(coordinator, entry))
    async_add_entities(entities)

class KMACustomSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, sensor_type, entry):
        super().__init__(coordinator)
        self._type = sensor_type
        self._entry = entry
        prefix = entry.data.get(CONF_PREFIX, "kma")
        details = SENSOR_TYPES[sensor_type]

        self.entity_id = f"sensor.{prefix}_{details[4]}"
        self._attr_name = f"{entry.title} {details[0]}"
        self._attr_native_unit_of_measurement = details[1]
        self._attr_icon = details[2]
        self._attr_device_class = details[3]
        self._attr_unique_id = f"{entry.entry_id}_{sensor_type}"
        self._attr_entity_category = details[5]

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Murianwind",
            model="integration"
        )

    @property
    def native_value(self):
        # API 만료일 실시간 계산
        if self._type == "api_expire":
            expire_str = self._entry.options.get(CONF_EXPIRE_DATE) or self._entry.data.get(CONF_EXPIRE_DATE)
            if not expire_str: return None
            try:
                expire = date.fromisoformat(str(expire_str).strip())
                return (expire - date.today()).days
            except Exception: return None

        if not self.coordinator.data: return None
        
        # 날씨 데이터 확인
        weather = self.coordinator.data.get("weather", {})
        if self._type in weather:
            val = weather[self._type]
            if self._type in ["TMP", "REH", "WSD", "POP"] and val is not None:
                try: return float(val)
                except ValueError: return val
            return val
            
        # 대기질(에어코리아) 데이터 확인
        air = self.coordinator.data.get("air", {})
        if self._type in air:
            return air.get(self._type)
            
        # 기타 데이터 (업데이트 시간 등)
        return self.coordinator.data.get(self._type)

class KMALocationDebugSensor(CoordinatorEntity, SensorEntity):
    _attr_icon = "mdi:map-marker"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        prefix = entry.data.get(CONF_PREFIX, "kma")
        self.entity_id = f"sensor.{prefix}_location"
        self._attr_name = f"{entry.title} 현재위치"
        self._attr_unique_id = f"{entry.entry_id}_location"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Murianwind",
            model="integration"
        )

    @property
    def native_value(self):
        """좌표 대신 주소 정보를 우선 출력하도록 수정"""
        if not self.coordinator.data: return None
        w = self.coordinator.data.get("weather", {})
        # 주소 정보(동네 이름)가 있다면 이를 반환
        address = w.get("address")
        if address:
            return address
        # 주소가 없는 경우 기존처럼 좌표 출력
        lat = w.get("debug_lat")
        lon = w.get("debug_lon")
        return f"{lat}, {lon}" if lat and lon else None

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data: return {}
        w = self.coordinator.data.get("weather", {})
        air = self.coordinator.data.get("air", {})
        return {
            "nx": w.get("debug_nx"),
            "ny": w.get("debug_ny"),
            "reg_id_temp": w.get("debug_reg_id_temp"),
            "reg_id_land": w.get("debug_reg_id_land"),
            "air_station": air.get("station"), # 에어 스테이션 정보 보강
        }
