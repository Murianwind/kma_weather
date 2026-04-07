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

            entity_id = user_input.get(CONF_LOCATION_ENTITY)
            state = self.hass.states.get(entity_id) if entity_id else None
            name = state.name if state else entity_id.split('.')[-1] if entity_id else "우리집"
            
            return self.async_create_entry(
                title=f"기상청 날씨: {name}",
                data=user_input
            )

        # strings.json의 description(4개 API 신청 링크)이 출력되도록 설정
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
            })
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
                vol.Optional(CONF_LOCATION_ENTITY, default=self._config_entry.options.get(CONF_LOCATION_ENTITY, self._config_entry.data.get(CONF_LOCATION_ENTITY, ""))): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["zone", "device_tracker"])
                ),
                vol.Optional(CONF_APPLY_DATE, default=self._config_entry.options.get(CONF_APPLY_DATE, self._config_entry.data.get(CONF_APPLY_DATE, ""))): cv.string,
                vol.Optional(CONF_EXPIRE_DATE, default=self._config_entry.options.get(CONF_EXPIRE_DATE, self._config_entry.data.get(CONF_EXPIRE_DATE, ""))): cv.string,
            })
        )
