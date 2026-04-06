from homeassistant.components.weather import WeatherEntity, WeatherEntityFeature
from homeassistant.const import UnitOfTemperature, UnitOfSpeed
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([KMAWeatherEntity(coordinator, entry)])

class KMAWeatherEntity(WeatherEntity):
    _attr_has_entity_name = True
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_speed_unit = UnitOfSpeed.METERS_PER_SECOND
    _attr_native_pressure_unit = "hPa"
    _attr_native_precipitation_unit = "mm"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_weather"
        self._attr_name = "날씨 요약"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title}

    @property
    def condition(self):
        return self.coordinator.data.get("weather", {}).get("current_condition")

    @property
    def native_temperature(self):
        return self.coordinator.data.get("weather", {}).get("TMP")

    @property
    def native_humidity(self):
        return self.coordinator.data.get("weather", {}).get("REH")

    @property
    def native_wind_speed(self):
        return self.coordinator.data.get("weather", {}).get("WSD")

    @property
    def wind_bearing(self):
        return self.coordinator.data.get("weather", {}).get("VEC_KOR")
