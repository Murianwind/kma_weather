"""
test_pause_zones.py
폴링 중단 존(pause_zones) 기능 TDD 테스트
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator


def make_zone_state(entity_id, lat, lon, radius=100, friendly_name="우리집"):
    state = MagicMock()
    state.entity_id = entity_id
    state.attributes = {
        "latitude": lat, "longitude": lon,
        "radius": radius, "friendly_name": friendly_name,
    }
    return state


def make_coordinator(hass, location_entity, pause_zones=None):
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": location_entity}
    entry.options = {"pause_zones": pause_zones or []}
    entry.entry_id = "pause_test"
    coord = KMAWeatherUpdateCoordinator(hass, entry)
    coord._store_loaded = True
    coord._cached_data = {"weather": {"TMP": 20}, "air": {}}
    coord.api.fetch_data = AsyncMock(return_value={
        "weather": {"TMP": 25}, "air": {}, "pollen": None, "raw_forecast": {}
    })
    return coord


def make_states_getter(zone_map: dict):
    def _get(entity_id):
        return zone_map.get(entity_id)
    return _get


# ─────────────────────────────────────────────────────────────────────────────
# A. config_flow — pause_zones 옵션 노출 조건
# ─────────────────────────────────────────────────────────────────────────────

class TestOptionsFlowPauseZonesVisibility:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("location_entity,should_show", [
        ("device_tracker.phone",  True),
        ("device_tracker.iphone", True),
        ("person.hayeongi",       True),
        ("zone.home",             False),
        ("zone.office",           False),
        ("",                      False),
        (None,                    False),
    ])
    async def test_pause_zones_visibility(self, hass, location_entity, should_show):
        """
        [Given] location_entity 종류별 설정
        [When]  옵션 플로우 초기화
        [Then]  device_tracker/person → pause_zones 노출, zone/없음 → 숨김
        """
        from custom_components.kma_weather.config_flow import KMAWeatherOptionsFlowHandler
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": location_entity or ""}
        entry.options = {}
        flow = KMAWeatherOptionsFlowHandler(entry)
        flow.hass = hass
        zone_state = make_zone_state("zone.home", 37.56, 126.98)
        with patch.object(hass.states, "async_entity_ids", return_value=["zone.home", "zone.office"]), \
             patch.object(hass.states, "get", return_value=zone_state):
            result = await flow.async_step_init(user_input=None)
        schema_keys = [str(k) for k in result["data_schema"].schema.keys()]
        if should_show:
            assert "pause_zones" in schema_keys, f"{location_entity} → pause_zones 노출 기대"
        else:
            assert "pause_zones" not in schema_keys, f"{location_entity} → pause_zones 숨김 기대"

    @pytest.mark.asyncio
    async def test_zone_list_shows_all_zones(self, hass):
        """
        [Given] HA에 zone.home, zone.office 2개 존재
        [When]  device_tracker로 옵션 플로우 열기
        [Then]  pause_zones 스키마에 포함됨
        """
        from custom_components.kma_weather.config_flow import KMAWeatherOptionsFlowHandler
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "device_tracker.phone"}
        entry.options = {}
        flow = KMAWeatherOptionsFlowHandler(entry)
        flow.hass = hass
        def mock_get(entity_id):
            names = {"zone.home": "우리집", "zone.office": "사무실"}
            return make_zone_state(entity_id, 37.56, 126.98, friendly_name=names.get(entity_id, entity_id))
        with patch.object(hass.states, "async_entity_ids", return_value=["zone.home", "zone.office"]), \
             patch.object(hass.states, "get", side_effect=mock_get):
            result = await flow.async_step_init(user_input=None)
        schema_keys = [str(k) for k in result["data_schema"].schema.keys()]
        assert "pause_zones" in schema_keys


# ─────────────────────────────────────────────────────────────────────────────
# B. coordinator — 존 안/밖 폴링 동작
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordinatorPauseZones:

    @pytest.mark.asyncio
    async def test_inside_zone_returns_cache_without_api_call(self, hass):
        """
        [Given] pause_zones=[zone.home], 현재 위치가 zone.home 반경 안
        [When]  _async_update_data 호출
        [Then]  API 호출 없이 캐시 반환
        """
        coord = make_coordinator(hass, "device_tracker.phone", pause_zones=["zone.home"])
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        zone_state = make_zone_state("zone.home", 37.56, 126.98, radius=100)
        with patch.object(hass.states, "get", return_value=zone_state):
            result = await coord._async_update_data()
        coord.api.fetch_data.assert_not_called()
        assert result["weather"]["TMP"] == 20

    @pytest.mark.asyncio
    async def test_outside_zone_calls_api(self, hass):
        """
        [Given] pause_zones=[zone.home], 현재 위치가 약 5km 밖
        [When]  _async_update_data 호출
        [Then]  API 호출 후 새 데이터 반환
        """
        coord = make_coordinator(hass, "device_tracker.phone", pause_zones=["zone.home"])
        coord._resolve_location = MagicMock(return_value=(37.61, 126.98))
        zone_state = make_zone_state("zone.home", 37.56, 126.98, radius=100)
        with patch.object(hass.states, "get", return_value=zone_state):
            result = await coord._async_update_data()
        coord.api.fetch_data.assert_called_once()
        assert result["weather"]["TMP"] == 25

    @pytest.mark.asyncio
    async def test_inside_one_of_multiple_zones_stops_polling(self, hass):
        """
        [Given] pause_zones=[zone.home, zone.office], 현재 위치가 zone.office 안
        [When]  _async_update_data 호출
        [Then]  API 호출 없이 캐시 반환
        """
        coord = make_coordinator(hass, "device_tracker.phone",
                                 pause_zones=["zone.home", "zone.office"])
        coord._resolve_location = MagicMock(return_value=(37.50, 127.02))
        zone_map = {
            "zone.home":   make_zone_state("zone.home",   37.56, 126.98, radius=100),
            "zone.office": make_zone_state("zone.office", 37.50, 127.02, radius=100),
        }
        with patch.object(hass.states, "get", side_effect=make_states_getter(zone_map)):
            result = await coord._async_update_data()
        coord.api.fetch_data.assert_not_called()
        assert result["weather"]["TMP"] == 20

    @pytest.mark.asyncio
    async def test_outside_all_zones_calls_api(self, hass):
        """
        [Given] pause_zones=[zone.home, zone.office], 두 존 모두 밖
        [When]  _async_update_data 호출
        [Then]  API 호출
        """
        coord = make_coordinator(hass, "device_tracker.phone",
                                 pause_zones=["zone.home", "zone.office"])
        coord._resolve_location = MagicMock(return_value=(37.40, 127.10))
        zone_map = {
            "zone.home":   make_zone_state("zone.home",   37.56, 126.98, radius=100),
            "zone.office": make_zone_state("zone.office", 37.50, 127.02, radius=100),
        }
        with patch.object(hass.states, "get", side_effect=make_states_getter(zone_map)):
            result = await coord._async_update_data()
        coord.api.fetch_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_boundary_exactly_on_radius_stops_polling(self, hass):
        """
        [Given] 현재 위치가 반경 안쪽(약 90m)
        [When]  _async_update_data 호출
        [Then]  캐시 반환
        """
        coord = make_coordinator(hass, "device_tracker.phone", pause_zones=["zone.home"])
        coord._resolve_location = MagicMock(return_value=(37.56081, 126.98))
        zone_state = make_zone_state("zone.home", 37.56, 126.98, radius=100)
        with patch.object(hass.states, "get", return_value=zone_state):
            result = await coord._async_update_data()
        coord.api.fetch_data.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# C. 존 생명주기
# ─────────────────────────────────────────────────────────────────────────────

class TestZoneLifecycle:

    @pytest.mark.asyncio
    async def test_deleted_zone_in_pause_zones_is_skipped(self, hass):
        """
        [Given] pause_zones에 zone.office 설정됐는데 삭제됨(None 반환)
        [When]  _async_update_data 호출
        [Then]  스킵 후 정상 폴링
        """
        coord = make_coordinator(hass, "device_tracker.phone", pause_zones=["zone.office"])
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        with patch.object(hass.states, "get", return_value=None):
            result = await coord._async_update_data()
        coord.api.fetch_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_zone_added_to_ha_appears_in_options(self, hass):
        """
        [Given] HA에 zone.gym이 새로 추가됨
        [When]  옵션 플로우 재진입
        [Then]  pause_zones 스키마에 있음
        """
        from custom_components.kma_weather.config_flow import KMAWeatherOptionsFlowHandler
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "device_tracker.phone"}
        entry.options = {"pause_zones": ["zone.home"]}
        flow = KMAWeatherOptionsFlowHandler(entry)
        flow.hass = hass
        zone_state = make_zone_state("zone.home", 37.56, 126.98)
        with patch.object(hass.states, "async_entity_ids", return_value=["zone.home", "zone.gym"]), \
             patch.object(hass.states, "get", return_value=zone_state):
            result = await flow.async_step_init(user_input=None)
        schema_keys = [str(k) for k in result["data_schema"].schema.keys()]
        assert "pause_zones" in schema_keys

    @pytest.mark.asyncio
    async def test_pause_zones_with_missing_attributes_skipped(self, hass):
        """
        [Given] 존 엔티티에 latitude/longitude 속성 없음
        [When]  _async_update_data 호출
        [Then]  AttributeError 없이 스킵 후 정상 폴링
        """
        coord = make_coordinator(hass, "device_tracker.phone", pause_zones=["zone.broken"])
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        broken_zone = MagicMock()
        broken_zone.attributes = {}
        with patch.object(hass.states, "get", return_value=broken_zone):
            result = await coord._async_update_data()
        coord.api.fetch_data.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# D. 기존 기능 영향 없음
# ─────────────────────────────────────────────────────────────────────────────

class TestExistingFunctionalityUnchanged:

    @pytest.mark.asyncio
    async def test_no_pause_zones_option_polls_normally(self, hass):
        """
        [Given] pause_zones=[] (기존 사용자)
        [When]  _async_update_data 호출
        [Then]  기존과 동일하게 API 호출
        """
        coord = make_coordinator(hass, "device_tracker.phone", pause_zones=[])
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        result = await coord._async_update_data()
        coord.api.fetch_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_zone_location_entity_ignores_pause_zones(self, hass):
        """
        [Given] location_entity=zone.home + pause_zones=["zone.home"]
        [When]  _async_update_data 호출
        [Then]  zone.* 기기는 pause_zones 무시 → 정상 폴링
        """
        coord = make_coordinator(hass, "zone.home", pause_zones=["zone.home"])
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        zone_state = make_zone_state("zone.home", 37.56, 126.98, radius=100)
        with patch.object(hass.states, "get", return_value=zone_state):
            result = await coord._async_update_data()
        coord.api.fetch_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_location_entity_ignores_pause_zones(self, hass):
        """
        [Given] location_entity="" + pause_zones=["zone.home"]
        [When]  _async_update_data 호출
        [Then]  pause_zones 무시 → 정상 폴링
        """
        coord = make_coordinator(hass, "", pause_zones=["zone.home"])
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        zone_state = make_zone_state("zone.home", 37.56, 126.98, radius=100)
        with patch.object(hass.states, "get", return_value=zone_state):
            result = await coord._async_update_data()
        coord.api.fetch_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_failure_still_returns_cache_regardless_of_zones(self, hass):
        """
        [Given] 존 밖 + API 실패
        [When]  _async_update_data 호출
        [Then]  캐시 반환
        """
        coord = make_coordinator(hass, "device_tracker.phone", pause_zones=["zone.home"])
        coord._resolve_location = MagicMock(return_value=(37.61, 126.98))
        coord.api.fetch_data = AsyncMock(return_value=None)
        zone_state = make_zone_state("zone.home", 37.56, 126.98, radius=100)
        with patch.object(hass.states, "get", return_value=zone_state):
            result = await coord._async_update_data()
        assert result["weather"]["TMP"] == 20


# ─────────────────────────────────────────────────────────────────────────────
# E. 기존 사용자 마이그레이션
# ─────────────────────────────────────────────────────────────────────────────

class TestExistingUserMigration:

    @pytest.mark.asyncio
    async def test_existing_user_opens_options_without_pause_zones_key(self, hass):
        """
        [Given] entry.options에 pause_zones 키 자체가 없는 기존 사용자
        [When]  옵션 플로우 열기
        [Then]  오류 없이 form 표시
        """
        from custom_components.kma_weather.config_flow import KMAWeatherOptionsFlowHandler
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "device_tracker.phone"}
        entry.options = {
            "location_entity": "device_tracker.phone",
            "expire_date": "2026-12-31",
            "apply_date": "2025-01-01",
        }
        flow = KMAWeatherOptionsFlowHandler(entry)
        flow.hass = hass
        zone_state = make_zone_state("zone.home", 37.56, 126.98)
        with patch.object(hass.states, "async_entity_ids", return_value=["zone.home"]), \
             patch.object(hass.states, "get", return_value=zone_state):
            result = await flow.async_step_init(user_input=None)
        assert result["type"] == "form"
        assert result["errors"] == {}

    @pytest.mark.asyncio
    async def test_existing_user_submits_without_selecting_pause_zones(self, hass):
        """
        [Given] 기존 사용자가 pause_zones 선택 없이 제출
        [When]  user_input에 pause_zones=[]
        [Then]  CREATE_ENTRY + 기존 옵션값 유지
        """
        from custom_components.kma_weather.config_flow import KMAWeatherOptionsFlowHandler
        from homeassistant.data_entry_flow import FlowResultType
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "device_tracker.phone"}
        entry.options = {
            "location_entity": "device_tracker.phone",
            "expire_date": "2026-12-31",
            "apply_date": "2025-01-01",
        }
        flow = KMAWeatherOptionsFlowHandler(entry)
        flow.hass = hass
        zone_state = make_zone_state("zone.home", 37.56, 126.98)
        with patch.object(hass.states, "async_entity_ids", return_value=["zone.home"]), \
             patch.object(hass.states, "get", return_value=zone_state):
            result = await flow.async_step_init(user_input={
                "location_entity": "device_tracker.phone",
                "expire_date": "2026-12-31",
                "apply_date": "2025-01-01",
                "pause_zones": [],
            })
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"]["expire_date"] == "2026-12-31"
        assert result["data"]["apply_date"] == "2025-01-01"
        assert result["data"].get("pause_zones", []) == []

    @pytest.mark.asyncio
    async def test_existing_user_adds_pause_zone_preserves_other_options(self, hass):
        """
        [Given] 기존 사용자가 pause_zones에 zone.home 추가 후 제출
        [When]  user_input에 pause_zones=["zone.home"]
        [Then]  CREATE_ENTRY + pause_zones 저장 + 기존 옵션 유지
        """
        from custom_components.kma_weather.config_flow import KMAWeatherOptionsFlowHandler
        from homeassistant.data_entry_flow import FlowResultType
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "device_tracker.phone"}
        entry.options = {
            "location_entity": "device_tracker.phone",
            "expire_date": "2026-12-31",
            "apply_date": "2025-01-01",
        }
        flow = KMAWeatherOptionsFlowHandler(entry)
        flow.hass = hass
        zone_state = make_zone_state("zone.home", 37.56, 126.98)
        with patch.object(hass.states, "async_entity_ids", return_value=["zone.home"]), \
             patch.object(hass.states, "get", return_value=zone_state):
            result = await flow.async_step_init(user_input={
                "location_entity": "device_tracker.phone",
                "expire_date": "2026-12-31",
                "apply_date": "2025-01-01",
                "pause_zones": ["zone.home"],
            })
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"]["pause_zones"] == ["zone.home"]
        assert result["data"]["expire_date"] == "2026-12-31"
        assert result["data"]["apply_date"] == "2025-01-01"

    @pytest.mark.asyncio
    async def test_existing_user_removes_pause_zone(self, hass):
        """
        [Given] 기존에 pause_zones=["zone.home"]이 저장된 사용자
        [When]  pause_zones=[]로 변경 후 제출
        [Then]  CREATE_ENTRY + pause_zones=[]
        """
        from custom_components.kma_weather.config_flow import KMAWeatherOptionsFlowHandler
        from homeassistant.data_entry_flow import FlowResultType
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "device_tracker.phone"}
        entry.options = {
            "location_entity": "device_tracker.phone",
            "pause_zones": ["zone.home"],
            "expire_date": "2026-12-31",
            "apply_date": "2025-01-01",
        }
        flow = KMAWeatherOptionsFlowHandler(entry)
        flow.hass = hass
        zone_state = make_zone_state("zone.home", 37.56, 126.98)
        with patch.object(hass.states, "async_entity_ids", return_value=["zone.home"]), \
             patch.object(hass.states, "get", return_value=zone_state):
            result = await flow.async_step_init(user_input={
                "location_entity": "device_tracker.phone",
                "expire_date": "2026-12-31",
                "apply_date": "2025-01-01",
                "pause_zones": [],
            })
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"]["pause_zones"] == []

    @pytest.mark.asyncio
    async def test_coordinator_no_crash_when_pause_zones_key_missing(self, hass):
        """
        [Given] entry.options에 pause_zones 키 없는 기존 사용자
        [When]  _async_update_data 호출
        [Then]  KeyError 없이 정상 폴링
        """
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "device_tracker.phone"}
        entry.options = {
            "location_entity": "device_tracker.phone",
            "expire_date": "2026-12-31",
        }
        entry.entry_id = "legacy_test"
        coord = KMAWeatherUpdateCoordinator(hass, entry)
        coord._store_loaded = True
        coord._cached_data = {"weather": {"TMP": 20}, "air": {}}
        coord.api.fetch_data = AsyncMock(return_value={
            "weather": {"TMP": 25}, "air": {}, "pollen": None, "raw_forecast": {}
        })
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        result = await coord._async_update_data()
        coord.api.fetch_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_location_entity_changed_to_zone_hides_pause_zones(self, hass):
        """
        [Given] location_entity=zone.home + 이전에 저장된 pause_zones=["zone.home"] 잔존
        [When]  옵션 플로우 열기
        [Then]  pause_zones 숨겨짐
        """
        from custom_components.kma_weather.config_flow import KMAWeatherOptionsFlowHandler
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "zone.home"}
        entry.options = {
            "location_entity": "zone.home",
            "pause_zones": ["zone.home"],
            "expire_date": "2026-12-31",
        }
        flow = KMAWeatherOptionsFlowHandler(entry)
        flow.hass = hass
        zone_state = make_zone_state("zone.home", 37.56, 126.98)
        with patch.object(hass.states, "async_entity_ids", return_value=["zone.home"]), \
             patch.object(hass.states, "get", return_value=zone_state):
            result = await flow.async_step_init(user_input=None)
        schema_keys = [str(k) for k in result["data_schema"].schema.keys()]
        assert "pause_zones" not in schema_keys

    @pytest.mark.asyncio
    async def test_coordinator_ignores_pause_zones_when_location_entity_is_zone(self, hass):
        """
        [Given] location_entity=zone.home + pause_zones=["zone.home"] 잔존
        [When]  _async_update_data 호출
        [Then]  pause_zones 무시 → 정상 폴링
        """
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "zone.home"}
        entry.options = {
            "location_entity": "zone.home",
            "pause_zones": ["zone.home"],
        }
        entry.entry_id = "zone_entity_test"
        coord = KMAWeatherUpdateCoordinator(hass, entry)
        coord._store_loaded = True
        coord._cached_data = {"weather": {"TMP": 20}, "air": {}}
        coord.api.fetch_data = AsyncMock(return_value={
            "weather": {"TMP": 25}, "air": {}, "pollen": None, "raw_forecast": {}
        })
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        zone_state = make_zone_state("zone.home", 37.56, 126.98, radius=100)
        with patch.object(hass.states, "get", return_value=zone_state):
            result = await coord._async_update_data()
        coord.api.fetch_data.assert_called_once()
