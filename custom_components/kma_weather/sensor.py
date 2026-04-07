from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfTemperature, PERCENTAGE, UnitOfSpeed, EntityCategory
from homeassistant.helpers.entity import DeviceInfo
from datetime import date
import logging
from .const import DOMAIN, CONF_PREFIX, CONF_EXPIRE_DATE

_LOGGER = logging.getLogger(__name__)

# ★ 원본 SENSOR_TYPES 구조 100% 유지
SENSOR_TYPES = {
    "TMP": ["기온", UnitOfTemperature.CELSIUS, "mdi:thermometer", None, "temperature", None],
    "REH": ["습도", PERCENTAGE, "mdi:water-percent", None, "humidity", None],
    "WSD": ["풍속", UnitOfSpeed.METERS_PER_SECOND, "mdi:weather-windy", None, "wind_speed", None],
    "VEC_KOR": ["풍향", None, "mdi:compass-outline", None, "wind_direction", None],
    "POP": ["강수확률", PERCENTAGE, "mdi:umbrella-outline", None, "precipitation_prob", None],
    "rain_start_time": ["강수 시작 시각", None, "mdi:clock-outline", None, "rain_start", None],
    "current_condition_kor": ["현재 날씨", None, "mdi:weather-cloudy", None, "condition", None],
    "pm10Value": ["미세먼지 농도", "㎍/㎥", "mdi:blur", None, "pm10", None],
    "pm10Grade": ["미세먼지 등급", None, "mdi:check-circle-outline", None, "pm10_grade", None],
    "pm25Value": ["초미세먼지 농도", "㎍/㎥", "mdi:blur-linear", None, "pm25", None],
    "pm25Grade": ["초미세먼지 등급", None, "mdi:check-circle-outline", None, "pm25_grade", None],
    "last_updated": ["업데이트 시간", None, "mdi:update", SensorDeviceClass.TIMESTAMP, "last_updated", EntityCategory.DIAGNOSTIC],
    "api_expire": ["API 잔여일수", "일", "mdi:key-alert", None, "api_expire", EntityCategory.DIAGNOSTIC],
    "apparent_temp": ["체감온도", UnitOfTemperature.CELSIUS, "mdi:thermometer-lines", None, "apparent_temperature", None],
    "TMX_today": ["오늘 최고기온", UnitOfTemperature.CELSIUS, "mdi:thermometer-chevron-up", None, "today_temp_max", None],
    "TMN_today": ["오늘 최저기온", UnitOfTemperature.CELSIUS, "mdi:thermometer-chevron-down", None, "today_temp_min", None],
    "TMX_tomorrow": ["내일 최고기온", UnitOfTemperature.CELSIUS, "mdi:thermometer-chevron-up", None, "tomorrow_temp_max", None],
    "TMN_tomorrow": ["내일 최저기온", UnitOfTemperature.CELSIUS, "mdi:thermometer-chevron-down", None, "tomorrow_temp_min", None],
    "weather_am_tomorrow": ["내일 오전 날씨", None, "mdi:weather-partly-cloudy", None, "tomorrow_weather_am", None],
    "weather_pm_tomorrow": ["내일 오후 날씨", None, "mdi:weather-partly-cloudy", None, "tomorrow_weather_pm", None],
}

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    # 원본과 동일하게 SENSOR_TYPES를 순회하며 생성
    entities = [KMACustomSensor(coordinator, s_type, entry) for s_type in SENSOR_TYPES]
    entities.append(KMALocationDebugSensor(coordinator, entry))
    async_add_entities(entities)

class KMACustomSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, sensor_type, entry):
        super().__init__(coordinator)
        self._type = sensor_type
        self._entry = entry
        prefix = entry.data.get(CONF_PREFIX, "kma").lower()
        details = SENSOR_TYPES[sensor_type]

        # ★ 원본 명명 규칙/ID 규칙 100% 유지
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
        # 1. API 만료일 계산 (원본 로직 유지)
        if self._type == "api_expire":
            expire_str = self._entry.options.get(CONF_EXPIRE_DATE) or self._entry.data.get(CONF_EXPIRE_DATE)
            if not expire_str:
                return None
            try:
                expire = date.fromisoformat(str(expire_str).strip())
                return (expire - date.today()).days
            except Exception:
                return None

        # ★ 안정성 보강: 데이터 부재 시 Safe Return
        if not self.coordinator.data:
            return None

        weather = self.coordinator.data.get("weather", {})
        air = self.coordinator.data.get("air", {})

        # 2. weather 딕셔너리에서 조회 (Safe Access 보강)
        if self._type in weather:
            val = weather.get(self._type)
            if self._type in ["TMP", "REH", "WSD", "POP"] and val is not None:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return val
            return val

        # 3. 에어코리아 데이터 (Safe Access 보강)
        if self._type in air:
            return air.get(self._type)

        return None

    @property
    def suggested_display_precision(self):
        """기온, 습도, 강수확률 등의 센서 화면 출력 시 소수점을 제거(0)하고 정수로 표시합니다."""
        int_sensors = [
            "TMP", "REH", "POP", "apparent_temp", 
            "TMX_today", "TMN_today", "TMX_tomorrow", "TMN_tomorrow"
        ]
        if self._type in int_sensors:
            return 0
        return None

class KMALocationDebugSensor(CoordinatorEntity, SensorEntity):
    _attr_icon = "mdi:map-marker"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        prefix = entry.data.get(CONF_PREFIX, "kma").lower()
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
        if not self.coordinator.data:
            return None
        w = self.coordinator.data.get("weather", {})
        # 원본 로직 유지: 주소 우선, 없으면 좌표
        return w.get("address") or f"{w.get('debug_lat')}, {w.get('debug_lon')}"

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data:
            return {}
        w = self.coordinator.data.get("weather", {})
        air = self.coordinator.data.get("air", {})
        # 원본의 모든 디버그 속성 유지
        return {
            "nx": w.get("debug_nx"),
            "ny": w.get("debug_ny"),
            "reg_id_temp": w.get("debug_reg_id_temp"),
            "reg_id_land": w.get("debug_reg_id_land"),
            "air_station": air.get("station"),
            "lat": w.get("debug_lat"),
            "lon": w.get("debug_lon"),
        }
