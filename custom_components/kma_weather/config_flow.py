import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from .const import (
    DOMAIN, CONF_KMA_API_KEY, CONF_AIR_API_KEY, 
    CONF_LOCATION_TYPE, CONF_ZONE_ID, CONF_MOBILE_DEVICE_ID,
    LOCATION_TYPE_ZONE, LOCATION_TYPE_MOBILE
)

class KMAWeatherConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for KMA Weather."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            # 여기에서 API 키 유효성 체크 로직을 추가할 수 있습니다.
            return self.async_create_entry(title="기상청 날씨", data=user_input)

        # 1. Zone 목록 가져오기
        zones = [
            selector.SelectOptionDict(value=z.entity_id, label=z.name)
            for z in self.hass.states.async_all("zone")
        ]
        
        # 2. Device Tracker(모바일) 목록 가져오기
        devices = [
            selector.SelectOptionDict(value=d.entity_id, label=d.attributes.get("friendly_name", d.entity_id))
            for d in self.hass.states.async_all("device_tracker")
        ]

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_KMA_API_KEY): str,
                vol.Required(CONF_AIR_API_KEY): str,
                vol.Required(CONF_LOCATION_TYPE, default=LOCATION_TYPE_ZONE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": LOCATION_TYPE_ZONE, "label": "고정 위치 (Zone)"},
                            {"value": LOCATION_TYPE_MOBILE, "label": "이동 위치 (Mobile App)"}
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Optional(CONF_ZONE_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="zone")
                ),
                vol.Optional(CONF_MOBILE_DEVICE_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="device_tracker")
                ),
            }),
            errors=errors,
        )
