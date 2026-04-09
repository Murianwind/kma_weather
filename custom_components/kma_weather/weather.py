import logging
from homeassistant.components.weather import (
    WeatherEntity, 
    WeatherEntityFeature, 
    Forecast
)
from homeassistant.const import (
    UnitOfTemperature, 
    UnitOfSpeed, 
    UnitOfPressure
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN, CONF_PREFIX

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """엔티티 등록 - 여기서 딱 하나만 등록하도록 보장합니다."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    # [수정] 오직 실제 데이터가 있는 KMAWeather 하나만 생성합니다.
    async_add_entities([
        KMAWeather(coordinator, entry)
    ])

class KMAWeather(CoordinatorEntity, WeatherEntity):
    """실제 모든 날씨 데이터를 포함하는 메인 엔티티."""

    # [수정] False로 설정해야 '기상청 날씨: Murian의 아이폰' 같은 접두사가 붙지 않습니다.
    _attr_has_entity_name = False 
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_wind_speed_unit = UnitOfSpeed.METERS_PER_SECOND
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_supported_features = (
        WeatherEntityFeature.FORECAST_DAILY | 
        WeatherEntityFeature.FORECAST_TWICE_DAILY
    )

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        from homeassistant.util import slugify
        prefix = slugify(entry.data.get(CONF_PREFIX, "kma"))
        
        # ID는 기존 ID를 유지하여 설정을 보존합니다.
        self.entity_id = f"weather.{prefix}_weather"
        
        # [수정] 표시 이름을 "날씨 요약"으로 고정합니다.
        self._attr_name = "날씨 요약"
        
        self._attr_unique_id = f"{entry.entry_id}_weather"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Murianwind",
            model="integration"
        )

    @property
    def native_temperature(self):
        val = (self.coordinator.data or {}).get("weather", {}).get("TMP")
        if val == "-": return None # 수동 처리 추가
        try: return int(float(val)) if val is not None else None
        except: return None # 에러 시 원본 대신 None 반환

    @property
    def humidity(self):
        val = (self.coordinator.data or {}).get("weather", {}).get("REH")
        if val == "-": return None
        try: return int(float(val)) if val is not None else None
        except: return None

    @property
    def native_wind_speed(self):
        return (self.coordinator.data or {}).get("weather", {}).get("WSD")

    @property
    def wind_bearing(self):
        return (self.coordinator.data or {}).get("weather", {}).get("VEC")

    @property
    def condition(self):
        return (self.coordinator.data or {}).get("weather", {}).get("current_condition")

    @property
    def extra_state_attributes(self):
        """기존 KMAWeatherSummary가 가졌던 요약 정보들을 이 속성으로 모두 합칩니다."""
        w = (self.coordinator.data or {}).get("weather", {})
        return {
            "today_max": w.get("TMX_today"),
            "today_min": w.get("TMN_today"),
            "tomorrow_max": w.get("TMX_tomorrow"),
            "tomorrow_min": w.get("TMN_tomorrow"),
            "rain_start": w.get("rain_start_time"),
            "apparent_temp": w.get("apparent_temp"),
            "address": w.get("address")
        }

    async def async_forecast_daily(self) -> list[Forecast]:
        twice = (self.coordinator.data or {}).get("weather", {}).get("forecast_twice_daily", [])
        return twice[::2] if twice else []

    async def async_forecast_twice_daily(self) -> list[Forecast]:
        return (self.coordinator.data or {}).get("weather", {}).get("forecast_twice_daily", [])

# [수정] KMAWeatherSummary 클래스를 여기서 완전히 삭제(또는 주석 처리)했습니다.
