import logging
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfTemperature, PERCENTAGE, UnitOfSpeed, EntityCategory
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.core import callback
from datetime import date
from .const import DOMAIN, CONF_PREFIX, CONF_EXPIRE_DATE

_LOGGER = logging.getLogger(__name__)

# [이름, 단위, 아이콘, device_class, entity_id접미, entity_category]
SENSOR_TYPES = {
    "TMP":                ["현재온도",        UnitOfTemperature.CELSIUS,          "mdi:thermometer",             SensorDeviceClass.TEMPERATURE, "temperature",          None],
    "REH":                ["현재습도",        PERCENTAGE,                          "mdi:water-percent",           SensorDeviceClass.HUMIDITY,    "humidity",             None],
    "WSD":                ["현재풍속",        UnitOfSpeed.METERS_PER_SECOND,       "mdi:weather-windy",           SensorDeviceClass.WIND_SPEED,  "wind_speed",           None],
    "VEC_KOR":            ["현재풍향",        None,                                "mdi:compass",                 None,                          "wind_direction",       None],
    "POP":                ["강수확률",        PERCENTAGE,                          "mdi:umbrella-outline",        None,                          "precipitation_prob",   None],
    "apparent_temp":      ["체감온도",        UnitOfTemperature.CELSIUS,          "mdi:thermometer-lines",       SensorDeviceClass.TEMPERATURE, "apparent_temperature", None],
    "rain_start_time":    ["비시작시간",      None,                                "mdi:clock-outline",           None,                          "rain_start",           None],
    "current_condition_kor": ["현재날씨",    None,                                "mdi:weather-cloudy",          None,                          "condition",            None],
    "pm10Value":          ["미세먼지 농도",   "µg/m³",                             "mdi:blur",                    SensorDeviceClass.PM10,        "pm10",                 None],
    "pm10Grade":          ["미세먼지 등급",   None,                                "mdi:check-circle-outline",    None,                          "pm10_grade",           None],
    "pm25Value":          ["초미세먼지 농도", "µg/m³",                             "mdi:blur-linear",             SensorDeviceClass.PM25,        "pm25",                 None],
    "pm25Grade":          ["초미세먼지 등급", None,                                "mdi:check-circle-outline",    None,                          "pm25_grade",           None],
    "address":            ["현재 위치",       None,                                "mdi:map-marker",              None,                          "location",             EntityCategory.DIAGNOSTIC],
    "last_updated":       ["업데이트 시간",   None,                                "mdi:update",                  SensorDeviceClass.TIMESTAMP,   "last_updated",         EntityCategory.DIAGNOSTIC],
    "api_expire":         ["API 잔여일수",    "일",                                "mdi:key-alert",               None,                          "api_expire",           EntityCategory.DIAGNOSTIC],
    "TMX_today":          ["오늘최고온도",    UnitOfTemperature.CELSIUS,          "mdi:thermometer-chevron-up",  SensorDeviceClass.TEMPERATURE, "today_temp_max",       None],
    "TMN_today":          ["오늘최저온도",    UnitOfTemperature.CELSIUS,          "mdi:thermometer-chevron-down",SensorDeviceClass.TEMPERATURE, "today_temp_min",       None],
    "wf_am_today":        ["오늘오전날씨",    None,                                "mdi:weather-partly-cloudy",   None,                          "today_condition_am",   None],
    "wf_pm_today":        ["오늘오후날씨",    None,                                "mdi:weather-cloudy",          None,                          "today_condition_pm",   None],
    "TMX_tomorrow":       ["내일최고온도",    UnitOfTemperature.CELSIUS,          "mdi:thermometer-chevron-up",  SensorDeviceClass.TEMPERATURE, "tomorrow_temp_max",    None],
    "TMN_tomorrow":       ["내일최저온도",    UnitOfTemperature.CELSIUS,          "mdi:thermometer-chevron-down",SensorDeviceClass.TEMPERATURE, "tomorrow_temp_min",    None],
    "wf_am_tomorrow":     ["내일오전날씨",    None,                                "mdi:weather-partly-cloudy",   None,                          "tomorrow_condition_am",None],
    "wf_pm_tomorrow":     ["내일오후날씨",    None,                                "mdi:weather-cloudy",          None,                          "tomorrow_condition_pm",None],
    "warning":            ["기상특보",        None,                                "mdi:alert-outline",           None,                          "warning",              None],
    "dawn":               ["다음 새벽",         None,   "mdi:weather-night",               None,  "dawn",               None],
    "sunrise":            ["다음 일출",          None,   "mdi:weather-sunset-up",           None,  "sunrise",            None],
    "sunset":             ["다음 일몰",          None,   "mdi:weather-sunset-down",         None,  "sunset",             None],
    "dusk":               ["다음 황혼",          None,   "mdi:weather-night-partly-cloudy", None,  "dusk",               None],
    "astro_dawn":         ["다음 천문관측 종료",  None,   "mdi:telescope",                   None,  "astro_dawn",         None],
    "astro_dusk":         ["다음 천문관측 시작",  None,   "mdi:telescope",                   None,  "astro_dusk",         None],
    "moon_phase":         ["달 위상",            None,   "mdi:moon-waning-gibbous",         None,  "moon_phase",         None],
    "moon_illumination":  ["달 조명율",          PERCENTAGE, "mdi:brightness-percent",      None,  "moon_illumination",  None],
    "moonrise":           ["다음 월출",          None,   "mdi:moon-full",                   None,  "moonrise",           None],
    "moonset":            ["다음 월몰",          None,   "mdi:moon-waning-crescent",        None,  "moonset",            None],
    "observation_condition": ["천문 관측 조건",  None,   "mdi:telescope",                   None,  "observation_condition", None],
    "pollen":             ["꽃가루 농도",      None,                                "mdi:flower-pollen",           None,                          "pollen",               None],
}

