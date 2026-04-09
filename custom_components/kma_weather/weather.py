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
    async_add_entities([KMAWeather(coordinator, entry)])

class KMAWeather(CoordinatorEntity, WeatherEntity):

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

        self.entity_id = f"weather.{prefix}_weather"
        self._attr_name = "날씨 요약"
        self._attr_unique_id = f"{entry.entry_id}_weather"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Murianwind",
            model="integration"
        )

    def _get_safe_val(self, key):
        val = (self.coordinator.data or {}).get("weather", {}).get(key)
        return None if val == "-" else val

    @property
    def native_temperature(self):
        val = self._get_safe_val("TMP")
        try:
            return float(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    @property
    def humidity(self):
        val = self._get_safe_val("REH")
        try:
            return int(float(val)) if val is not None else None
        except (ValueError, TypeError):
            return None

    @property
    def native_wind_speed(self):
        val = self._get_safe_val("WSD")
        try:
            return float(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    @property
    def wind_bearing(self):
        val = self._get_safe_val("VEC")
        try:
            return float(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    @property
    def condition(self):
        """현재 날씨 상태(HA 표준 영문)를 반환합니다.

        current_condition 은 coordinator 에서 current_condition_kor 와
        항상 동기화되어 저장되므로, 두 값은 동일한 기상 상태를 나타냅니다.
        """
        # ── [수정] 'current_condition_eng'(없는 키) → 'current_condition' ──
        return self._get_safe_val("current_condition")

    @property
    def extra_state_attributes(self):
        w = (self.coordinator.data or {}).get("weather", {})
        return {
            "today_max": w.get("TMX_today"),
            "today_min": w.get("TMN_today"),
            "tomorrow_max": w.get("TMX_tomorrow"),
            "tomorrow_min": w.get("TMN_tomorrow"),
            "rain_start": w.get("rain_start_time"),
            "apparent_temp": w.get("apparent_temp"),
            "address": w.get("address"),
        }

    async def async_forecast_daily(self) -> list[Forecast]:
        return (self.coordinator.data or {}).get("weather", {}).get("forecast_daily", [])

    async def async_forecast_twice_daily(self) -> list[Forecast]:
        return (self.coordinator.data or {}).get("weather", {}).get("forecast_twice_daily", [])
