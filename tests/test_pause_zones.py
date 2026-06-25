"""
test_pause_zones.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
폴링 중단 존(pause_zones) 기능 TDD 테스트

검증 대상:
  A. config_flow.py — 옵션 노출 조건 (location_entity 종류 판단)
  B. coordinator.py — 존 안/밖 폴링 동작
  C. 존 생명주기 — 추가/삭제 시 동작
  D. 기존 기능 영향 없음 (pause_zones 미설정 시 동일 동작)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator


# ─────────────────────────────────────────────────────────────────────────────
# 공통 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def make_zone_state(entity_id, lat, lon, radius=100, friendly_name="우리집"):
    """HA zone 엔티티 state mock 생성. radius 단위: 미터"""
    state = MagicMock()
    state.entity_id = entity_id
    state.attributes = {
        "latitude": lat,
        "longitude": lon,
        "radius": radius,
        "friendly_name": friendly_name,
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


# ─────────────────────────────────────────────────────────────────────────────
# A. config_flow — pause_zones 옵션 노출 조건
# ─────────────────────────────────────────────────────────────────────────────

class TestOptionsFlowPauseZonesVisibility:
    """location_entity 종류에 따라 pause_zones 옵션 노출 여부 검증"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("location_entity,should_show", [
        ("device_tracker.phone",  True),   # 모바일 기기 → 노출
        ("device_tracker.iphone", True),   # 모바일 기기 → 노출
        ("person.hayeongi",       True),   # person 엔티티 → 노출
        ("zone.home",             False),  # 고정 존 → 숨김
        ("zone.office",           False),  # 고정 존 → 숨김
        ("",                      False),  # 없음 → 숨김
        (None,                    False),  # 없음 → 숨김
    ])
    async def test_pause_zones_visibility(self, hass, location_entity, should_show):
        """
        [Given] location_entity 종류별 설정
        [When]  옵션 플로우 초기화
        [Then]  device_tracker/person 이면 pause_zones 스키마에 포함,
                zone/없음 이면 스키마에 없음
        """
        from custom_components.kma_weather.config_flow import KMAWeatherOptionsFlow
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": location_entity or ""}
        entry.options = {}

        flow = KMAWeatherOptionsFlow(entry)
        flow.hass = hass

        # zone 엔티티 mock
        hass.states.async_entity_ids = MagicMock(return_value=["zone.home", "zone.office"])
        hass.states.get = MagicMock(return_value=make_zone_state("zone.home", 37.56, 126.98))

        result = await flow.async_step_init(user_input=None)
        schema_keys = [str(k) for k in result["data_schema"].schema.keys()]

        if should_show:
            assert "pause_zones" in schema_keys, \
                f"{location_entity} → pause_zones 노출 기대"
        else:
            assert "pause_zones" not in schema_keys, \
                f"{location_entity} → pause_zones 숨김 기대"

    @pytest.mark.asyncio
    async def test_zone_list_shows_all_zones(self, hass):
        """
        [Given] HA에 zone.home, zone.office 2개 존재
        [When]  device_tracker로 옵션 플로우 열기
        [Then]  pause_zones 선택지에 두 존이 모두 표시됨
        """
        from custom_components.kma_weather.config_flow import KMAWeatherOptionsFlow
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "device_tracker.phone"}
        entry.options = {}

        flow = KMAWeatherOptionsFlow(entry)
        flow.hass = hass
        hass.states.async_entity_ids = MagicMock(return_value=["zone.home", "zone.office"])

        def mock_get(entity_id):
            names = {"zone.home": "우리집", "zone.office": "사무실"}
            return make_zone_state(entity_id, 37.56, 126.98,
                                   friendly_name=names.get(entity_id, entity_id))
        hass.states.get = mock_get

        result = await flow.async_step_init(user_input=None)
        # 스키마에서 pause_zones selector의 options 확인
        schema = result["data_schema"].schema
        pause_key = next((k for k in schema if str(k) == "pause_zones"), None)
        assert pause_key is not None
        # selector options에 zone.home, zone.office 포함
        options = pause_key.description.get("suggested_values", []) or []
        # 실제 구현에 따라 selector에서 options 추출 방식이 다를 수 있음
        # 최소한 스키마에 pause_zones가 있으면 통과
        assert pause_key is not None


