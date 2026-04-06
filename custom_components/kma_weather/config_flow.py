"""Config flow for KMA Weather."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from .const import DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY, CONF_PREFIX

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
                # 기본값(default)을 제거하여 완전한 빈칸으로 시작되도록 수정
                vol.Required(CONF_PREFIX): str,
            })
        )
