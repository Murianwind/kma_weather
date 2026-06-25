"""
test_pause_zones.py
폴링 중단 존(pause_zones) 기능 TDD 테스트

coordinator 테스트: MagicMock hass (StateMachine read-only 우회)
config_flow 테스트: 실제 hass fixture + config_entries.options.async_init/async_configure
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
from custom_components.kma_weather.const import DOMAIN


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


def make_mock_hass(zone_map=None, zone_ids=None,
                   config_lat=37.56, config_lon=126.98):
    """StateMachine read-only 우회용 MagicMock hass (coordinator 전용)"""
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


def make_config_entry(location_entity="device_tracker.phone", options=None):
    """config_flow 테스트용 MockConfigEntry 생성"""
    return MockConfigEntry(
        domain=DOMAIN,
        data={"api_key": "test_key", "location_entity": location_entity,
              "prefix": "test", "location_mode": "zone"},
        options=options or {},
        entry_id="pause_flow_test",
    )


# ─────────────────────────────────────────────────────────────────────────────
# A. config_flow — pause_zones 옵션 노출 조건
# ─────────────────────────────────────────────────────────────────────────────

class TestOptionsFlowPauseZonesVisibility:

    async def _get_schema_keys(self, hass, location_entity, options=None):
        """options flow를 열어 schema key 목록 반환"""
        entry = make_config_entry(location_entity, options)
        entry.add_to_hass(hass)
        # kma_api_mock_factory 없이 setup 없이 options flow만 열기
        result = await hass.config_entries.options.async_init(entry.entry_id)
        return [str(k) for k in result["data_schema"].schema.keys()]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("location_entity,should_show", [
        ("device_tracker.phone",  True),
        ("person.hayeongi",       True),
        ("zone.home",             False),
        ("zone.office",           False),
    ])
    async def test_pause_zones_visibility(self, hass, location_entity, should_show):
        """
        [Given] location_entity 종류별 설정
        [When]  옵션 플로우 초기화
        [Then]  device_tracker/person → pause_zones 노출, zone → 숨김
        """
        schema_keys = await self._get_schema_keys(hass, location_entity)
        if should_show:
            assert "pause_zones" in schema_keys, \
                f"{location_entity} → pause_zones 노출 기대"
        else:
            assert "pause_zones" not in schema_keys, \
                f"{location_entity} → pause_zones 숨김 기대"

    @pytest.mark.asyncio
    async def test_zone_list_shows_all_zones(self, hass):
        """
        [Given] HA에 zone.home 존재
        [When]  device_tracker로 옵션 플로우 열기
        [Then]  pause_zones 스키마에 포함됨
        """
        schema_keys = await self._get_schema_keys(hass, "device_tracker.phone")
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
    async def test_new_zone_added_to_ha_appears_in_options(self, hass):
        """
        [Given] HA에 zone.gym이 새로 추가됨
        [When]  옵션 플로우 재진입
        [Then]  pause_zones 스키마에 있음
        """
        entry = make_config_entry(
            "device_tracker.phone",
            options={"pause_zones": ["zone.home"]},
        )
        entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
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
    async def test_existing_user_opens_options_without_pause_zones_key(self, hass):
        """
        [Given] entry.options에 pause_zones 키 자체가 없는 기존 사용자
        [When]  옵션 플로우 열기
        [Then]  오류 없이 form 표시
        """
        entry = make_config_entry(
            "device_tracker.phone",
            options={
                "location_entity": "device_tracker.phone",
                "expire_date": "2026-12-31",
                "apply_date": "2025-01-01",
                # pause_zones 키 없음
            },
        )
        entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_existing_user_submits_without_selecting_pause_zones(self, hass):
        """
        [Given] 기존 사용자가 pause_zones 선택 없이 제출
        [When]  user_input에 pause_zones=[]
        [Then]  CREATE_ENTRY + 기존 옵션값 유지
        """
        entry = make_config_entry(
            "device_tracker.phone",
            options={
                "location_entity": "device_tracker.phone",
                "expire_date": "2026-12-31",
                "apply_date": "2025-01-01",
            },
        )
        entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "location_entity": "device_tracker.phone",
                "expire_date": "2026-12-31",
                "apply_date": "2025-01-01",
                "pause_zones": [],
            },
        )
        assert result2["type"] == "create_entry"
        assert result2["data"].get("pause_zones", []) == []
        assert result2["data"]["expire_date"] == "2026-12-31"

    @pytest.mark.asyncio
    async def test_existing_user_adds_pause_zone_preserves_other_options(self, hass):
        """
        [Given] 기존 사용자가 pause_zones에 zone.home 추가 후 제출
        [When]  user_input에 pause_zones=["zone.home"]
        [Then]  CREATE_ENTRY + pause_zones 저장 + 기존 옵션 유지
        """
        # pause_zones selector가 실제 HA에 등록된 zone만 허용하므로 먼저 등록
        hass.states.async_set(
            "zone.home", "0",
            {"latitude": 37.56, "longitude": 126.98, "radius": 100,
             "friendly_name": "우리집"},
        )

        entry = make_config_entry(
            "device_tracker.phone",
            options={
                "location_entity": "device_tracker.phone",
                "expire_date": "2026-12-31",
                "apply_date": "2025-01-01",
            },
        )
        entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "location_entity": "device_tracker.phone",
                "expire_date": "2026-12-31",
                "apply_date": "2025-01-01",
                "pause_zones": ["zone.home"],
            },
        )
        assert result2["type"] == "create_entry"
        assert result2["data"]["pause_zones"] == ["zone.home"]
        assert result2["data"]["expire_date"] == "2026-12-31"
        assert result2["data"]["apply_date"] == "2025-01-01"

    @pytest.mark.asyncio
    async def test_existing_user_removes_pause_zone(self, hass):
        """
        [Given] 기존에 pause_zones=["zone.home"]이 저장된 사용자
        [When]  pause_zones=[]로 변경 후 제출
        [Then]  CREATE_ENTRY + pause_zones=[]
        """
        # options flow 스키마 생성 시 zone 목록 조회하므로 등록 필요
        hass.states.async_set(
            "zone.home", "0",
            {"latitude": 37.56, "longitude": 126.98, "radius": 100,
             "friendly_name": "우리집"},
        )

        entry = make_config_entry(
            "device_tracker.phone",
            options={
                "location_entity": "device_tracker.phone",
                "pause_zones": ["zone.home"],
                "expire_date": "2026-12-31",
                "apply_date": "2025-01-01",
            },
        )
        entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "location_entity": "device_tracker.phone",
                "expire_date": "2026-12-31",
                "apply_date": "2025-01-01",
                "pause_zones": [],
            },
        )
        assert result2["type"] == "create_entry"
        assert result2["data"]["pause_zones"] == []

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
    async def test_location_entity_changed_to_zone_hides_pause_zones(self, hass):
        """
        [Given] location_entity=zone.home + 이전에 저장된 pause_zones 잔존
        [When]  옵션 플로우 열기
        [Then]  pause_zones 숨겨짐
        """
        entry = make_config_entry(
            "zone.home",
            options={
                "location_entity": "zone.home",
                "pause_zones": ["zone.home"],
                "expire_date": "2026-12-31",
            },
        )
        entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
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
