import logging
from datetime import datetime, timedelta, timezone
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .api_kma import KMAWeatherAPI
from .const import (
    DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY,
    CONF_REG_ID_TEMP, CONF_REG_ID_LAND, convert_grid
)

_LOGGER = logging.getLogger(__name__)

class KMAWeatherUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            # HA 코디네이터 자체의 회전 주기는 5분으로 짧게 설정 (GPS 기민성 확보)
            update_interval=timedelta(minutes=5),
        )
        self.entry = entry
        self.api = KMAWeatherAPI(
            session=async_get_clientsession(hass),
            api_key=entry.data.get(CONF_API_KEY),
            reg_id_temp=entry.data.get(CONF_REG_ID_TEMP, "11B10101"),
            reg_id_land=entry.data.get(CONF_REG_ID_LAND, "11B00000"),
        )
        
        # 이전 위치 및 API 호출 상태를 기억하기 위한 변수 (스마트 폴링용)
        self._last_lat = None
        self._last_lon = None
        self._last_nx = None
        self._last_ny = None
        self._last_api_update = None
        self._cached_data = None

    async def _async_update_data(self):
        try:
            entity_id = self.entry.data.get(CONF_LOCATION_ENTITY, "")
            state = self.hass.states.get(entity_id)

            lat, lon = None, None

            # 1. 엔티티에서 좌표 획득 시도
            if state and "latitude" in state.attributes and "longitude" in state.attributes:
                lat = float(state.attributes["latitude"])
                lon = float(state.attributes["longitude"])
            elif self.hass.config.latitude:  # 위치를 못 찾으면 HA 설정 기본 집 위치 사용
                lat = float(self.hass.config.latitude)
                lon = float(self.hass.config.longitude)

            # 2. 좌표 유효성 검사 및 폴백
            if lat is not None and lon is not None:
                current_lat, current_lon = lat, lon
            elif self._last_lat is not None:
                _LOGGER.warning("위치 정보를 가져올 수 없어 이전 좌표를 사용합니다.")
                current_lat, current_lon = self._last_lat, self._last_lon
            else:
                raise UpdateFailed("위치 정보가 없습니다. 엔티티 상태를 확인하세요.")

            # 현재 위도/경도를 기상청 격자(nx, ny)로 변환
            current_nx, current_ny = convert_grid(current_lat, current_lon)
            now = datetime.now(timezone.utc)

            # 3. API 호출 조건 검사 (스마트 폴링 핵심 로직)
            needs_api_call = False
            
            # 조건 A: 캐시된 데이터가 아예 없는 초기 구동 시
            if not self._cached_data:
                needs_api_call = True
            # 조건 B: 기상청 격자(nx, ny)가 변경될 만큼 유의미한 이동이 발생했을 때
            elif self._last_nx != current_nx or self._last_ny != current_ny:
                _LOGGER.info("기상청 격자 변경 감지. 날씨 API 갱신을 즉시 수행합니다.")
                needs_api_call = True
            # 조건 C: 격자는 그대로지만, 마지막 API 호출 후 1시간 이상 경과했을 때
            elif self._last_api_update is None or (now - self._last_api_update) >= timedelta(hours=1):
                needs_api_call = True

            # 4. API 호출 수행 및 저장
            if needs_api_call:
                data = await self.api.fetch_data(current_lat, current_lon, current_nx, current_ny)
                data["weather"]["last_updated"] = now

                data["weather"]["debug_nx"] = current_nx
                data["weather"]["debug_ny"] = current_ny
                data["weather"]["debug_lat"] = round(current_lat, 5)
                data["weather"]["debug_lon"] = round(current_lon, 5)
                data["weather"]["debug_reg_id_temp"] = self.entry.data.get(CONF_REG_ID_TEMP, "11B10101")
                data["weather"]["debug_reg_id_land"] = self.entry.data.get(CONF_REG_ID_LAND, "11B00000")
                data["weather"]["debug_station"] = data.get("air", {}).get("station", "정보없음")

                # 다음 폴링을 위해 캐시 및 상태 업데이트
                self._cached_data = data
                self._last_api_update = now
                self._last_lat = current_lat
                self._last_lon = current_lon
                self._last_nx = current_nx
                self._last_ny = current_ny
                
                return data
            else:
                # 5. API 호출 조건 미달 시, 캐시된 데이터 반환 (트래픽 절약)
                return self._cached_data

        except UpdateFailed:
            raise
        except Exception as e:
            # 실패 시에도 에러를 뿜지 않고 이전 데이터를 유지
            if getattr(self, '_cached_data', None):
                _LOGGER.warning("API 업데이트 실패, 이전 데이터를 유지합니다: %s", e)
                return self._cached_data
            raise UpdateFailed(f"기상청 업데이트 실패: {e}")
