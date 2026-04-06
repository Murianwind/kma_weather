import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from .const import *

class KMAConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            # 선택된 엔티티에 따라 타이틀 결정
            title = "기상청 날씨"
            if user_input[CONF_LOCATION_TYPE] == LOCATION_TYPE_ZONE:
                title = f"날씨: {user_input.get(CONF_ZONE_ID).split('.')[-1]}"
            else:
                title = f"이동형 날씨: {user_input.get(CONF_MOBILE_DEVICE_ID).split('.')[-1]}"
            
            return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_KMA_API_KEY): str,
                vol.Required(CONF_AIR_API_KEY): str,
                vol.Required(CONF_LOCATION_TYPE, default=LOCATION_TYPE_ZONE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": LOCATION_TYPE_ZONE, "label": "고정 지역 (Zone)"},
                            {"value": LOCATION_TYPE_MOBILE, "label": "이동 기기 (Mobile App)"}
                        ], mode=selector.SelectSelectorMode.DROPDOWN
                    )
                ),
                # 사용자가 선택한 타입에 맞는 엔티티만 입력하도록 유도
                vol.Optional(CONF_ZONE_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="zone")
                ),
                vol.Optional(CONF_MOBILE_DEVICE_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="device_tracker")
                ),
            })
        )
