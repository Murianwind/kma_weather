"""Weather platform for KMA Weather."""
from homeassistant.components.weather import (
    WeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfSpeed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up KMA Weather entity."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([KMAWeatherEntity(coordinator, entry)])

class KMAWeatherEntity(CoordinatorEntity, WeatherEntity):
    """Representation of KMA Weather."""
    _attr_has_entity_name = True
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_speed_unit = UnitOfSpeed.METERS_PER_SECOND
    _attr_native_pressure_unit = "hPa"
    _attr_native_precipitation_unit = "mm"
    _attr_supported_features = WeatherEntityFeature.FORECAST_DAILY

    def __init__(self, coordinator, entry):
        """Initialize the weather entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_weather"
        self._attr_name = "날씨 요약"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
        }

    @property
    def condition(self):
        """현재 날씨 상태."""
        return self.coordinator.data.get("weather", {}).get("current_condition")

    @property
    def native_temperature(self):
        """현재 기온."""
        return self.coordinator.data.get("weather", {}).get("TMP")

    @property
    def native_humidity(self):
        """현재 습도."""
        return self.coordinator.data.get("weather", {}).get("REH")

    @property
    def native_wind_speed(self):
        """현재 풍속."""
        return self.coordinator.data.get("weather", {}).get("WSD")

    @property
    def wind_bearing(self):
        """현재 풍향."""
        return self.coordinator.data.get("weather", {}).get("VEC_KOR")

    @property
    def extra_state_attributes(self):
        """상세 속성."""
        w = self.coordinator.data.get("weather", {})
        return {
            "today_max": w.get("TMX_today"),
            "today_min": w.get("TMN_today"),
            "tomorrow_am": w.get("weather_am_tomorrow"),
            "tomorrow_pm": w.get("weather_pm_tomorrow"),
            "location": w.get("location_weather"),
            "attribution": "기상청 및 에어코리아 API",
        }
