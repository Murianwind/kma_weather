import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from .const import DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY, CONF_PREFIX, CONF_APPLY_DATE, CONF_EXPIRE_DATE

class KMAConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            entity_id = user_input[CONF_LOCATION_ENTITY]
            state = self.hass.states.get(entity_id)
            name = state.name if state else entity_id.split('.')[-1]
            return self.async_create_entry(title=f"기상청 날씨: {name}", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_API_KEY): str,
                vol.Required(CONF_LOCATION_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["zone", "device_tracker"])
                ),
                vol.Required(CONF_PREFIX): str,
                vol.Optional(CONF_APPLY_DATE): str,
                vol.Optional(CONF_EXPIRE_DATE): str,
            })
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return KMAOptionsFlow(config_entry)

class KMAOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            # 설정 업데이트 시 즉시 반영을 위해 entry 업데이트 트리거
            return self.async_create_entry(title="", data=user_input)

        # 현재 설정값 불러오기 (options 최우선, 없으면 data)
        current_apply = self.config_entry.options.get(CONF_APPLY_DATE) or self.config_entry.data.get(CONF_APPLY_DATE, "")
        current_expire = self.config_entry.options.get(CONF_EXPIRE_DATE) or self.config_entry.data.get(CONF_EXPIRE_DATE, "")

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(CONF_APPLY_DATE, default=current_apply): str,
                vol.Optional(CONF_EXPIRE_DATE, default=current_expire): str,
            })
        )
