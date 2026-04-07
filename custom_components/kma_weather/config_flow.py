import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv
from .const import (
    DOMAIN, CONF_API_KEY, CONF_NAME, CONF_LOCATION_ENTITY, 
    CONF_PREFIX, CONF_EXPIRE_DATE, CONF_APPLY_DATE
)

class KMAWeatherConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    async def async_step_user(self, user_input=None):
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_API_KEY]}_{user_input[CONF_PREFIX]}")
            self._abort_if_unique_id_configured()
            # title에 사용자가 입력한 기기 이름을 사용합니다.
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_API_KEY): str,
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_PREFIX): str,
                vol.Optional(CONF_LOCATION_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain=["zone", "device_tracker", "person"])),
                vol.Optional(CONF_APPLY_DATE): cv.string,
                vol.Optional(CONF_EXPIRE_DATE): cv.string,
            }),
            description_placeholders={"api_link": "https://www.data.go.kr/"}
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
