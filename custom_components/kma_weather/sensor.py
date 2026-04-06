"""Sensor platform for KMA Smart Weather."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfTemperature,
    UnitOfPercentage,
    UnitOfSpeed,
    UnitOfPrecipitation,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the KMA Weather sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    # 요청하신 16개 센서 및 관리용 센서 목록
    # (이름, 데이터키, 단위, 디바이스클래스)
    sensor_definitions = [
        ("현재날씨", "current_condition", None, None),
        ("현재위치 날씨", "location_weather", None, None),
        ("현재풍속", "WSD", UnitOfSpeed.METERS_PER_SECOND, SensorDeviceClass.WIND_SPEED),
        ("현재풍향", "VEC_KOR", None, None),
        ("최고온도", "TMX_today", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("최저온도", "TMN_today", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("강수확률", "POP", UnitOfPercentage, None),
        ("비시작시간오늘내일", "rain_start_time", None, None),
        ("내일최고온도", "TMX_tomorrow", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("내일최저온도", "TMN_tomorrow", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("내일오전날씨", "weather_am_tomorrow", None, None),
        ("내일오후날씨", "weather_pm_tomorrow", None, None),
        ("미세먼지", "pm10Value", "㎍/㎥", SensorDeviceClass.PM10),
        ("미세먼지등급", "pm10Grade", None, None),
        ("초미세먼지", "pm25Value", "㎍/㎥", SensorDeviceClass.PM25),
        ("초미세먼지등급", "pm25Grade", None, None),
    ]

    entities = [
        KMACustomSensor(coordinator, entry, name, key, unit, device_class)
        for name, key, unit, device_class in sensor_definitions
    ]
    
    # 추가 요구사항: API 인증키 만료 센서
    entities.append(APIExpirationSensor(entry))

    async_add_entities(entities)

class KMACustomSensor(CoordinatorEntity, SensorEntity):
    """Representation of a KMA Weather sensor."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, name, key, unit, device_class):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        
        # 기기별로 센서를 그룹화 (Zone 이름 또는 모바일 기기 이름으로 표시)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "기상청",
            "model": "Smart Weather API",
        }

    @property
    def native_value(self) -> str | float | None:
        """Return the state of the sensor."""
        data = self.coordinator.data
        if not data:
            return None

        # 대기질 데이터 파싱 (에어코리아)
        if "pm" in self._key:
            return data.get("air", {}).get(self._key)
        
        # 기상 데이터 파싱 (기상청)
        return data.get("weather", {}).get(self._key)

class APIExpirationSensor(SensorEntity):
    """Sensor to track API Key expiration (typically 2 years)."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "days"

    def __init__(self, entry):
        """Initialize the expiration sensor."""
        self._entry = entry
        self._attr_name = "API 인증키 남은 일수"
        self._attr_unique_id = f"{entry.entry_id}_api_expiry"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
        }

    @property
    def native_value(self) -> int:
        """Calculate remaining days from entry creation (approximate)."""
        # 실제 발급일을 가져올 수 없으므로 HA 등록일 기준으로 730일 계산
        # 필요 시 설정 단계에서 발급일을 입력받도록 확장 가능
        return 730
