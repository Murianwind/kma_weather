"""Config flow for KMA Weather."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from .const import DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY

class KMAConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for KMA Weather."""
    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if user_input is not None:
            # 엔티티 ID에서 이름을 추출하여 타이틀 생성
            entity_id = user_input[CONF_LOCATION_ENTITY]
            state = self.hass.states.get(entity_id)
            name = state.name if state else entity_id.split('.')[-1]
            title = f"기상청 날씨: {name}"
            
            return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_API_KEY): str,
                # 모든 Zone과 Device Tracker를 하나의 목록으로 제공
                vol.Required(CONF_LOCATION_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["zone", "device_tracker"],
                        multiple=False
                    )
                ),
            })
        )
