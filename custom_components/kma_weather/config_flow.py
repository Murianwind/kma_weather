import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv
from .const import DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY, CONF_PREFIX, CONF_EXPIRE_DATE, CONF_APPLY_DATE

class KMAWeatherConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_API_KEY]}_{user_input[CONF_PREFIX]}")
            self._abort_if_unique_id_configured()
            
            # 주소 정보를 가져와서 기기 이름(Title)으로 사용 (이전 방식 복구)
            # 여기서는 우선 prefix를 사용하고, 필요시 통합구성요소 목록에서 직접 수정 가능합니다.
            return self.async_create_entry(title=user_input[CONF_PREFIX], data=user_input)

        # 스크린샷과 동일한 구성 + API 안내 문구 복구
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_API_KEY): str,
                vol.Required(CONF_PREFIX, default="kma"): str,
                vol.Optional(CONF_LOCATION_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["zone", "device_tracker", "person"])
                ),
                vol.Optional(CONF_APPLY_DATE): cv.string,
                vol.Optional(CONF_EXPIRE_DATE): cv.string,
            }),
            # UI에 표시될 API 신청 안내 문구 (description_placeholders 활용)
            description_placeholders={
                "api_link": "https://www.data.go.kr/",
                "api_guide": "기상청 API 키가 없으시면 공공데이터포털에서 신청하세요."
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
