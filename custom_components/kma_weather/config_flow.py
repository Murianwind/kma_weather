import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv
from .const import (
    DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY, 
    CONF_PREFIX, CONF_EXPIRE_DATE, CONF_APPLY_DATE
)

class KMAWeatherConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    async def async_step_user(self, user_input=None):
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_API_KEY]}_{user_input[CONF_PREFIX]}")
            self._abort_if_unique_id_configured()

            # ★ 사용자님이 지시하신 핀포인트 로직: 엔티티 이름을 가져와 기기 제목 생성
            entity_id = user_input.get(CONF_LOCATION_ENTITY)
            state = self.hass.states.get(entity_id) if entity_id else None
            # 상태값에서 이름을 가져오거나, 없으면 entity_id에서 추출
            name = state.name if state else entity_id.split('.')[-1] if entity_id else "우리집"
            
            return self.async_create_entry(
                title=f"기상청 날씨: {name}", # 예: "기상청 날씨: 우리집"
                data=user_input
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_API_KEY): str,
                vol.Required(CONF_PREFIX): str, # 접두사 기본값 삭제 완료
                vol.Optional(CONF_LOCATION_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["zone", "device_tracker", "person"])
                ),
                vol.Optional(CONF_APPLY_DATE): cv.string,
                vol.Optional(CONF_EXPIRE_DATE): cv.string,
            }),
            # ★ 등록 화면 상단 API 신청 안내 문구 복구
            description_placeholders={
                "api_link": "https://www.data.go.kr/",
                "api_guide": "공공데이터포털에서 '단기예보' API를 신청하고 인증키를 입력하세요."
            }
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return KMAWeatherOptionsFlowHandler(config_entry)

class KMAWeatherOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry): self._config_entry = config_entry
    async def async_step_init(self, user_input=None):
        if user_input is not None: return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(CONF_LOCATION_ENTITY, default=self._config_entry.options.get(CONF_LOCATION_ENTITY, self._config_entry.data.get(CONF_LOCATION_ENTITY, ""))): selector.EntitySelector(selector.EntitySelectorConfig(domain=["zone", "device_tracker", "person"])),
                vol.Optional(CONF_APPLY_DATE, default=self._config_entry.options.get(CONF_APPLY_DATE, self._config_entry.data.get(CONF_APPLY_DATE, ""))): cv.string,
                vol.Optional(CONF_EXPIRE_DATE, default=self._config_entry.options.get(CONF_EXPIRE_DATE, self._config_entry.data.get(CONF_EXPIRE_DATE, ""))): cv.string,
            })
        )
