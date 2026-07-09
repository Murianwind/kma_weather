"""
test_heatwave_night_warning.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
열대야특보(warnVar=13) 매핑 검증

배경: WARN_TYPE_MAP에 warnVar=13(열대야)이 누락되어 있어서,
실제로 열대야주의보/경보가 발효 중이어도 조용히 무시되고
"특보없음"으로 표시되던 문제 수정.

실제 기상청 getPwnCd API 응답(2026-07-09, 칠곡군/의성군/제주시동부/
경주시중북부/포항시 열대야 사례)을 기반으로 시나리오 구성.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock

from custom_components.kma_weather.api_kma import KMAWeatherAPI
from custom_components.kma_weather.const import WARN_TYPE_MAP


def make_api():
    return KMAWeatherAPI(MagicMock(), "TEST_KEY")


# ─────────────────────────────────────────────────────────────────────────────
# 1. WARN_TYPE_MAP 자체에 13번이 있는지
# ─────────────────────────────────────────────────────────────────────────────

class TestWarnTypeMapHasHeatwaveNight:
    """WARN_TYPE_MAP에 열대야(warnVar=13)가 등록되어 있는지 검증"""

    def test_warn_type_map_contains_key_13(self):
        """
        [Given] WARN_TYPE_MAP 상수
        [When]  "13" 키를 조회
        [Then]  (주의보명, 경보명) 튜플이 존재해야 함
        """
        assert "13" in WARN_TYPE_MAP

    def test_warn_type_map_13_names_are_heatwave_night(self):
        """
        [Given] WARN_TYPE_MAP["13"]
        [When]  주의보/경보 이름을 확인
        [Then]  "열대야주의보"/"열대야경보"여야 함
        """
        advisory, warning = WARN_TYPE_MAP["13"]
        assert advisory == "열대야주의보"
        assert warning == "열대야경보"


# ─────────────────────────────────────────────────────────────────────────────
# 2. _get_warning() 실제 동작 검증 (실제 API 응답 기반)
# ─────────────────────────────────────────────────────────────────────────────

class TestGetWarningWithHeatwaveNight:
    """실제 기상청 응답 형태로 열대야특보가 정상 표시되는지 검증"""

    @pytest.mark.asyncio
    async def test_active_heatwave_night_advisory_is_shown(self):
        """
        [Given] 신규 발효(command=1, cancel=0, endTime=0)된 열대야주의보
                (2026-07-09 제주시동부 실제 사례 기반)
        [When]  _get_warning 호출
        [Then]  "열대야주의보"가 반환됨 (이전에는 매핑이 없어 무시되었음)
        """
        api = make_api()
        response = {
            "response": {
                "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                "body": {"items": {"item": [
                    {"areaCode": "L1091330", "areaName": "제주시동부",
                     "cancel": "0", "command": "1", "endTime": 0,
                     "tmFc": 202607091620, "warnVar": 13, "warnStress": 0},
                ]}},
            }
        }
        api._fetch = AsyncMock(return_value=response)

        result = await api._get_warning("L1091330")

        assert result == "열대야주의보"

    @pytest.mark.asyncio
    async def test_cancelled_heatwave_night_is_not_shown(self):
        """
        [Given] 해제된(command=2) 열대야특보
                (2026-07-09 칠곡군 실제 사례 기반)
        [When]  _get_warning 호출
        [Then]  "특보없음" (해제된 특보는 활성 특보 목록에서 제외되어야 함)
        """
        api = make_api()
        response = {
            "response": {
                "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                "body": {"items": {"item": [
                    {"areaCode": "L1071000", "areaName": "칠곡군",
                     "cancel": "0", "command": "2", "endTime": 202607091620,
                     "tmFc": 202607091620, "warnVar": 13, "warnStress": 0},
                ]}},
            }
        }
        api._fetch = AsyncMock(return_value=response)

        result = await api._get_warning("L1071000")

        assert result == "특보없음"

    @pytest.mark.asyncio
    async def test_heatwave_night_combined_with_other_warning(self):
        """
        [Given] 같은 지역에 열대야주의보 + 호우주의보가 동시에 활성
        [When]  _get_warning 호출
        [Then]  두 특보 이름이 쉼표로 함께 표시됨
        """
        api = make_api()
        response = {
            "response": {
                "header": {"resultCode": "00"},
                "body": {"items": {"item": [
                    {"areaCode": "L1071000", "cancel": "0", "command": "1",
                     "endTime": 0, "tmFc": 202607091620, "warnVar": 13, "warnStress": 0},
                    {"areaCode": "L1071000", "cancel": "0", "command": "1",
                     "endTime": 0, "tmFc": 202607091620, "warnVar": 2, "warnStress": 0},
                ]}},
            }
        }
        api._fetch = AsyncMock(return_value=response)

        result = await api._get_warning("L1071000")

        assert "열대야주의보" in result
        assert "호우주의보" in result

    @pytest.mark.asyncio
    async def test_heatwave_night_warning_level_stress_1(self):
        """
        [Given] warnStress=1(경보 수준)인 열대야특보
        [When]  _get_warning 호출
        [Then]  "열대야경보"로 표시됨 (주의보가 아니라 경보)
        """
        api = make_api()
        response = {
            "response": {
                "header": {"resultCode": "00"},
                "body": {"items": {"item": [
                    {"areaCode": "L1072400", "cancel": "0", "command": "1",
                     "endTime": 0, "tmFc": 202607091620, "warnVar": 13, "warnStress": 1},
                ]}},
            }
        }
        api._fetch = AsyncMock(return_value=response)

        result = await api._get_warning("L1072400")

        assert result == "열대야경보"
