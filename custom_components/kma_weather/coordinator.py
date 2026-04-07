import logging
import asyncio
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
            # 위치 체크 주기는 5분으로 기민하게 유지
            update_interval=timedelta(minutes=5),
        )
        self.entry = entry
        self.api = KMAWeatherAPI(
            session=async_get_clientsession(hass),
            api_key=entry.data.get(CONF_API_KEY),
            reg_id_temp=entry.data.get(CONF_REG_ID_TEMP, "11B10101"),
            reg_id_land=entry.data.get(CONF_REG_ID_LAND, "11B00000"),
        )
        
        self._last_lat = None
        self._last_lon = None
        self._last_nx = None
        self._last_ny = None
        self._last_api_update = None
        self._cached_data = None
        # ★ 핵심 보완: 동시성 제어를 위한 락 추가
        self._update_lock = asyncio.Lock()

    async def _async_update_data(self):
        """Fetch data from API with concurrency control and fail-safe logic."""
        async with self._update_lock:
            try:
                entity_id = self.entry.data.get(CONF_LOCATION_ENTITY, "")
                state = self.hass.states.get(entity_id)

                lat, lon = None, None

                # 1. 위치 정보 획득
                if state and "latitude" in state.attributes and "longitude" in state.attributes:
                    lat = float(state.attributes["latitude"])
                    lon = float(state.attributes["longitude"])
                elif self.hass.config.latitude:
                    lat = float(self.hass.config.latitude)
                    lon = float(self.hass.config.longitude)

                if lat is not None and lon is not None:
                    current_lat, current_lon = lat, lon
                elif self._last_lat is not None:
                    current_lat, current_lon = self._last_lat, self._last_lon
                else:
                    # 위치 정보가 아예 없는 초기 상황
                    if self._cached_data: return self._cached_data
                    raise UpdateFailed("위치 정보를 확인할 수 없습니다.")

                current_nx, current_ny = convert_grid(current_lat, current_lon)
                now = datetime.now(timezone.utc)

                # 2. 스마트 폴링 판단 (API 호출 여부 결정)
                needs_api_call = False
                if not self._cached_data:
                    needs_api_call = True
                elif self._last_nx != current_nx or self._last_ny != current_ny:
                    _LOGGER.info("기상청 격자 변경 감지. 날씨 API를 즉시 갱신합니다.")
                    needs_api_call = True
                elif self._last_api_update is None or (now - self._last_api_update) >= timedelta(hours=1):
                    needs_api_call = True

                # 3. API 호출 및 가용성(Fail-Safe) 처리
                if needs_api_call:
                    new_data = await self.api.fetch_data(current_lat, current_lon, current_nx, current_ny)
                    
                    if new_data is None:
                        # API에서 None을 주면(실패) 기존 데이터 유지
                        if self._cached_data:
                            _LOGGER.warning("새 데이터를 가져오지 못했습니다. 캐시된 데이터를 사용합니다.")
                            return self._cached_data
                        raise UpdateFailed("데이터 수집 실패 및 캐시된 정보 없음")

                    # 성공 시 메타데이터와 함께 저장
                    new_data["weather"]["last_updated"] = now
                    self._cached_data = new_data
                    self._last_api_update = now
                    self._last_lat, self._last_lon = current_lat, current_lon
                    self._last_nx, self._last_ny = current_nx, current_ny
                    
                    return new_data
                
                # 호출 주기가 아니면 기존 데이터 반환
                return self._cached_data

            except Exception as e:
                # 4. 어떤 예외가 발생해도 캐시가 있으면 엔티티 사망 방지
                if self._cached_data:
                    _LOGGER.warning("업데이트 중 오류 발생, 마지막 성공 데이터 유지: %s", e)
                    return self._cached_data
                raise UpdateFailed(f"업데이트 실패: {e}")