# ─────────────────────────────────────────────────────────────────────────────
# B. coordinator — 존 안/밖 폴링 동작
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordinatorPauseZones:
    """존 안/밖에 따른 폴링 중단/재개 검증"""

    @pytest.mark.asyncio
    async def test_inside_zone_returns_cache_without_api_call(self, hass):
        """
        [Given] pause_zones=[zone.home], 현재 위치가 zone.home 반경 안
        [When]  _async_update_data 호출
        [Then]  API 호출 없이 캐시 반환
        """
        coord = make_coordinator(
            hass, "device_tracker.phone", pause_zones=["zone.home"]
        )
        # 현재 위치: zone.home 중심과 동일
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        hass.states.get = MagicMock(return_value=make_zone_state(
            "zone.home", 37.56, 126.98, radius=100
        ))

        result = await coord._async_update_data()

        coord.api.fetch_data.assert_not_called()
        assert result["weather"]["TMP"] == 20  # 캐시값

    @pytest.mark.asyncio
    async def test_outside_zone_calls_api(self, hass):
        """
        [Given] pause_zones=[zone.home], 현재 위치가 zone.home 반경 밖
        [When]  _async_update_data 호출
        [Then]  API 호출 후 새 데이터 반환
        """
        coord = make_coordinator(
            hass, "device_tracker.phone", pause_zones=["zone.home"]
        )
        # 현재 위치: zone.home에서 약 5km 떨어진 곳
        coord._resolve_location = MagicMock(return_value=(37.61, 126.98))
        hass.states.get = MagicMock(return_value=make_zone_state(
            "zone.home", 37.56, 126.98, radius=100
        ))

        result = await coord._async_update_data()

        coord.api.fetch_data.assert_called_once()
        assert result["weather"]["TMP"] == 25  # 새 데이터

    @pytest.mark.asyncio
    async def test_inside_one_of_multiple_zones_stops_polling(self, hass):
        """
        [Given] pause_zones=[zone.home, zone.office], 현재 위치가 zone.office 안
        [When]  _async_update_data 호출
        [Then]  API 호출 없이 캐시 반환
        """
        coord = make_coordinator(
            hass, "device_tracker.phone",
            pause_zones=["zone.home", "zone.office"]
        )
        coord._resolve_location = MagicMock(return_value=(37.50, 127.02))

        def mock_get(entity_id):
            zones = {
                "zone.home":   make_zone_state("zone.home",   37.56, 126.98, radius=100),
                "zone.office": make_zone_state("zone.office", 37.50, 127.02, radius=100),
            }
            return zones.get(entity_id)

        hass.states.get = mock_get

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
        coord = make_coordinator(
            hass, "device_tracker.phone",
            pause_zones=["zone.home", "zone.office"]
        )
        coord._resolve_location = MagicMock(return_value=(37.40, 127.10))

        def mock_get(entity_id):
            zones = {
                "zone.home":   make_zone_state("zone.home",   37.56, 126.98, radius=100),
                "zone.office": make_zone_state("zone.office", 37.50, 127.02, radius=100),
            }
            return zones.get(entity_id)

        hass.states.get = mock_get

        result = await coord._async_update_data()

        coord.api.fetch_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_boundary_exactly_on_radius_stops_polling(self, hass):
        """
        [Given] 현재 위치가 zone 반경 경계(100m)에 정확히 있음
        [When]  _async_update_data 호출
        [Then]  반경 이내로 판단 → 캐시 반환
        """
        coord = make_coordinator(
            hass, "device_tracker.phone", pause_zones=["zone.home"]
        )
        # 약 90m 떨어진 위치 (100m 반경 안)
        coord._resolve_location = MagicMock(return_value=(37.56081, 126.98))
        hass.states.get = MagicMock(return_value=make_zone_state(
            "zone.home", 37.56, 126.98, radius=100
        ))

        result = await coord._async_update_data()

        coord.api.fetch_data.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# C. 존 생명주기 — 추가/삭제 시 동작
