from homeassistant.components.weather import WeatherEntity, ForecastMode
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfTemperature, UnitOfSpeed
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN, CONF_PREFIX

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([KMAWeatherEntity(coordinator, entry)])

class KMAWeatherEntity(CoordinatorEntity, WeatherEntity):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._entry = entry
        prefix = entry.data.get(CONF_PREFIX, "kma").lower()
        self.entity_id = f"weather.{prefix}_weather"
        self._attr_name = entry.title
        self._attr_unique_id = f"{entry.entry_id}_weather"
        self._attr_native_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_native_wind_speed_unit = UnitOfSpeed.METERS_PER_SECOND
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Murianwind",
            model="integration",
        )

    @property
    def condition(self):
        if not self.coordinator.data: return None
        return self.coordinator.data["weather"].get("current_condition")

    @property
    def native_temperature(self):
        if not self.coordinator.data: return None
        val = self.coordinator.data["weather"].get("TMP")
        return float(val) if val is not None else None

    @property
    def humidity(self):
        if not self.coordinator.data: return None
        val = self.coordinator.data["weather"].get("REH")
        return float(val) if val is not None else None

    @property
    def native_wind_speed(self):
        if not self.coordinator.data: return None
        val = self.coordinator.data["weather"].get("WSD")
        return float(val) if val is not None else None

    async def async_get_forecasts(self, forecast_type):
        if not self.coordinator.data: return []
        if forecast_type == ForecastMode.DAILY:
            return self.coordinator.data["weather"].get("forecast_daily", [])
        return []from homeassistant.components.weather import WeatherEntity, ForecastMode
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfTemperature, UnitOfSpeed
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN, CONF_PREFIX

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([KMAWeatherEntity(coordinator, entry)])

class KMAWeatherEntity(CoordinatorEntity, WeatherEntity):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._entry = entry
        prefix = entry.data.get(CONF_PREFIX, "kma").lower()
        self.entity_id = f"weather.{prefix}_weather"
        self._attr_name = entry.title
        self._attr_unique_id = f"{entry.entry_id}_weather"
        self._attr_native_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_native_wind_speed_unit = UnitOfSpeed.METERS_PER_SECOND
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Murianwind",
            model="integration",
        )

    @property
    def condition(self):
        if not self.coordinator.data: return None
        return self.coordinator.data["weather"].get("current_condition")

    @property
    def native_temperature(self):
        if not self.coordinator.data: return None
        val = self.coordinator.data["weather"].get("TMP")
        return float(val) if val is not None else None

    @property
    def humidity(self):
        if not self.coordinator.data: return None
        val = self.coordinator.data["weather"].get("REH")
        return float(val) if val is not None else None

    @property
    def native_wind_speed(self):
        if not self.coordinator.data: return None
        val = self.coordinator.data["weather"].get("WSD")
        return float(val) if val is not None else None

    async def async_get_forecasts(self, forecast_type):
        if not self.coordinator.data: return []
        if forecast_type == ForecastMode.DAILY:
            return self.coordinator.data["weather"].get("forecast_daily", [])
        return []
