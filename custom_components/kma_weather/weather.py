from homeassistant.components.weather import WeatherEntity, WeatherEntityFeature, Forecast
from homeassistant.const import UnitOfTemperature, UnitOfSpeed, UnitOfPressure
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN, CONF_PREFIX

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([KMAWeather(coordinator, entry)])

class KMAWeather(CoordinatorEntity, WeatherEntity):
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_wind_speed_unit = UnitOfSpeed.METERS_PER_SECOND
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_supported_features = WeatherEntityFeature.FORECAST_DAILY | WeatherEntityFeature.FORECAST_TWICE_DAILY

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        from homeassistant.util import slugify
        prefix = slugify(entry.data.get(CONF_PREFIX, "kma"))
        self.entity_id = f"weather.{prefix}_weather"
        self._attr_name = entry.title
        self._attr_unique_id = f"{entry.entry_id}_weather"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=entry.title, manufacturer="Murianwind", model="integration")

    @property
    def native_temperature(self):
        return (self.coordinator.data or {}).get("weather", {}).get("TMP")

    @property
    def native_pressure(self):
        # 기상청 단기예보에는 기압 데이터가 없으므로 None 반환 (PCP 매핑 제거)
        return None

    @property
    def humidity(self):
        return (self.coordinator.data or {}).get("weather", {}).get("REH")

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
        return (self.coordinator.data or {}).get("weather", {}).get("forecast_daily", [])

    async def async_forecast_twice_daily(self) -> list[Forecast]:
        return (self.coordinator.data or {}).get("weather", {}).get("forecast_twice_daily", [])
