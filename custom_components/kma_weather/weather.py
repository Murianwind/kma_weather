from homeassistant.components.weather import (
    Forecast, WeatherEntity, WeatherEntityFeature,
    ATTR_CONDITION_CLOUDY, ATTR_CONDITION_RAINY, ATTR_CONDITION_SUNNY
)
from homeassistant.const import UnitOfTemperature, UnitOfPrecipitation
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([KMAWeather(coordinator, entry)])

class KMAWeather(WeatherEntity):
    """기상청 예보 엔티티."""
    _attr_has_entity_name = True
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_precipitation_unit = UnitOfPrecipitation.MILLIMETERS
    _attr_supported_features = WeatherEntityFeature.FORECAST_HOURLY

    def __init__(self, coordinator, entry):
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_weather"
        self._attr_name = "기상청 날씨"

    @property
    def native_temperature(self):
        """현재 기온 (T1H 또는 TMP)."""
        data = self.coordinator.data.get("weather")
        # 데이터 파싱 로직 (T1H 등 찾기)
        return data.get("current_temp")

    @property
    def condition(self):
        """하늘 상태(SKY)와 강수형태(PTY)를 조합하여 반환."""
        # SKY: 1(맑음), 3(구름많음), 4(흐림)
        # PTY: 0(없음), 1(비), 2(비/눈), 3(눈), 4(소나기)
        return ATTR_CONDITION_SUNNY # 로직 결과에 따라 가변

    async def async_forecast_hourly(self) -> list[Forecast]:
        """시간별 예보 데이터 변환."""
        forecasts = []
        # coordinator.data["weather"]["forecast"] 데이터를 Forecast 객체 리스트로 변환
        return forecasts
