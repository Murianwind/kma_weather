"""Sensor platform for KMA Weather."""
from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfPercentage, UnitOfSpeed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up KMA Weather sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    # 이미지와 100% 일치하는 16개 센서 목록
    sensors = [
        ("강수확률", "POP", UnitOfPercentage, None),
        ("내일오전날씨", "weather_am_tomorrow", None, None),
        ("내일오후날씨", "weather_pm_tomorrow", None, None),
        ("내일최고온도", "TMX_tomorrow", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("내일최저온도", "TMN_tomorrow", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("미세먼지", "pm10Value", "㎍/㎥", SensorDeviceClass.PM10),
        ("미세먼지등급", "pm10Grade", None, None),
        ("비시작시간오늘내일", "rain_start_time", None, None),
        ("현재위치 날씨", "location_weather", None, None),
        ("초미세먼지", "pm25Value", "㎍/㎥", SensorDeviceClass.PM25),
        ("초미세먼지등급", "pm25Grade", None, None),
        ("최고온도", "TMX_today", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("최저온도", "TMN_today", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("현재날씨", "current_condition", None, None),
        ("현재풍속", "WSD", UnitOfSpeed.METERS_PER_SECOND, SensorDeviceClass.WIND_SPEED),
        ("현재풍향", "VEC_KOR", None, None),
    ]

    entities = [KMACustomSensor(coordinator, entry, *s) for s in sensors]
    
    # API 만료 안내 센서 추가 (기기가 만들어질 때 자동으로 생성됨)
    entities.append(APIExpirationSensor(entry))
    
    async_add_entities(entities)

class KMACustomSensor(CoordinatorEntity, SensorEntity):
    """기상청 및 에어코리아 데이터를 표시하는 센서."""
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, name, key, unit, dev_class):
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = dev_class
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "기상청",
        }

    @property
    def native_value(self):
        data = self.coordinator.data
        if not data: return None
        # 미세먼지 관련 키는 air에서, 나머지는 weather에서 가져옴
        if "pm" in self._key:
            return data.get("air", {}).get(self._key)
        return data.get("weather", {}).get(self._key)

class APIExpirationSensor(SensorEntity):
    """API 인증키 만료 안내 센서."""
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "일"

    def __init__(self, entry):
        self._attr_name = "API 인증키 남은 일수"
        self._attr_unique_id = f"{entry.entry_id}_api_expiry"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
        }

    @property
    def native_value(self):
        # 공공데이터포털 API는 보통 2년(730일) 단위로 갱신이 필요합니다.
        # 실제 발급일을 알 수 없으므로 우선 고정값을 보여주며, 필요 시 계산 로직 추가가 가능합니다.
        return 730
