import logging
from homeassistant.components.weather import (
    WeatherEntity,
    WeatherEntityFeature,
    Forecast,
)
from homeassistant.const import (
    UnitOfTemperature,
    UnitOfPressure,
    UnitOfSpeed,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN, CONF_PREFIX

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([KMAWeather(coordinator, entry)])

class KMAWeather(CoordinatorEntity, WeatherEntity):
    _attr_has_entity_name = True
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_wind_speed_unit = UnitOfSpeed.METERS_PER_SECOND
    _attr_supported_features = WeatherEntityFeature.FORECAST_TWICE_DAILY

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._entry = entry
        prefix = entry.data.get(CONF_PREFIX, "kma")
        self.entity_id = f"weather.{prefix}_weather"
        self._attr_unique_id = f"{entry.entry_id}_weather"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Murianwind",
            model="integration"
        )

    def _get_safe_val(self, key):
        """데이터 누락 기호 '-'를 None으로 변환하는 헬퍼 함수"""
        val = self.coordinator.data.get("weather", {}).get(key)
        return None if val == "-" else val

    @property
    def native_temperature(self):
        val = self._get_safe_val("TMP")
        try: return float(val) if val is not None else None
        except (ValueError, TypeError): return None

    @property
    def humidity(self):
        val = self._get_safe_val("REH")
        try: return int(float(val)) if val is not None else None
        except (ValueError, TypeError): return None

    @property
    def native_wind_speed(self):
        val = self._get_safe_val("WSD")
        try: return float(val) if val is not None else None
        except (ValueError, TypeError): return None

    @property
    def condition(self):
        return self.coordinator.data.get("weather", {}).get("current_condition_eng")

    async def async_get_forecasts_twice_daily(self) -> list[Forecast]:
        return self.coordinator.data.get("forecast", [])
