import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector, aiohttp_client
import homeassistant.helpers.config_validation as cv
from urllib.parse import unquote
from .const import (
    DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY,
    CONF_PREFIX, CONF_EXPIRE_DATE, CONF_APPLY_DATE
)

_LOGGER = logging.getLogger(__name__)

# 공공데이터포털 API 에러 코드 → 사용자 메시지
_API_ERROR_MESSAGES = {
    "01": "어플리케이션 에러가 발생했습니다. 잠시 후 다시 시도해주세요.",
    "10": "잘못된 요청입니다. API 키를 확인해주세요.",
    "20": "서비스 접근이 거부되었습니다. API 키 권한을 확인해주세요.",
    "22": "일일 요청 한도를 초과했습니다. 내일 다시 시도해주세요.",
    "30": "등록되지 않은 API 키입니다. 공공데이터포털에서 키를 확인해주세요.",
    "31": "만료된 API 키입니다. 공공데이터포털에서 키를 갱신해주세요.",
    "32": "트래픽이 초과되었습니다. 잠시 후 다시 시도해주세요.",
}

async def _validate_api_key(hass, api_key: str) -> str | None:
    """
    API 키 유효성 검사.
    성공 시 None 반환, 실패 시 errors 딕셔너리에 쓸 에러 키 반환.
    """
    try:
        session = aiohttp_client.async_get_clientsession(hass)
        decoded_key = unquote(api_key)

        # 단기예보 API로 테스트 호출 (서울 격자 좌표, 날짜/시간은 무관)
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Seoul")
        now = datetime.now(tz)
        adj = now - timedelta(minutes=10)
        valid_hours = [h for h in [2, 5, 8, 11, 14, 17, 20, 23] if h <= adj.hour]
        base_h = max(valid_hours) if valid_hours else 23
        base_d = adj.strftime("%Y%m%d") if valid_hours else (adj - timedelta(days=1)).strftime("%Y%m%d")

        async with session.get(
            "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
            params={
                "serviceKey": decoded_key,
                "dataType": "JSON",
                "base_date": base_d,
                "base_time": f"{base_h:02d}00",
                "nx": 60,
                "ny": 127,
                "numOfRows": 1,
            },
            timeout=10,
        ) as resp:
            if resp.status != 200:
                return "cannot_connect"

            data = await resp.json(content_type=None)

        result_code = (
            data.get("response", {})
                .get("header", {})
                .get("resultCode", "")
        )

        if result_code == "00":
            return None  # 정상

        # 에러 코드별 메시지 로깅
        msg = _API_ERROR_MESSAGES.get(result_code, f"알 수 없는 오류 (코드: {result_code})")
        _LOGGER.warning("API 키 검증 실패: %s", msg)

        # Config Flow errors 키로 매핑
        if result_code in ("30", "31"):
            return "invalid_api_key"
        elif result_code == "22":
            return "api_quota_exceeded"
        elif result_code in ("20", "32"):
            return "api_access_denied"
        else:
            return "api_error"

    except Exception as e:
        _LOGGER.error("API 키 검증 중 오류: %s", e)
        return "cannot_connect"


class KMAWeatherConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            # ── API 키 유효성 검사 ──────────────────────────────────────────
            error_key = await _validate_api_key(self.hass, user_input[CONF_API_KEY])
            if error_key:
                errors[CONF_API_KEY] = error_key
            else:
                # 검증 통과 → 엔트리 생성
                await self.async_set_unique_id(
                    f"{user_input[CONF_API_KEY]}_{user_input[CONF_PREFIX]}"
                )
                self._abort_if_unique_id_configured()

                entity_id = user_input.get(CONF_LOCATION_ENTITY)
                state = self.hass.states.get(entity_id) if entity_id else None
                name = (
                    state.name if state
                    else entity_id.split(".")[-1] if entity_id
                    else "우리집"
                )

                return self.async_create_entry(
                    title=f"기상청 날씨: {name}",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_API_KEY): str,
                vol.Required(CONF_PREFIX): str,
                vol.Optional(CONF_LOCATION_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["zone", "device_tracker"])
                ),
                vol.Optional(CONF_APPLY_DATE): cv.string,
                vol.Optional(CONF_EXPIRE_DATE): cv.string,
            }),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return KMAWeatherOptionsFlowHandler(config_entry)


class KMAWeatherOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_LOCATION_ENTITY,
                    default=self._config_entry.options.get(
                        CONF_LOCATION_ENTITY,
                        self._config_entry.data.get(CONF_LOCATION_ENTITY, "")
                    ),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["zone", "device_tracker"])
                ),
                vol.Optional(
                    CONF_APPLY_DATE,
                    default=self._config_entry.options.get(
                        CONF_APPLY_DATE,
                        self._config_entry.data.get(CONF_APPLY_DATE, "")
                    ),
                ): cv.string,
                vol.Optional(
                    CONF_EXPIRE_DATE,
                    default=self._config_entry.options.get(
                        CONF_EXPIRE_DATE,
                        self._config_entry.data.get(CONF_EXPIRE_DATE, "")
                    ),
                ): cv.string,
            }),
        )
