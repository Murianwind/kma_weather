from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfTemperature, PERCENTAGE, UnitOfSpeed, EntityCategory
from homeassistant.helpers.entity import DeviceInfo
from datetime import date
from .const import DOMAIN, CONF_PREFIX, CONF_EXPIRE_DATE, CONF_APPLY_DATE

SENSOR_TYPES = {
    "TMP": ["현재온도", UnitOfTemperature.CELSIUS, "mdi:thermometer", None, "temperature", None],
    "REH": ["현재습도", PERCENTAGE, "mdi:water-percent", None, "humidity", None],
    "WSD": ["현재풍속", UnitOfSpeed.METERS_PER_SECOND, "mdi:weather-windy", None, "wind_speed", None],
    "POP": ["강수확률", PERCENTAGE, "mdi:umbrella-outline", None, "precipitation_prob", None],
    "rain_start_time": ["비시작시간오늘내", None, "mdi:clock-outline", None, "rain_start", None],
    "current_condition_kor": ["현재날씨", None, "mdi:weather-cloudy", None, "condition", None],
    "pm10Value": ["미세먼지 농도", "㎍/㎥", "mdi:blur", None, "pm10", None],
    "pm10Grade": ["미세먼지 등급", None, "mdi:check-circle-outline", None, "pm10_grade", None],
    "pm25Value": ["초미세먼지 농도", "㎍/㎥", "mdi:blur-linear", None, "pm25", None],
    "pm25Grade": ["초미세먼지 등급", None, "mdi:check-circle-outline", None, "pm25_grade", None],
    "address": ["기상청 날씨", None, "mdi:map-marker", None, "location", EntityCategory.DIAGNOSTIC],
    "last_updated": ["업데이트 시간", None, "mdi:update", SensorDeviceClass.TIMESTAMP, "last_updated", EntityCategory.DIAGNOSTIC],
    "api_expire": ["API 잔여일수", "일", "mdi:key-alert", None, "api_expire", EntityCategory.DIAGNOSTIC],
    "TMX_today": ["오늘최고온도", UnitOfTemperature.CELSIUS, "mdi:thermometer-chevron-up", None, "today_temp_max", None],
    "TMN_today": ["오늘최저온도", UnitOfTemperature.CELSIUS, "mdi:thermometer-chevron-down", None, "today_temp_min", None],
    "TMX_tomorrow": ["내일최고온도", UnitOfTemperature.CELSIUS, "mdi:thermometer-chevron-up", None, "tomorrow_temp_max", None],
    "TMN_tomorrow": ["내일최저온도", UnitOfTemperature.CELSIUS, "mdi:thermometer-chevron-down", None, "tomorrow_temp_min", None],
    # [신규 추가] 내일 오전/오후 날씨 상태 센서
    "wf_am_tomorrow": ["내일오전날씨", None, "mdi:weather-partly-cloudy", None, "tomorrow_condition_am", None],
    "wf_pm_tomorrow": ["내일오후날씨", None, "mdi:weather-cloudy", None, "tomorrow_condition_pm", None],
}

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [KMACustomSensor(coordinator, s_type, entry) for s_type in SENSOR_TYPES]
    async_add_entities(entities)

class KMACustomSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, sensor_type, entry):
        super().__init__(coordinator)
        self._type = sensor_type
        self._entry = entry
        prefix = entry.data.get(CONF_PREFIX, "kma")
        details = SENSOR_TYPES[sensor_type]
        
        self.entity_id = f"sensor.{prefix}_{details[4]}"
        self._attr_name = details[0]
        self._attr_native_unit_of_measurement = details[1]
        self._attr_icon = details[2]
        self._attr_device_class = details[3]
        self._attr_unique_id = f"{entry.entry_id}_{sensor_type}"
        self._attr_entity_category = details[5]
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=entry.title)

    @property
    def native_value(self):
        # 1. 기존 API 만료일 로직 보존
        if self._type == "api_expire":
            exp = self._entry.options.get(CONF_EXPIRE_DATE) or self._entry.data.get(CONF_EXPIRE_DATE)
            try: return (date.fromisoformat(exp) - date.today()).days
            except: return None
        
        # 2. 데이터 추출 로직
        data = self.coordinator.data or {}
        w, a = data.get("weather", {}), data.get("air", {})
        val = w.get(self._type) if self._type in w else a.get(self._type)

        # 3. [수정 사항] 온도 및 습도 단위 센서 정수 처리
        if val is not None and self._attr_native_unit_of_measurement in [UnitOfTemperature.CELSIUS, PERCENTAGE]:
            try:
                return int(float(val))
            except (ValueError, TypeError):
                return val
                
        return val

    @property
    def extra_state_attributes(self):
        if self._type == "address":
            w = self.coordinator.data.get("weather", {})
            return {
                "short_term_nx_ny": f"{w.get('debug_nx')}, {w.get('debug_ny')}",
                "mid_term_temp_id": w.get("debug_reg_id_temp"),
                "mid_term_land_id": w.get("debug_reg_id_land"),
                "air_korea_station": w.get("station"),
                "latitude": w.get("debug_lat"),
                "longitude": w.get("debug_lon")
            }
        return None