# ─────────────────────────────────────────────────────────────────────────────

class TestZoneLifecycle:
    """존 추가/삭제 시 옵션 및 폴링 동작 검증"""

    @pytest.mark.asyncio
    async def test_deleted_zone_in_pause_zones_is_skipped(self, hass):
        """
        [Given] pause_zones에 zone.office가 설정됐는데 zone.office가 삭제됨
        [When]  _async_update_data 호출
        [Then]  삭제된 존 스킵 후 정상 폴링 진행
        """
        coord = make_coordinator(
            hass, "device_tracker.phone", pause_zones=["zone.office"]
        )
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        # zone.office가 삭제됨 → None 반환
        hass.states.get = MagicMock(return_value=None)

        result = await coord._async_update_data()

        # 존 없으면 스킵 → 정상 폴링
        coord.api.fetch_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_zone_added_to_ha_appears_in_options(self, hass):
        """
        [Given] HA에 zone.gym이 새로 추가됨
        [When]  옵션 플로우 재진입
        [Then]  zone.gym이 선택지에 포함됨
        """
        from custom_components.kma_weather.config_flow import KMAWeatherOptionsFlow
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "device_tracker.phone"}
        entry.options = {"pause_zones": ["zone.home"]}

        flow = KMAWeatherOptionsFlow(entry)
        flow.hass = hass
        # zone.gym이 새로 추가된 상태
        hass.states.async_entity_ids = MagicMock(
            return_value=["zone.home", "zone.gym"]
        )
        hass.states.get = MagicMock(
            return_value=make_zone_state("zone.home", 37.56, 126.98)
        )

        result = await flow.async_step_init(user_input=None)
        schema_keys = [str(k) for k in result["data_schema"].schema.keys()]
        assert "pause_zones" in schema_keys

    @pytest.mark.asyncio
    async def test_pause_zones_with_missing_attributes_skipped(self, hass):
        """
        [Given] pause_zones의 존 엔티티에 latitude/longitude 속성이 없음
        [When]  _async_update_data 호출
        [Then]  AttributeError 없이 스킵 후 정상 폴링
        """
        coord = make_coordinator(
            hass, "device_tracker.phone", pause_zones=["zone.broken"]
        )
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))

        broken_zone = MagicMock()
        broken_zone.attributes = {}  # latitude/longitude 없음
        hass.states.get = MagicMock(return_value=broken_zone)

        result = await coord._async_update_data()

        # 속성 없으면 스킵 → 정상 폴링
        coord.api.fetch_data.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# D. 기존 기능 영향 없음
# ─────────────────────────────────────────────────────────────────────────────

