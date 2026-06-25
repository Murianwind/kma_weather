"""
test_pause_zones.py
폴링 중단 존(pause_zones) 기능 TDD 테스트

hass fixture의 StateMachine은 get/async_entity_ids가 read-only라
patch가 불가능하므로, coordinator 테스트는 MagicMock hass를 사용하고
config_flow 테스트는 실제 hass fixture를 사용하되
flow.hass를 MagicMock으로 교체한다.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator


# ─────────────────────────────────────────────────────────────────────────────
# 공통 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def make_zone_state(entity_id, lat, lon, radius=100, friendly_name="우리집"):
    state = MagicMock()
    state.entity_id = entity_id
    state.attributes = {
        "latitude": lat, "longitude": lon,
        "radius": radius, "friendly_name": friendly_name,
    }
    return state


def make_mock_hass(zone_map: dict = None, zone_ids: list = None,
                   config_lat: float = 37.56, config_lon: float = 126.98):
    """
    StateMachine read-only 우회용 MagicMock hass.
    zone_map: {entity_id: state_or_None}
    zone_ids: async_entity_ids("zone") 반환값
    """
    hass = MagicMock()
    hass.config.latitude = config_lat
    hass.config.longitude = config_lon
    hass.config.config_dir = "/tmp/test_kma"

    _zone_map = zone_map or {}
    hass.states.get = MagicMock(side_effect=lambda eid: _zone_map.get(eid))
    hass.states.async_entity_ids = MagicMock(return_value=zone_ids or [])

    hass.data = {}
    hass.async_create_task = MagicMock(return_value=None)
    hass.async_add_executor_job = AsyncMock(return_value=None)
    return hass


def make_coordinator(zone_map=None, location_entity="device_tracker.phone",
                     pause_zones=None, zone_ids=None):
    """MagicMock hass 기반 coordinator 생성"""
    hass = make_mock_hass(zone_map=zone_map, zone_ids=zone_ids)
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


def make_options_flow(location_entity, options=None, zone_map=None, zone_ids=None):
    """config_flow 옵션 플로우 + MagicMock hass 생성"""
    from custom_components.kma_weather.config_flow import KMAWeatherOptionsFlowHandler
    entry = MagicMock()
    entry.data = {"api_key": "key", "location_entity": location_entity}
    entry.options = options or {}
    flow = KMAWeatherOptionsFlowHandler(entry)
    flow.hass = make_mock_hass(zone_map=zone_map, zone_ids=zone_ids)
    return flow


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
    async def test_pause_zones_visibility(self, location_entity, should_show):
        """
        [Given] location_entity 종류별 설정
        [When]  옵션 플로우 초기화
        [Then]  device_tracker/person → pause_zones 노출, zone/없음 → 숨김
        """
        zone_state = make_zone_state("zone.home", 37.56, 126.98)
        flow = make_options_flow(
            location_entity or "",
            zone_map={"zone.home": zone_state, "zone.office": zone_state},
            zone_ids=["zone.home", "zone.office"],
        )
        result = await flow.async_step_init(user_input=None)
        schema_keys = [str(k) for k in result["data_schema"].schema.keys()]
        if should_show:
            assert "pause_zones" in schema_keys, f"{location_entity} → pause_zones 노출 기대"
        else:
            assert "pause_zones" not in schema_keys, f"{location_entity} → pause_zones 숨김 기대"

    @pytest.mark.asyncio
    async def test_zone_list_shows_all_zones(self):
        """
        [Given] HA에 zone.home, zone.office 2개 존재
        [When]  device_tracker로 옵션 플로우 열기
        [Then]  pause_zones 스키마에 포함됨
        """
        def mock_get(eid):
            names = {"zone.home": "우리집", "zone.office": "사무실"}
            return make_zone_state(eid, 37.56, 126.98, friendly_name=names.get(eid, eid))

        flow = make_options_flow(
            "device_tracker.phone",
            zone_map={"zone.home": mock_get("zone.home"), "zone.office": mock_get("zone.office")},
            zone_ids=["zone.home", "zone.office"],
        )
        result = await flow.async_step_init(user_input=None)
        schema_keys = [str(k) for k in result["data_schema"].schema.keys()]
        assert "pause_zones" in schema_keys


# ─────────────────────────────────────────────────────────────────────────────
# B. coordinator — 존 안/밖 폴링 동작
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordinatorPauseZones:

    @pytest.mark.asyncio
    async def test_inside_zone_returns_cache_without_api_call(self):
        """
        [Given] pause_zones=[zone.home], 현재 위치가 zone.home 반경 안
        [When]  _async_update_data 호출
        [Then]  API 호출 없이 캐시 반환
        """
        zone_state = make_zone_state("zone.home", 37.56, 126.98, radius=100)
        coord = make_coordinator(
            zone_map={"zone.home": zone_state},
            pause_zones=["zone.home"],
        )
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        result = await coord._async_update_data()
        coord.api.fetch_data.assert_not_called()
        assert result["weather"]["TMP"] == 20

    @pytest.mark.asyncio
    async def test_outside_zone_calls_api(self):
        """
        [Given] pause_zones=[zone.home], 현재 위치가 약 5km 밖
        [When]  _async_update_data 호출
        [Then]  API 호출 후 새 데이터 반환
        """
        zone_state = make_zone_state("zone.home", 37.56, 126.98, radius=100)
        coord = make_coordinator(
            zone_map={"zone.home": zone_state},
            pause_zones=["zone.home"],
        )
        coord._resolve_location = MagicMock(return_value=(37.61, 126.98))
        result = await coord._async_update_data()
        coord.api.fetch_data.assert_called_once()
        assert result["weather"]["TMP"] == 25

    @pytest.mark.asyncio
    async def test_inside_one_of_multiple_zones_stops_polling(self):
        """
        [Given] pause_zones=[zone.home, zone.office], 현재 위치가 zone.office 안
        [When]  _async_update_data 호출
        [Then]  API 호출 없이 캐시 반환
        """
        zone_map = {
            "zone.home":   make_zone_state("zone.home",   37.56, 126.98, radius=100),
            "zone.office": make_zone_state("zone.office", 37.50, 127.02, radius=100),
        }
        coord = make_coordinator(
            zone_map=zone_map,
            pause_zones=["zone.home", "zone.office"],
        )
        coord._resolve_location = MagicMock(return_value=(37.50, 127.02))
        result = await coord._async_update_data()
        coord.api.fetch_data.assert_not_called()
        assert result["weather"]["TMP"] == 20

    @pytest.mark.asyncio
    async def test_outside_all_zones_calls_api(self):
        """
        [Given] pause_zones=[zone.home, zone.office], 두 존 모두 밖
        [When]  _async_update_data 호출
        [Then]  API 호출
        """
        zone_map = {
            "zone.home":   make_zone_state("zone.home",   37.56, 126.98, radius=100),
            "zone.office": make_zone_state("zone.office", 37.50, 127.02, radius=100),
        }
        coord = make_coordinator(
            zone_map=zone_map,
            pause_zones=["zone.home", "zone.office"],
        )
        coord._resolve_location = MagicMock(return_value=(37.40, 127.10))
        result = await coord._async_update_data()
        coord.api.fetch_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_boundary_exactly_on_radius_stops_polling(self):
        """
        [Given] 현재 위치가 반경 안쪽(약 90m)
        [When]  _async_update_data 호출
        [Then]  캐시 반환
        """
        zone_state = make_zone_state("zone.home", 37.56, 126.98, radius=100)
        coord = make_coordinator(
            zone_map={"zone.home": zone_state},
            pause_zones=["zone.home"],
        )
        coord._resolve_location = MagicMock(return_value=(37.56081, 126.98))
        result = await coord._async_update_data()
        coord.api.fetch_data.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# C. 존 생명주기
# ─────────────────────────────────────────────────────────────────────────────

class TestZoneLifecycle:

    @pytest.mark.asyncio
    async def test_deleted_zone_in_pause_zones_is_skipped(self):
        """
        [Given] pause_zones에 zone.office 설정됐는데 삭제됨(None 반환)
        [When]  _async_update_data 호출
        [Then]  스킵 후 정상 폴링
        """
        coord = make_coordinator(
            zone_map={"zone.office": None},
            pause_zones=["zone.office"],
        )
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        result = await coord._async_update_data()
        coord.api.fetch_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_zone_added_to_ha_appears_in_options(self):
        """
        [Given] HA에 zone.gym이 새로 추가됨
        [When]  옵션 플로우 재진입
        [Then]  pause_zones 스키마에 있음
        """
        zone_state = make_zone_state("zone.home", 37.56, 126.98)
        flow = make_options_flow(
            "device_tracker.phone",
            options={"pause_zones": ["zone.home"]},
            zone_map={"zone.home": zone_state, "zone.gym": zone_state},
            zone_ids=["zone.home", "zone.gym"],
        )
        result = await flow.async_step_init(user_input=None)
        schema_keys = [str(k) for k in result["data_schema"].schema.keys()]
        assert "pause_zones" in schema_keys

    @pytest.mark.asyncio
    async def test_pause_zones_with_missing_attributes_skipped(self):
        """
        [Given] 존 엔티티에 latitude/longitude 속성 없음
        [When]  _async_update_data 호출
        [Then]  AttributeError 없이 스킵 후 정상 폴링
        """
        broken_zone = MagicMock()
        broken_zone.attributes = {}
        coord = make_coordinator(
            zone_map={"zone.broken": broken_zone},
            pause_zones=["zone.broken"],
        )
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        result = await coord._async_update_data()
        coord.api.fetch_data.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# D. 기존 기능 영향 없음
# ─────────────────────────────────────────────────────────────────────────────

class TestExistingFunctionalityUnchanged:

    @pytest.mark.asyncio
    async def test_no_pause_zones_option_polls_normally(self):
        """
        [Given] pause_zones=[] (기존 사용자)
        [When]  _async_update_data 호출
        [Then]  기존과 동일하게 API 호출
        """
        coord = make_coordinator(pause_zones=[])
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        result = await coord._async_update_data()
        coord.api.fetch_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_zone_location_entity_ignores_pause_zones(self):
        """
        [Given] location_entity=zone.home + pause_zones=["zone.home"]
        [When]  _async_update_data 호출
        [Then]  zone.* 기기는 pause_zones 무시 → 정상 폴링
        """
        zone_state = make_zone_state("zone.home", 37.56, 126.98, radius=100)
        coord = make_coordinator(
            zone_map={"zone.home": zone_state},
            location_entity="zone.home",
            pause_zones=["zone.home"],
        )
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        result = await coord._async_update_data()
        coord.api.fetch_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_location_entity_ignores_pause_zones(self):
        """
        [Given] location_entity="" + pause_zones=["zone.home"]
        [When]  _async_update_data 호출
        [Then]  pause_zones 무시 → 정상 폴링
        """
        zone_state = make_zone_state("zone.home", 37.56, 126.98, radius=100)
        coord = make_coordinator(
            zone_map={"zone.home": zone_state},
            location_entity="",
            pause_zones=["zone.home"],
        )
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        result = await coord._async_update_data()
        coord.api.fetch_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_failure_still_returns_cache_regardless_of_zones(self):
        """
        [Given] 존 밖 + API 실패
        [When]  _async_update_data 호출
        [Then]  캐시 반환
        """
        zone_state = make_zone_state("zone.home", 37.56, 126.98, radius=100)
        coord = make_coordinator(
            zone_map={"zone.home": zone_state},
            pause_zones=["zone.home"],
        )
        coord._resolve_location = MagicMock(return_value=(37.61, 126.98))
        coord.api.fetch_data = AsyncMock(return_value=None)
        result = await coord._async_update_data()
        assert result["weather"]["TMP"] == 20


# ─────────────────────────────────────────────────────────────────────────────
# E. 기존 사용자 마이그레이션
# ─────────────────────────────────────────────────────────────────────────────

class TestExistingUserMigration:

    @pytest.mark.asyncio
    async def test_existing_user_opens_options_without_pause_zones_key(self):
        """
        [Given] entry.options에 pause_zones 키 자체가 없는 기존 사용자
        [When]  옵션 플로우 열기
        [Then]  오류 없이 form 표시
        """
        zone_state = make_zone_state("zone.home", 37.56, 126.98)
        flow = make_options_flow(
            "device_tracker.phone",
            options={
                "location_entity": "device_tracker.phone",
                "expire_date": "2026-12-31",
                "apply_date": "2025-01-01",
                # pause_zones 키 없음
            },
            zone_map={"zone.home": zone_state},
            zone_ids=["zone.home"],
        )
        result = await flow.async_step_init(user_input=None)
        assert result["type"] == "form"
        assert result["errors"] == {}

    @pytest.mark.asyncio
    async def test_existing_user_submits_without_selecting_pause_zones(self):
        """
        [Given] 기존 사용자가 pause_zones 선택 없이 제출
        [When]  user_input에 pause_zones=[]
        [Then]  CREATE_ENTRY + 기존 옵션값 유지
        """
        from homeassistant.data_entry_flow import FlowResultType
        zone_state = make_zone_state("zone.home", 37.56, 126.98)
        flow = make_options_flow(
            "device_tracker.phone",
            options={
                "location_entity": "device_tracker.phone",
                "expire_date": "2026-12-31",
                "apply_date": "2025-01-01",
            },
            zone_map={"zone.home": zone_state},
            zone_ids=["zone.home"],
        )
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
    async def test_existing_user_adds_pause_zone_preserves_other_options(self):
        """
        [Given] 기존 사용자가 pause_zones에 zone.home 추가 후 제출
        [When]  user_input에 pause_zones=["zone.home"]
        [Then]  CREATE_ENTRY + pause_zones 저장 + 기존 옵션 유지
        """
        from homeassistant.data_entry_flow import FlowResultType
        zone_state = make_zone_state("zone.home", 37.56, 126.98)
        flow = make_options_flow(
            "device_tracker.phone",
            options={
                "location_entity": "device_tracker.phone",
                "expire_date": "2026-12-31",
                "apply_date": "2025-01-01",
            },
            zone_map={"zone.home": zone_state},
            zone_ids=["zone.home"],
        )
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
    async def test_existing_user_removes_pause_zone(self):
        """
        [Given] 기존에 pause_zones=["zone.home"]이 저장된 사용자
        [When]  pause_zones=[]로 변경 후 제출
        [Then]  CREATE_ENTRY + pause_zones=[]
        """
        from homeassistant.data_entry_flow import FlowResultType
        zone_state = make_zone_state("zone.home", 37.56, 126.98)
        flow = make_options_flow(
            "device_tracker.phone",
            options={
                "location_entity": "device_tracker.phone",
                "pause_zones": ["zone.home"],
                "expire_date": "2026-12-31",
                "apply_date": "2025-01-01",
            },
            zone_map={"zone.home": zone_state},
            zone_ids=["zone.home"],
        )
        result = await flow.async_step_init(user_input={
            "location_entity": "device_tracker.phone",
            "expire_date": "2026-12-31",
            "apply_date": "2025-01-01",
            "pause_zones": [],
        })
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"]["pause_zones"] == []

    @pytest.mark.asyncio
    async def test_coordinator_no_crash_when_pause_zones_key_missing(self):
        """
        [Given] entry.options에 pause_zones 키 없는 기존 사용자
        [When]  _async_update_data 호출
        [Then]  KeyError 없이 정상 폴링
        """
        hass = make_mock_hass()
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "device_tracker.phone"}
        entry.options = {
            "location_entity": "device_tracker.phone",
            "expire_date": "2026-12-31",
            # pause_zones 키 없음
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
    async def test_location_entity_changed_to_zone_hides_pause_zones(self):
        """
        [Given] location_entity=zone.home + 이전에 저장된 pause_zones 잔존
        [When]  옵션 플로우 열기
        [Then]  pause_zones 숨겨짐
        """
        zone_state = make_zone_state("zone.home", 37.56, 126.98)
        flow = make_options_flow(
            "zone.home",
            options={
                "location_entity": "zone.home",
                "pause_zones": ["zone.home"],
                "expire_date": "2026-12-31",
            },
            zone_map={"zone.home": zone_state},
            zone_ids=["zone.home"],
        )
        result = await flow.async_step_init(user_input=None)
        schema_keys = [str(k) for k in result["data_schema"].schema.keys()]
        assert "pause_zones" not in schema_keys

    @pytest.mark.asyncio
    async def test_coordinator_ignores_pause_zones_when_location_entity_is_zone(self):
        """
        [Given] location_entity=zone.home + pause_zones=["zone.home"] 잔존
        [When]  _async_update_data 호출
        [Then]  pause_zones 무시 → 정상 폴링
        """
        zone_state = make_zone_state("zone.home", 37.56, 126.98, radius=100)
        coord = make_coordinator(
            zone_map={"zone.home": zone_state},
            location_entity="zone.home",
            pause_zones=["zone.home"],
        )
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        result = await coord._async_update_data()
        coord.api.fetch_data.assert_called_once()
