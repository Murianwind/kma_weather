"""
tests/test_init_and_config.py
기존 테스트와 중복되지 않는 Config Flow 및 컴포넌트 생명주기 테스트만 남겼습니다.
"""
import pytest
from homeassistant import data_entry_flow
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_NAME
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unittest.mock import patch

from custom_components.kma_weather.const import DOMAIN

# =====================================================================
# 1. UI 설정 흐름 (Config Flow) - 커버리지 58.2% 보완
# =====================================================================

@pytest.mark.asyncio
async def test_config_flow_valid_setup(hass):
    """정상적인 좌표 입력 시 설정 성공"""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    
    with patch("custom_components.kma_weather.async_setup_entry", return_value=True), \
         patch("custom_components.kma_weather.config_flow.KMAWeatherAPI._fetch", return_value={"response": "ok"}):
        
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_LATITUDE: 37.5665, CONF_LONGITUDE: 126.9780, "api_key": "test_key"},
        )
    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_config_flow_invalid_location(hass):
    """잘못된 좌표 입력 시 에러 폼 반환"""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    with patch("custom_components.kma_weather.config_flow.KMAWeatherAPI._fetch", side_effect=Exception):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_LATITUDE: 0.0, CONF_LONGITUDE: 0.0, "api_key": "test_key"},
        )
    assert result2["type"] == data_entry_flow.FlowResultType.FORM
    assert result2["errors"] is not None


# =====================================================================
# 2. 생명주기 (Init / Unload / Reload) - 커버리지 64.0% 보완
# =====================================================================

@pytest.mark.asyncio
async def test_unload_and_reload_entry(hass):
    """컴포넌트 로드 -> 재시작(Reload) -> 언로드(Unload) 라이프사이클 통합 검증"""
    config_entry = MockConfigEntry(domain=DOMAIN, data={CONF_LATITUDE: 37.5, CONF_LONGITUDE: 126.9, "api_key": "test"})
    config_entry.add_to_hass(hass)

    with patch("custom_components.kma_weather.coordinator.KMAWeatherUpdateCoordinator._async_update_data", return_value={}):
        # 1. 초기 셋업
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        assert config_entry.entry_id in hass.data[DOMAIN]

        # 2. 리로드(Reload) 테스트
        reload_ok = await hass.config_entries.async_reload(config_entry.entry_id)
        await hass.async_block_till_done()
        assert reload_ok is True

        # 3. 언로드(Unload) 테스트
        unload_ok = await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()
        assert unload_ok is True
        assert config_entry.entry_id not in hass.data.get(DOMAIN, {})
