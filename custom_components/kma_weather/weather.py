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
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        KMAWeather(coordinator, entry),
        KMAWeatherSummary(coordinator, entry)
    ])

class KMAWeather(CoordinatorEntity, WeatherEntity):
    _attr_has_entity_name = True
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
        self.entity_id = f"weather.{prefix}_weather"
        self._attr_name = entry.title
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
        try: return int(float(val)) if val is not None else None
        except: return val

    @property
    def humidity(self):
        val = (self.coordinator.data or {}).get("weather", {}).get("REH")
        try: return int(float(val)) if val is not None else None
        except: return val

    @property
    def native_wind_speed(self):
        return (self.coordinator.data or {}).get("weather", {}).get("WSD")

    @property
    def wind_bearing(self):
        return (self.coordinator.data or {}).get("weather", {}).get("VEC")

    @property
    def condition(self):
        return (self.coordinator.data or {}).get("weather", {}).get("current_condition")

    async def async_forecast_daily(self) -> list[Forecast]:
        twice = (self.coordinator.data or {}).get("weather", {}).get("forecast_twice_daily", [])
        return twice[::2] if twice else []

    async def async_forecast_twice_daily(self) -> list[Forecast]:
        return (self.coordinator.data or {}).get("weather", {}).get("forecast_twice_daily", [])

class KMAWeatherSummary(CoordinatorEntity, WeatherEntity):
    _attr_has_entity_name = False
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    
    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        from homeassistant.util import slugify
        prefix = slugify(entry.data.get(CONF_PREFIX, "kma"))
        self.entity_id = f"weather.{prefix}_weather_summary"
        
        # [수정] 이름을 "날씨 요약"으로 고정 (접두사 중복 방지)
        self._attr_name = "날씨 요약"
        
        self._attr_unique_id = f"{entry.entry_id}_summary"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Murianwind",
            model="integration"
        )

    @property
    def native_temperature(self):
        val = (self.coordinator.data or {}).get("weather", {}).get("TMP")
        try: return int(float(val)) if val is not None else None
        except: return val

    @property
    def condition(self):
        return (self.coordinator.data or {}).get("weather", {}).get("current_condition")

    @property
    def extra_state_attributes(self):
        w = (self.coordinator.data or {}).get("weather", {})
        return {
            "today_max": w.get("TMX_today"),
            "today_min": w.get("TMN_today"),
            "tomorrow_max": w.get("TMX_tomorrow"),
            "tomorrow_min": w.get("TMN_tomorrow"),
            "rain_start": w.get("rain_start_time"),
            "apparent_temp": w.get("apparent_temp")
        }
