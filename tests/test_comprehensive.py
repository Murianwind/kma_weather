"""
tests/test_init_and_config.py
기존 테스트와 중복되지 않는 Config Flow 및 컴포넌트 생명주기 테스트만 남겼습니다.
"""
import pytest
from homeassistant import data_entry_flow
from homeassistant.config_entries import SOURCE_USER
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unittest.mock import patch

from custom_components.kma_weather.const import (
    DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY, CONF_PREFIX
)

# =====================================================================
# 1. UI 설정 흐름 (Config Flow) - 커버리지 58.2% 보완
# =====================================================================

@pytest.mark.asyncio
async def test_config_flow_valid_setup(hass):
    """정상적인 API 키와 입력값으로 설정 성공"""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    
    # 폼이 제대로 열렸는지 확인
    assert result["type"] == data_entry_flow.FlowResultType.FORM

    # _validate_api_key 가 성공(None 반환)하는 것으로 모킹
    with patch("custom_components.kma_weather.async_setup_entry", return_value=True), \
         patch("custom_components.kma_weather.config_flow._validate_api_key", return_value=None):
        
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_API_KEY: "valid_api_key_test",
                CONF_PREFIX: "my_weather",
            },
        )
    
    # 정상적으로 Entry가 생성되었는지 확인
    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result2["title"] == "기상청 날씨: 우리집" # 기본 fallback 이름
    assert result2["data"][CONF_PREFIX] == "my_weather"


@pytest.mark.asyncio
async def test_config_flow_invalid_api_key(hass):
    """유효하지 않은 API 키 입력 시 에러 폼 반환"""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    # _validate_api_key 가 실패("invalid_api_key" 반환)하는 것으로 모킹
    with patch("custom_components.kma_weather.config_flow._validate_api_key", return_value="invalid_api_key"):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_API_KEY: "wrong_api_key",
                CONF_PREFIX: "my_weather",
            },
        )
        
    # 설정이 완료되지 않고 폼이 다시 표시되어야 하며, API 키 에러가 나야 함
    assert result2["type"] == data_entry_flow.FlowResultType.FORM
    assert result2["errors"] == {CONF_API_KEY: "invalid_api_key"}


# =====================================================================
# 2. 생명주기 (Init / Unload / Reload) - 커버리지 64.0% 보완
# =====================================================================

@pytest.mark.asyncio
async def test_unload_and_reload_entry(hass):
    """컴포넌트 로드 -> 재시작(Reload) -> 언로드(Unload) 라이프사이클 통합 검증"""
    config_entry = MockConfigEntry(
        domain=DOMAIN, 
        data={CONF_API_KEY: "test_key", CONF_PREFIX: "test_prefix"}
    )
    config_entry.add_to_hass(hass)

    # Coordinator가 데이터를 가져오는 로직만 껍데기로 모킹
    with patch("custom_components.kma_weather.coordinator.KMAWeatherUpdateCoordinator._async_update_data", return_value={}):
        
        # 1. 초기 셋업 (Setup)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        assert config_entry.entry_id in hass.data[DOMAIN]

        # 2. 리로드 (Reload) 테스트
        reload_ok = await hass.config_entries.async_reload(config_entry.entry_id)
        await hass.async_block_till_done()
        assert reload_ok is True
        # 리로드 후에도 정상적으로 데이터 공간에 존재해야 함
        assert config_entry.entry_id in hass.data[DOMAIN]

        # 3. 언로드 (Unload) 테스트
        unload_ok = await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()
        assert unload_ok is True
        # 언로드 후에는 hass.data 에서 안전하게 지워져야 함
        assert config_entry.entry_id not in hass.data.get(DOMAIN, {})
