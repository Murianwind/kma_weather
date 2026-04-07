import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from .const import (
    DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY, CONF_PREFIX, 
    CONF_EXPIRE_DATE
)

class KMAWeatherConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """KMA Weather 통합 구성요소의 설정 흐름을 관리합니다."""
    VERSION = 1

    async def async_step_user(self, user_input=None):
        """사용자가 UI에서 통합 구성요소를 추가할 때 호출되는 첫 번째 단계입니다."""
        errors = {}
        
        if user_input is not None:
            # API 키를 고유 ID로 설정하여 중복 설치를 방지합니다.
            await self.async_set_unique_id(user_input[CONF_API_KEY])
            self._abort_if_unique_id_configured()
            
            return self.async_create_entry(
                title=user_input[CONF_PREFIX], 
                data=user_input
            )

        # 설정 화면에서 사용자에게 보여줄 최소한의 필수 항목들
        # reg_id_temp/land는 coordinator에서 자동 계산하므로 여기서 제거되었습니다.
        schema = vol.Schema({
            vol.Required(CONF_API_KEY): str,
            vol.Required(CONF_PREFIX, default="기상청"): str,
            vol.Optional(CONF_LOCATION_ENTITY, default="zone.home"): str,
            vol.Optional(CONF_EXPIRE_DATE, default="2026-12-31"): cv.string,
        })

        return self.async_show_form(
            step_id="user", 
            data_schema=schema, 
            errors=errors
        )

    async def async_step_import(self, import_config):
        """YAML 설정(Legacy)으로부터 데이터를 가져올 때 사용됩니다."""
        return await self.async_step_user(import_config)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """설치 후 사용자가 설정을 변경할 수 있도록 옵션 흐름 핸들러를 반환합니다."""
        return KMAWeatherOptionsFlowHandler(config_entry)


class KMAWeatherOptionsFlowHandler(config_entries.OptionsFlow):
    """통합 구성요소 관리 화면에서 '설정'을 눌렀을 때의 동작을 정의합니다."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """옵션 설정 화면을 구성하고 데이터를 저장합니다."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # 현재 저장된 값을 기본값으로 불러와서 수정할 수 있게 합니다.
        options_schema = vol.Schema({
            vol.Optional(
                CONF_LOCATION_ENTITY, 
                default=self.config_entry.options.get(
                    CONF_LOCATION_ENTITY, 
                    self.config_entry.data.get(CONF_LOCATION_ENTITY, "zone.home")
                )
            ): str,
            vol.Optional(
                CONF_EXPIRE_DATE, 
                default=self.config_entry.options.get(
                    CONF_EXPIRE_DATE, 
                    self.config_entry.data.get(CONF_EXPIRE_DATE, "2026-12-31")
                )
            ): cv.string,
        })

        return self.async_show_form(
            step_id="init", 
            data_schema=options_schema
        )
