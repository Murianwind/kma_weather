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
    """엔티티 등록 - 기상청 날씨 엔티티 하나를 등록합니다."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    # 실제 데이터가 포함된 KMAWeather 엔티티를 생성합니다.
    async_add_entities([
        KMAWeather(coordinator, entry)
    ])

class KMAWeather(CoordinatorEntity, WeatherEntity):
    """기상청 날씨 데이터를 제공하는 메인 엔티티 클래스입니다."""

    # 표시 이름 설정을 위해 False로 지정합니다.
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
        
        # 고유 ID 및 엔티티 ID 설정
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
        """데이터 누락 기호 '-'를 None으로 처리하는 내부 함수입니다."""
        val = (self.coordinator.data or {}).get("weather", {}).get(key)
        return None if val == "-" else val

    @property
    def native_temperature(self):
        """현재 온도를 반환합니다. 수치 변환 에러 시 None을 반환합니다."""
        val = self._get_safe_val("TMP")
        try:
            return float(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    @property
    def humidity(self):
        """현재 습도를 반환합니다."""
        val = self._get_safe_val("REH")
        try:
            return int(float(val)) if val is not None else None
        except (ValueError, TypeError):
            return None

    @property
    def native_wind_speed(self):
        """현재 풍속을 반환합니다."""
        val = self._get_safe_val("WSD")
        try:
            return float(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    @property
    def wind_bearing(self):
        """풍향(도)을 반환합니다."""
        val = self._get_safe_val("VEC")
        try:
            return float(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    @property
    def condition(self):
        """현재 날씨 상태(영문)를 반환합니다."""
        return (self.coordinator.data or {}).get("weather", {}).get("current_condition_eng")

    @property
    def extra_state_attributes(self):
        """센서의 상세 정보를 속성으로 제공합니다."""
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
        """일별 예보 데이터를 반환합니다."""
        return (self.coordinator.data or {}).get("weather", {}).get("forecast_daily", [])

    async def async_forecast_twice_daily(self) -> list[Forecast]:
        """오전/오후 예보 데이터를 반환합니다."""
        return (self.coordinator.data or {}).get("weather", {}).get("forecast_twice_daily", [])
