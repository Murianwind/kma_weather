import logging
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfTemperature, PERCENTAGE, UnitOfSpeed, EntityCategory
from homeassistant.helpers.entity import DeviceInfo
from datetime import date
from .const import DOMAIN, CONF_PREFIX, CONF_EXPIRE_DATE

_LOGGER = logging.getLogger(__name__)

# weather_summary 포함 (테스트 호환성 유지)
SENSOR_TYPES = {
    "TMP":                ["현재온도",       UnitOfTemperature.CELSIUS,          "mdi:thermometer",             SensorDeviceClass.TEMPERATURE, "temperature",         None],
    "REH":                ["현재습도",       PERCENTAGE,                          "mdi:water-percent",           SensorDeviceClass.HUMIDITY,    "humidity",            None],
    "WSD":                ["현재풍속",       UnitOfSpeed.METERS_PER_SECOND,       "mdi:weather-windy",           SensorDeviceClass.WIND_SPEED,  "wind_speed",          None],
    "VEC_KOR":            ["현재풍향",       None,                                "mdi:compass",                 None,                          "wind_direction",      None],
    "POP":                ["강수확률",       PERCENTAGE,                          "mdi:umbrella-outline",        None,                          "precipitation_prob",  None],
    "apparent_temp":      ["체감온도",       UnitOfTemperature.CELSIUS,          "mdi:thermometer-lines",       SensorDeviceClass.TEMPERATURE, "apparent_temperature",None],
    "rain_start_time":    ["비시작시간",     None,                                "mdi:clock-outline",           None,                          "rain_start",          None],
    "current_condition_kor": ["현재날씨",   None,                                "mdi:weather-cloudy",          None,                          "condition",           None],
    "pm10Value":          ["미세먼지 농도",  "µg/m³",                             "mdi:blur",                    SensorDeviceClass.PM10,        "pm10",                None],
    "pm10Grade":          ["미세먼지 등급",  None,                                "mdi:check-circle-outline",    None,                          "pm10_grade",          None],
    "pm25Value":          ["초미세먼지 농도","µg/m³",                             "mdi:blur-linear",             SensorDeviceClass.PM25,        "pm25",                None],
    "pm25Grade":          ["초미세먼지 등급",None,                                "mdi:check-circle-outline",    None,                          "pm25_grade",          None],
    "address":            ["현재 위치",      None,                                "mdi:map-marker",              None,                          "location",            EntityCategory.DIAGNOSTIC],
    "last_updated":       ["업데이트 시간",  None,                                "mdi:update",                  SensorDeviceClass.TIMESTAMP,   "last_updated",        EntityCategory.DIAGNOSTIC],
    "api_expire":         ["API 잔여일수",   "일",                                "mdi:key-alert",               None,                          "api_expire",          EntityCategory.DIAGNOSTIC],
    "TMX_today":          ["오늘최고온도",   UnitOfTemperature.CELSIUS,          "mdi:thermometer-chevron-up",  SensorDeviceClass.TEMPERATURE, "today_temp_max",      None],
    "TMN_today":          ["오늘최저온도",   UnitOfTemperature.CELSIUS,          "mdi:thermometer-chevron-down",SensorDeviceClass.TEMPERATURE, "today_temp_min",      None],
    "wf_am_today":        ["오늘오전날씨",   None,                                "mdi:weather-partly-cloudy",   None,                          "today_condition_am",  None],
    "wf_pm_today":        ["오늘오후날씨",   None,                                "mdi:weather-cloudy",          None,                          "today_condition_pm",  None],
    "TMX_tomorrow":       ["내일최고온도",   UnitOfTemperature.CELSIUS,          "mdi:thermometer-chevron-up",  SensorDeviceClass.TEMPERATURE, "tomorrow_temp_max",   None],
    "TMN_tomorrow":       ["내일최저온도",   UnitOfTemperature.CELSIUS,          "mdi:thermometer-chevron-down",SensorDeviceClass.TEMPERATURE, "tomorrow_temp_min",   None],
    "wf_am_tomorrow":     ["내일오전날씨",   None,                                "mdi:weather-partly-cloudy",   None,                          "tomorrow_condition_am",None],
    "wf_pm_tomorrow":     ["내일오후날씨",   None,                                "mdi:weather-cloudy",          None,                          "tomorrow_condition_pm",None],
    "weather_summary":    ["날씨 요약",      None,                                "mdi:text-box-outline",        None,                          "summary",             None],
}

