"""Weather platform for KMA Weather."""
from __future__ import annotations

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

# UnitOfPrecipitation 대신 직접 문자열 "mm" 사용
UNIT_PRECIPITATION = "mm"

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
    _attr_native_precipitation_unit = UNIT_PRECIPITATION
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
        """Return the current condition."""
        return self.coordinator.data.get("weather", {}).get("current_condition")

    @property
    def native_temperature(self):
        """Return the temperature."""
        try:
            return float(self.coordinator.data.get("weather", {}).get("TMP", 0))
        except (TypeError, ValueError):
            return 0.0

    @property
    def extra_state_attributes(self):
        """Return additional weather attributes."""
        w = self.coordinator.data.get("weather", {})
        return {
            "today_max": w.get("TMX_today"),
            "today_min": w.get("TMN_today"),
            "tomorrow_am": w.get("weather_am_tomorrow"),
            "tomorrow_pm": w.get("weather_pm_tomorrow"),
            "location": w.get("location_weather"),
            "attribution": "기상청 및 에어코리아 API",
        }
