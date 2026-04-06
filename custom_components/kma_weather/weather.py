from homeassistant.components.weather import WeatherEntity, WeatherEntityFeature, Forecast
from .const import DOMAIN

class KMAWeatherEntity(WeatherEntity):
    _attr_has_entity_name = True
    _attr_supported_features = WeatherEntityFeature.FORECAST_DAILY | WeatherEntityFeature.FORECAST_HOURLY

    @property
    def extra_state_attributes(self):
        w = self.coordinator.data.get("weather", {})
        return {
            "today_max": w.get("TMX_today"),
            "today_min": w.get("TMN_today"),
            "tomorrow_am": w.get("weather_am_tomorrow"),
            "tomorrow_pm": w.get("weather_pm_tomorrow"),
            "day3_am": w.get("wf3Am"), # 중기예보: 3일 뒤 오전
            "day3_pm": w.get("wf3Pm"), # 중기예보: 3일 뒤 오후
            "day3_max": w.get("taMax3"),
            "day3_min": w.get("taMin3"),
            "location": w.get("location_name"),
        }

    async def async_forecast_daily(self) -> list[Forecast]:
        """HA 대시보드 하단에 주간 리스트 표시"""
        # 단기 + 중기 데이터를 합친 리스트 반환
        return self.coordinator.data.get("daily_forecast_list", [])