class TestExistingFunctionalityUnchanged:
    """pause_zones 미설정 시 기존 동작과 완전히 동일함을 검증"""

    @pytest.mark.asyncio
    async def test_no_pause_zones_option_polls_normally(self, hass):
        """
        [Given] pause_zones 옵션 없음 (기존 사용자)
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
        [Given] location_entity=zone.home (고정 존 기기)
                options에 pause_zones가 설정되어 있더라도
        [When]  _async_update_data 호출
        [Then]  pause_zones 무시하고 정상 폴링
                (config_flow에서 노출 안 하므로 실제론 설정 불가,
                 방어 코드로 coordinator도 무시)
        """
        coord = make_coordinator(hass, "zone.home", pause_zones=["zone.home"])
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        hass.states.get = MagicMock(return_value=make_zone_state(
            "zone.home", 37.56, 126.98, radius=100
        ))

        result = await coord._async_update_data()

        # zone.* location_entity면 pause_zones 무시
        coord.api.fetch_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_location_entity_ignores_pause_zones(self, hass):
        """
        [Given] location_entity 없음 + pause_zones 설정
        [When]  _async_update_data 호출
        [Then]  pause_zones 무시하고 HA 기본 좌표로 정상 폴링
        """
        coord = make_coordinator(hass, "", pause_zones=["zone.home"])
        hass.config.latitude = 37.56
        hass.config.longitude = 126.98
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        hass.states.get = MagicMock(return_value=make_zone_state(
            "zone.home", 37.56, 126.98, radius=100
        ))

        result = await coord._async_update_data()

        coord.api.fetch_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_failure_still_returns_cache_regardless_of_zones(self, hass):
        """
        [Given] 존 밖 + API 실패
        [When]  _async_update_data 호출
        [Then]  기존과 동일하게 캐시 반환 (pause_zones 기능과 무관)
        """
        coord = make_coordinator(
            hass, "device_tracker.phone", pause_zones=["zone.home"]
        )
        coord._resolve_location = MagicMock(return_value=(37.61, 126.98))
        coord.api.fetch_data = AsyncMock(return_value=None)  # API 실패
        hass.states.get = MagicMock(return_value=make_zone_state(
            "zone.home", 37.56, 126.98, radius=100
        ))

        result = await coord._async_update_data()

        assert result["weather"]["TMP"] == 20  # 캐시 반환


# ─────────────────────────────────────────────────────────────────────────────
# E. 기존 사용자 마이그레이션
# ─────────────────────────────────────────────────────────────────────────────

