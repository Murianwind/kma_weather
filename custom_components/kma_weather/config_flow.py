import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from .const import *

class KMAConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="기상청 날씨", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_KMA_API_KEY): str,
                vol.Required(CONF_AIR_API_KEY): str,
                vol.Required(CONF_LOCATION_TYPE, default=LOCATION_TYPE_ZONE): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=[LOCATION_TYPE_ZONE, LOCATION_TYPE_MOBILE])
                ),
                vol.Optional(CONF_ZONE_ID): selector.EntitySelector(selector.EntitySelectorConfig(domain="zone")),
                vol.Optional(CONF_MOBILE_DEVICE_ID): selector.EntitySelector(selector.EntitySelectorConfig(domain="device_tracker")),
            })
        )