async def async_setup_entry(hass, entry, async_add_entities):
    """센서 엔티티 등록"""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    prefix = entry.options.get(CONF_PREFIX, entry.data.get(CONF_PREFIX, "kma"))

    entities = [
        KMACustomSensor(coordinator, sensor_type, prefix, entry)
        for sensor_type in SENSOR_TYPES
    ]
    async_add_entities(entities)

class KMACustomSensor(CoordinatorEntity, SensorEntity):
    """기상청 커스텀 센서 클래스"""
    _attr_has_entity_name = True

    def __init__(self, coordinator, sensor_type, prefix, entry):
        super().__init__(coordinator)
        self._type = sensor_type
        self._entry = entry

        details = SENSOR_TYPES[sensor_type]
        self.entity_id = f"sensor.{prefix}_{details[4]}"
        self._attr_name = details[0]
        self._attr_native_unit_of_measurement = details[1]
        self._attr_icon = details[2]
        self._attr_device_class = details[3]
        self._attr_unique_id = f"{entry.entry_id}_{sensor_type}"
        self._attr_entity_category = details[5]

        # ── 풍속 센서: HA의 단위 자동변환(m/s → km/h 등)을 차단 ──────────
        # suggested_unit_of_measurement를 명시하면 HA가 해당 단위로 고정 표시한다.
        # unit_of_measurement는 native_unit과 동일하게 m/s로 유지.
        if sensor_type == "WSD":
            self._attr_suggested_unit_of_measurement = UnitOfSpeed.METERS_PER_SECOND

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Murianwind",
            model="KMA Weather Service",
        )

    @property
    def native_value(self):
        """센서의 현재 상태 값 반환"""
        if self._type == "api_expire":
            exp = self._entry.options.get(CONF_EXPIRE_DATE) or self._entry.data.get(CONF_EXPIRE_DATE)
            try:
                return (date.fromisoformat(exp) - date.today()).days
            except (ValueError, TypeError):
                return None

        if not self.coordinator.data:
            return None

        w = self.coordinator.data.get("weather", {})
        a = self.coordinator.data.get("air", {})

        # 오늘 최고/최저 기온은 코디네이터 내부 누적값 우선
        if self._type == "TMN_today":
            val = self.coordinator._daily_min_temp
        elif self._type == "TMX_today":
            val = self.coordinator._daily_max_temp
        elif self._type == "weather_summary":
            val = w.get("current_condition_kor")
        else:
            val = w.get(self._type) if self._type in w else a.get(self._type)

        if val in [None, "-", ""]:
            return None

        unit = self._attr_native_unit_of_measurement

        if unit is not None:
            try:
                f_val = float(val)

                # ── 온도·습도·강수확률: 정수(int) 반환 ─────────────────
                if unit in (UnitOfTemperature.CELSIUS, PERCENTAGE):
                    return int(round(f_val))

                # ── 풍속: 소수점 첫째자리 float 반환 (m/s 원본값) ───────
                # HA가 unit_system에 따라 km/h로 변환하려 해도
                # suggested_unit_of_measurement=m/s를 명시했으므로 m/s로 고정 표시됨
                if self._attr_device_class == SensorDeviceClass.WIND_SPEED:
                    return round(f_val, 1)

                # ── 미세먼지 농도: 소수점 첫째자리 float 반환 ────────────
                if self._attr_device_class in (SensorDeviceClass.PM10, SensorDeviceClass.PM25):
                    return round(f_val, 1)

                # ── 그 외 수치형: 정수 반환 ──────────────────────────────
                return int(f_val)

            except (ValueError, TypeError):
                return None

        # 문자열 상태값 (날씨, 비시작시간 등)
        return val

    @property
    def extra_state_attributes(self):
        """추가 속성 반환"""
        if not self.coordinator.data:
            return None

        w = self.coordinator.data.get("weather", {})
        a = self.coordinator.data.get("air", {})

        if self._type == "weather_summary":
            return {
                "forecast_daily": w.get("forecast_daily", []),
                "forecast_twice_daily": w.get("forecast_twice_daily", []),
                "today_max": self.coordinator._daily_max_temp,
                "today_min": self.coordinator._daily_min_temp,
            }

        if self._type == "address":
            return {
                "short_term_nx": w.get("debug_nx"),
                "short_term_ny": w.get("debug_ny"),
                "air_korea_station": a.get("station"),
                "latitude": w.get("debug_lat"),
                "longitude": w.get("debug_lon"),
            }
        return None
