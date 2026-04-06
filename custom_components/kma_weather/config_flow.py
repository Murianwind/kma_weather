"""Config flow for KMA Weather."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from .const import (
    DOMAIN,
    CONF_API_KEY,
    CONF_LOCATION_TYPE,
    CONF_ZONE_ID,
    CONF_MOBILE_DEVICE_ID,
    LOCATION_TYPE_ZONE,
    LOCATION_TYPE_MOBILE,
)

class KMAWeatherConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for KMA Weather."""
    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if user_input is not None:
            title = "기상청 날씨"
            if user_input[CONF_LOCATION_TYPE] == LOCATION_TYPE_ZONE:
                if user_input.get(CONF_ZONE_ID):
                    title = f"날씨: {user_input[CONF_ZONE_ID].split('.')[-1]}"
            else:
                if user_input.get(CONF_MOBILE_DEVICE_ID):
                    title = f"이동형 날씨: {user_input[CONF_MOBILE_DEVICE_ID].split('.')[-1]}"
            
            return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_API_KEY): str,
                vol.Required(CONF_LOCATION_TYPE, default=LOCATION_TYPE_ZONE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": LOCATION_TYPE_ZONE, "label": "고정 지역 (Zone)"},
                            {"value": LOCATION_TYPE_MOBILE, "label": "이동 기기 (Mobile App)"}
                        ], mode=selector.SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Optional(CONF_ZONE_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="zone")
                ),
                vol.Optional(CONF_MOBILE_DEVICE_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="device_tracker")
                ),
            })
        )
