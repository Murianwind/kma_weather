"""Weather platform for KMA Weather."""
from homeassistant.components.weather import (
    WeatherEntity,
    WeatherEntityFeature,
    Forecast,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, CONF_PREFIX

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
    
    # [수정] 단위를 상수가 아닌 문자열 "m/s"로 지정하여 HA의 강제 변환(km/h 등) 차단
    _attr_native_speed_unit = "m/s" 
    
    _attr_native_pressure_unit = "hPa"
    _attr_native_precipitation_unit = "mm"
    
    _attr_supported_features = WeatherEntityFeature.FORECAST_DAILY | WeatherEntityFeature.FORECAST_TWICE_DAILY

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        prefix = entry.data.get(CONF_PREFIX, "kma").lower()
        self.entity_id = f"weather.{prefix}_weather_summary"
        self._attr_unique_id = f"{entry.entry_id}_weather"
        self._attr_name = "날씨 요약"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title}

    @property
    def condition(self):
        return self.coordinator.data.get("weather", {}).get("current_condition")

    @property
    def native_temperature(self):
        try: return int(float(self.coordinator.data.get("weather", {}).get("TMP", 0)))
        except Exception: return None

    @property
    def native_humidity(self):
        try: return int(float(self.coordinator.data.get("weather", {}).get("REH", 0)))
        except Exception: return None

    @property
    def native_wind_speed(self):
        # 기상청 원본 m/s 데이터를 그대로 반환
        try: return float(self.coordinator.data.get("weather", {}).get("WSD", 0))
        except Exception: return None

    @property
    def wind_bearing(self):
        return self.coordinator.data.get("weather", {}).get("VEC_KOR")

    async def async_forecast_daily(self) -> list[Forecast] | None:
        return self.coordinator.data.get("weather", {}).get("forecast_daily", [])

    async def async_forecast_twice_daily(self) -> list[Forecast] | None:
        return self.coordinator.data.get("weather", {}).get("forecast_twice_daily", [])

    @property
    def extra_state_attributes(self):
        w = self.coordinator.data.get("weather", {})
        return {
            "today_max": w.get("TMX_today"),
            "today_min": w.get("TMN_today"),
            "tomorrow_am": w.get("weather_am_tomorrow"),
            "tomorrow_pm": w.get("weather_pm_tomorrow"),
            "location": w.get("location_weather"),
            "attribution": "기상청 및 에어코리아 API",
        }