# ── API별 센서 그룹 ───────────────────────────────────────────────────────────
# None: API 신청 여부와 무관하게 항상 등록
# 키 문자열: 해당 API가 승인됐을 때만 등록
SENSOR_API_GROUPS: dict[str | None, list[str]] = {
    None: [
        "api_expire", "last_updated", "address",
        "dawn", "sunrise", "sunset", "dusk",
        "astro_dawn", "astro_dusk",
        "moon_phase", "moon_illumination", "moonrise", "moonset",
        "observation_condition",
        "pollen",   # 비시즌 = 좋음으로 폴백, API 불필요
    ],
    "short": [
        "TMP", "REH", "WSD", "VEC_KOR", "POP", "apparent_temp",
        "rain_start_time", "current_condition_kor",
        "TMX_today", "TMN_today", "wf_am_today", "wf_pm_today",
        "TMX_tomorrow", "TMN_tomorrow", "wf_am_tomorrow", "wf_pm_tomorrow",
    ],
    "air": ["pm10Value", "pm10Grade", "pm25Value", "pm25Grade"],
    "warning": ["warning"],
}


def _eligible_sensor_types(coordinator) -> list[str]:
    """현재 승인된 API 기준으로 등록해야 할 센서 타입 목록을 반환한다."""
    approved = coordinator.api._approved_apis
    types: list[str] = list(SENSOR_API_GROUPS.get(None, []))
    for api_key, sensor_types in SENSOR_API_GROUPS.items():
        if api_key is not None and api_key in approved:
            types.extend(sensor_types)
    return types