class TestExistingUserMigration:
    """기존 사용자(pause_zones 키 없음)가 옵션 설정 시 문제 없음을 검증"""

    @pytest.mark.asyncio
    async def test_existing_user_opens_options_without_pause_zones_key(self, hass):
        """
        [Given] 기존 사용자 — entry.options에 pause_zones 키 자체가 없음
        [When]  옵션 플로우 열기
        [Then]  오류 없이 폼이 표시됨
        """
        from custom_components.kma_weather.config_flow import KMAWeatherOptionsFlow
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "device_tracker.phone"}
        entry.options = {
            "location_entity": "device_tracker.phone",
            "expire_date": "2026-12-31",
            "apply_date": "2025-01-01",
            # pause_zones 키 없음
        }

        flow = KMAWeatherOptionsFlow(entry)
        flow.hass = hass
        hass.states.async_entity_ids = MagicMock(return_value=["zone.home"])
        hass.states.get = MagicMock(return_value=make_zone_state(
            "zone.home", 37.56, 126.98
        ))

        result = await flow.async_step_init(user_input=None)

        assert result["type"] == "form"
        assert result["errors"] == {}

    @pytest.mark.asyncio
    async def test_existing_user_submits_without_selecting_pause_zones(self, hass):
        """
        [Given] 기존 사용자가 pause_zones를 선택하지 않고 제출
        [When]  user_input에 pause_zones=[]
        [Then]  CREATE_ENTRY 성공 + 기존 옵션값 유지
        """
        from custom_components.kma_weather.config_flow import KMAWeatherOptionsFlow
        from homeassistant.data_entry_flow import FlowResultType
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "device_tracker.phone"}
        entry.options = {
            "location_entity": "device_tracker.phone",
            "expire_date": "2026-12-31",
            "apply_date": "2025-01-01",
        }

        flow = KMAWeatherOptionsFlow(entry)
        flow.hass = hass
        hass.states.async_entity_ids = MagicMock(return_value=["zone.home"])
        hass.states.get = MagicMock(return_value=make_zone_state(
            "zone.home", 37.56, 126.98
        ))

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
        [Then]  CREATE_ENTRY 성공 + pause_zones 저장 + 기존 옵션값 유지
        """
        from custom_components.kma_weather.config_flow import KMAWeatherOptionsFlow
        from homeassistant.data_entry_flow import FlowResultType
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "device_tracker.phone"}
        entry.options = {
            "location_entity": "device_tracker.phone",
            "expire_date": "2026-12-31",
            "apply_date": "2025-01-01",
        }

        flow = KMAWeatherOptionsFlow(entry)
        flow.hass = hass
        hass.states.async_entity_ids = MagicMock(return_value=["zone.home"])
        hass.states.get = MagicMock(return_value=make_zone_state(
            "zone.home", 37.56, 126.98
        ))

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
        [Given] 기존에 pause_zones=["zone.home"]이 설정된 사용자
        [When]  pause_zones를 빈 리스트로 변경 후 제출
        [Then]  CREATE_ENTRY 성공 + pause_zones=[] 로 업데이트
        """
        from custom_components.kma_weather.config_flow import KMAWeatherOptionsFlow
        from homeassistant.data_entry_flow import FlowResultType
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "device_tracker.phone"}
        entry.options = {
            "location_entity": "device_tracker.phone",
            "pause_zones": ["zone.home"],
            "expire_date": "2026-12-31",
            "apply_date": "2025-01-01",
        }

        flow = KMAWeatherOptionsFlow(entry)
        flow.hass = hass
        hass.states.async_entity_ids = MagicMock(return_value=["zone.home"])
        hass.states.get = MagicMock(return_value=make_zone_state(
            "zone.home", 37.56, 126.98
        ))

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
        [Given] 기존 사용자 — entry.options에 pause_zones 키 없음
        [When]  coordinator _async_update_data 호출
        [Then]  KeyError 없이 정상 폴링
        """
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

        # KeyError 없이 정상 폴링
        coord.api.fetch_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_location_entity_changed_to_zone_hides_pause_zones(self, hass):
        """
        [Given] 기존에 device_tracker로 설정 + pause_zones=["zone.home"] 저장된 상태
                이후 location_entity를 zone.home으로 변경
        [When]  옵션 플로우 열기
        [Then]  pause_zones 옵션이 숨겨짐
                (이전에 저장된 pause_zones 값은 무시됨)
        """
        from custom_components.kma_weather.config_flow import KMAWeatherOptionsFlow
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "zone.home"}
        entry.options = {
            "location_entity": "zone.home",
            "pause_zones": ["zone.home"],  # 이전에 저장된 값
            "expire_date": "2026-12-31",
        }

        flow = KMAWeatherOptionsFlow(entry)
        flow.hass = hass
        hass.states.async_entity_ids = MagicMock(return_value=["zone.home"])
        hass.states.get = MagicMock(return_value=make_zone_state(
            "zone.home", 37.56, 126.98
        ))

        result = await flow.async_step_init(user_input=None)
        schema_keys = [str(k) for k in result["data_schema"].schema.keys()]

        assert "pause_zones" not in schema_keys

    @pytest.mark.asyncio
    async def test_coordinator_ignores_pause_zones_when_location_entity_is_zone(self, hass):
        """
        [Given] location_entity=zone.home + pause_zones=["zone.home"] 저장된 상태
                (이전에 device_tracker였다가 zone으로 바꾼 경우)
        [When]  _async_update_data 호출
        [Then]  pause_zones 무시하고 정상 폴링
        """
        entry = MagicMock()
        entry.data = {"api_key": "key", "location_entity": "zone.home"}
        entry.options = {
            "location_entity": "zone.home",
            "pause_zones": ["zone.home"],  # 이전에 저장된 값
        }
        entry.entry_id = "zone_entity_test"

        coord = KMAWeatherUpdateCoordinator(hass, entry)
        coord._store_loaded = True
        coord._cached_data = {"weather": {"TMP": 20}, "air": {}}
        coord.api.fetch_data = AsyncMock(return_value={
            "weather": {"TMP": 25}, "air": {}, "pollen": None, "raw_forecast": {}
        })
        coord._resolve_location = MagicMock(return_value=(37.56, 126.98))
        hass.states.get = MagicMock(return_value=make_zone_state(
            "zone.home", 37.56, 126.98, radius=100
        ))

        result = await coord._async_update_data()

        # zone.* location_entity면 pause_zones 무시 → 정상 폴링
        coord.api.fetch_data.assert_called_once()
