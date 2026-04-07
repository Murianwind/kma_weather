import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
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
    @callback
    def async_get_options_flow(config_entry):
        return KMAOptionsFlow(config_entry)


class KMAOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        """Initialize options flow."""
        # ★ 해결 핵심: HA 예약어와 겹치지 않도록 변수명 앞에 언더바(_)를 추가했습니다.
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # 변수명이 바뀌었으므로 아래 로직들도 self._config_entry 로 호출합니다.
        current_apply = self._config_entry.options.get(CONF_APPLY_DATE)
        if current_apply is None:
            current_apply = self._config_entry.data.get(CONF_APPLY_DATE, "")
        if current_apply is None:
            current_apply = ""

        current_expire = self._config_entry.options.get(CONF_EXPIRE_DATE)
        if current_expire is None:
            current_expire = self._config_entry.data.get(CONF_EXPIRE_DATE, "")
        if current_expire is None:
            current_expire = ""

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(CONF_APPLY_DATE, default=str(current_apply)): str,
                vol.Optional(CONF_EXPIRE_DATE, default=str(current_expire)): str,
            })
        )