async def async_setup_entry(hass, entry, async_add_entities):
    """센서 엔티티 등록 (승인된 API 기준 동적 등록)"""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    prefix = entry.options.get(CONF_PREFIX, entry.data.get(CONF_PREFIX, "kma"))

    # 등록된 센서 타입 추적 (중복 방지)
    if not hasattr(coordinator, "_registered_sensor_types"):
        coordinator._registered_sensor_types = set()

    def _make_sensors(types: list[str]) -> list:
        return [
            KMACustomSensor(coordinator, st, prefix, entry)
            for st in types
            if st not in coordinator._registered_sensor_types and st in SENSOR_TYPES
        ]

    # 초기 등록
    initial_types = _eligible_sensor_types(coordinator)
    initial_entities = _make_sensors(initial_types)
    for e in initial_entities:
        coordinator._registered_sensor_types.add(e._type)
    async_add_entities(initial_entities)

    # 매 업데이트마다 새로 승인된 API 확인 → 센서 추가
    @callback
    def _check_new_sensors():
        eligible = _eligible_sensor_types(coordinator)
        new_types = [t for t in eligible if t not in coordinator._registered_sensor_types]
        if not new_types:
            return
        new_entities = _make_sensors(new_types)
        for e in new_entities:
            coordinator._registered_sensor_types.add(e._type)
        if new_entities:
            _LOGGER.info("새 API 승인 감지: 센서 %d개 추가 (%s)", len(new_entities),
                         ", ".join(e._type for e in new_entities))
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_check_new_sensors))


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

        # 풍속 센서: HA 단위 자동변환 차단
        if sensor_type == "WSD":
            self._attr_suggested_unit_of_measurement = UnitOfSpeed.METERS_PER_SECOND

        # 수치형 단위 센서에 STATE_CLASS 설정
        if details[1] is not None and sensor_type not in ("api_expire",):
            self._attr_state_class = SensorStateClass.MEASUREMENT

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Murianwind",
            model="KMA Weather Service",
        )

    _MOON_PHASE_ICONS = {
        "삭":       "mdi:moon-new",
        "초승달":   "mdi:moon-waxing-crescent",
        "상현달":   "mdi:moon-first-quarter",
        "준상현달": "mdi:moon-waxing-gibbous",
        "보름달":   "mdi:moon-full",
        "준하현달": "mdi:moon-waning-gibbous",
        "하현달":   "mdi:moon-last-quarter",
        "그믐달":   "mdi:moon-waning-crescent",
    }

    _OBSERVATION_ICONS = {
        "최우수":          "mdi:star-shooting",
        "우수":            "mdi:star",
        "보통":            "mdi:star-half-full",
        "불량 (달빛)":     "mdi:moon-waning-gibbous",
        "관측불가 (강수)": "mdi:weather-rainy",
        "관측불가 (흐림)": "mdi:weather-cloudy",
        "관측불가 (낮/박명)": "mdi:weather-sunny",
    }

    _POLLEN_ICONS = {
        "좋음":   "mdi:flower-pollen-outline",
        "보통":   "mdi:flower-pollen",
        "나쁨":   "mdi:flower-pollen",
        "매우나쁨": "mdi:flower-pollen",
    }

    @property
    def icon(self) -> str:
        if self.coordinator.data:
            w = self.coordinator.data.get("weather", {})
            if self._type == "moon_phase":
                phase = w.get("moon_phase")
                if phase and phase in self._MOON_PHASE_ICONS:
                    return self._MOON_PHASE_ICONS[phase]
            elif self._type == "observation_condition":
                cond = w.get("observation_condition")
                if cond and cond in self._OBSERVATION_ICONS:
                    return self._OBSERVATION_ICONS[cond]
            elif self._type == "pollen":
                pollen = self.coordinator.data.get("pollen", {})
                worst = pollen.get("worst") or "좋음"
                return self._POLLEN_ICONS.get(worst, self._attr_icon)
        return self._attr_icon

    @property
    def native_value(self):
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

        # 꽃가루 센서: worst 등급 반환 (None이면 좋음)
        if self._type == "pollen":
            pollen = self.coordinator.data.get("pollen", {})
            worst = pollen.get("worst")
            return worst if worst is not None else "좋음"

        # 오늘 최고/최저 기온은 코디네이터 누적값 우선
        if self._type == "TMN_today":
            val = self.coordinator._daily_min_temp
        elif self._type == "TMX_today":
            val = self.coordinator._daily_max_temp
        else:
            val = w.get(self._type) if self._type in w else a.get(self._type)

        if val in [None, "-", ""]:
            return None

        unit = self._attr_native_unit_of_measurement
        if unit is not None:
            try:
                f_val = float(val)
                return int(f_val) if f_val == int(f_val) else f_val
            except (ValueError, TypeError):
                return None

        return val

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data:
            return None

        w = self.coordinator.data.get("weather", {})
        a = self.coordinator.data.get("air", {})

        if self._type == "address":
            return {
                "short_term_nx": w.get("debug_nx"),
                "short_term_ny": w.get("debug_ny"),
                "air_korea_station": a.get("station"),
                "latitude": w.get("debug_lat"),
                "longitude": w.get("debug_lon"),
            }

        if self._type == "pollen":
            pollen = self.coordinator.data.get("pollen", {})
            return {
                "참나무": pollen.get("oak") if pollen.get("oak") is not None else "좋음",
                "소나무": pollen.get("pine") if pollen.get("pine") is not None else "좋음",
                "풀":     pollen.get("grass") if pollen.get("grass") is not None else "좋음",
            }

        return None
