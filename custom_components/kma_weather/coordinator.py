from datetime import datetime, timedelta
import logging

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE

from .const import (
    DOMAIN, UPDATE_START_HOUR, UPDATE_START_MIN, 
    LOCATION_TYPE_ZONE, LOCATION_TYPE_MOBILE,
    CONF_LOCATION_TYPE, CONF_ZONE_ID, CONF_MOBILE_DEVICE_ID
)

_LOGGER = logging.getLogger(__name__)

class KMADataUpdateCoordinator(DataUpdateCoordinator):
    """기상청 데이터를 관리하는 코디네이터."""

    def __init__(self, hass, api, entry):
        self.api = api
        self.entry = entry
        
        # 02:15부터 3시간 간격 계산 로직은 _async_update_data 내에서 처리하거나
        # update_interval을 유동적으로 설정할 수 있습니다.
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=30), # 30분마다 위치 변화 및 업데이트 시간 체크
        )

    async def _async_update_data(self):
        """데이터 소스로부터 실제 데이터를 가져오는 함수."""
        try:
            # 1. 현재 설정된 위치 소스에서 위경도 추출
            lat, lon = self._get_current_location()
            
            # 2. 기상청/에어코리아 데이터 호출
            weather_data = await self.api.fetch_weather(lat, lon)
            air_data = await self.api.fetch_air_quality(lat, lon)
            
            return {
                "weather": weather_data,
                "air": air_data,
                "last_lat": lat,
                "last_lon": lon,
                "updated_at": datetime.now()
            }
        except Exception as err:
            raise UpdateFailed(f"Error communication with API: {err}")

    def _get_current_location(self):
        """설정에 따라 Zone 또는 Mobile 기기의 위경도를 가져옴."""
        loc_type = self.entry.data.get(CONF_LOCATION_TYPE)
        
        if loc_type == LOCATION_TYPE_ZONE:
            entity_id = self.entry.data.get(CONF_ZONE_ID)
            state = self.hass.states.get(entity_id)
            if state:
                return state.attributes.get(ATTR_LATITUDE), state.attributes.get(ATTR_LONGITUDE)
        
        elif loc_type == LOCATION_TYPE_MOBILE:
            entity_id = self.entry.data.get(CONF_MOBILE_DEVICE_ID)
            state = self.hass.states.get(entity_id)
            if state:
                # 모바일 앱 센서의 경우 'location_name' 속성이나 'latitude/longitude'를 직접 참조
                # 사용자가 제공한 리스트 형태 [lat, lon] 처리 로직
                loc = state.attributes.get("location")
                if isinstance(loc, list) and len(loc) == 2:
                    return loc[0], loc[1]
                return state.attributes.get(ATTR_LATITUDE), state.attributes.get(ATTR_LONGITUDE)
        
        # 기본값으로 HA 설정의 위치 반환
        return self.hass.config.latitude, self.hass.config.longitude
