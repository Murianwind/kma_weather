import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv
from .const import (
    DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY, CONF_PREFIX, 
    CONF_EXPIRE_DATE, CONF_APPLY_DATE
)

class KMAWeatherConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """KMA Weather 설정 흐름 (모든 필드 복구 완료)."""
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_API_KEY]}_{user_input[CONF_PREFIX]}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=user_input[CONF_PREFIX], data=user_input)

        # 1 & 2. 안내 문구 및 신청일/만료일 입력 기능 복구
        schema = vol.Schema({
            vol.Required(CONF_API_KEY): str,
            vol.Required(CONF_PREFIX, default="kma"): str,
            vol.Optional(CONF_LOCATION_ENTITY, default="zone.home"): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["zone", "device_tracker", "person"])
            ),
            vol.Optional(CONF_APPLY_DATE, default="2025-01-01"): cv.string,
            vol.Optional(CONF_EXPIRE_DATE, default="2026-12-31"): cv.string,
        })

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return KMAWeatherOptionsFlowHandler(config_entry)


class KMAWeatherOptionsFlowHandler(config_entries.OptionsFlow):
    """3. 기기 수정에서 날짜 변경 기능 복구."""

    def __init__(self, config_entry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options_schema = vol.Schema({
            vol.Optional(
                CONF_LOCATION_ENTITY, 
                default=self._config_entry.options.get(
                    CONF_LOCATION_ENTITY, 
                    self._config_entry.data.get(CONF_LOCATION_ENTITY, "zone.home")
                )
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["zone", "device_tracker", "person"])
            ),
            vol.Optional(
                CONF_EXPIRE_DATE, 
                default=self._config_entry.options.get(
                    CONF_EXPIRE_DATE, 
                    self._config_entry.data.get(CONF_EXPIRE_DATE, "2026-12-31")
                )
            ): cv.string,
        })

        return self.async_show_form(step_id="init", data_schema=options_schema)
