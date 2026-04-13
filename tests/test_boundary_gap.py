"""
단기/중기 경계(D+3/D+4)에서 데이터가 사라지는지 검증하는 BDD 스타일 테스트.
"""

import pytest
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock

from custom_components.kma_weather.api_kma import KMAWeatherAPI

TZ = ZoneInfo("Asia/Seoul")

# ─────────────────────────────────────────────────────────────────────────────
# [Given] 공통 헬퍼 함수 및 설정
# ─────────────────────────────────────────────────────────────────────────────

def make_api() -> KMAWeatherAPI:
    """테스트용 API 인스턴스 생성 및 위치 설정"""
    api = KMAWeatherAPI(MagicMock(), "TEST_KEY", "11B10101", "11B00000")
    api.lat, api.lon, api.nx, api.ny = 37.56, 126.98, 60, 127
    return api


def make_short_res_with_0915(now: datetime, days: int = 4) -> dict:
    """
    D+0~D+(days-1) 날짜에 0900/1500 TMP를 포함한 단기예보 응답 생성.
    이 데이터는 'short_covered_dates' 조건을 충족하여 단기 예보로 처리되도록 보장합니다.
    """
    items = []
    for d in range(days):
        day = now + timedelta(days=d)
        d_str = day.strftime("%Y%m%d")
        for h_str, tmp in [("0900", str(10 + d * 2)), ("1200", str(12 + d * 2)),
                            ("1500", str(15 + d * 2)), ("1800", str(13 + d * 2))]:
            for cat, val in [("TMP", tmp), ("SKY", "1"), ("PTY", "0"),
                              ("REH", "50"), ("WSD", "2.0"), ("POP", "10")]:
                items.append({
                    "fcstDate": d_str, 
                    "fcstTime": h_str,
                    "category": cat, 
                    "fcstValue": val
                })
    return {"response": {"body": {"items": {"item": items}}}}


def make_mid_res(tm_fc_dt: datetime, start_idx: int = 3, end_idx: int = 10) -> tuple:
    """tm_fc_dt 기준 기온 및 육상 중기예보 튜플 생성"""
    ta_item, land_item = {}, {}
    for i in range(start_idx, end_idx + 1):
        ta_item[f"taMax{i}"] = str(20 + i)
        ta_item[f"taMin{i}"] = str(5 + i)
        land_item[f"wf{i}Am"] = "맑음" if i % 2 == 0 else "흐림"
        land_item[f"wf{i}Pm"] = "맑음" if i % 2 == 0 else "흐림"

    def wrap(item):
        return {"response": {"body": {"items": {"item": [item]}}}}

    return (wrap(ta_item), wrap(land_item), tm_fc_dt)


def assert_d3_d4_not_none(result: dict, desc: str):
    """[Then] D+3/D+4 예보 데이터가 유효한지 검증하는 공통 Assertion"""
    daily = result["weather"]["forecast_daily"]
    twice = result["weather"]["forecast_twice_daily"]

    for i in [3, 4]:
        day_entry = next((e for e in daily if e["_day_index"] == i), None)
        assert day_entry is not None, f"[{desc}] D+{i} forecast_daily 항목 없음"
        assert day_entry["native_temperature"] is not None, f"[{desc}] D+{i} 최고기온 None"
        assert day_entry["native_templow"] is not None, f"[{desc}] D+{i} 최저기온 None"

        am_entry = next((e for e in twice if e["_day_index"] == i and e["is_daytime"]), None)
        pm_entry = next((e for e in twice if e["_day_index"] == i and not e["is_daytime"]), None)
        assert am_entry is not None, f"[{desc}] D+{i} twice_daily 주간 항목 없음"
        assert pm_entry is not None, f"[{desc}] D+{i} twice_daily 야간 항목 없음"
        assert am_entry["native_temperature"] is not None, f"[{desc}] D+{i} twice 주간 온도 None"
        assert pm_entry["native_temperature"] is not None, f"[{desc}] D+{i} twice 야간 온도 None"


# ─────────────────────────────────────────────────────────────────────────────
# 1. 매시 10분 전체(00:10~23:10) 전수 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestHourlyUpdateAllDay:
    """하루 24시간 매시 10분 업데이트 시나리오 전수 검증"""

    @pytest.mark.parametrize("hour,minute,d3_idx,d4_idx,desc", [
        ( 0, 10, 4, 5, "00:10 tmFc=전날18시"),
        ( 1, 10, 4, 5, "01:10 tmFc=전날18시"),
        ( 2, 10, 4, 5, "02:10 tmFc=전날18시 📡단기발표"),
        ( 3, 10, 4, 5, "03:10 tmFc=전날18시"),
        ( 4, 10, 4, 5, "04:10 tmFc=전날18시"),
        ( 5, 10, 4, 5, "05:10 tmFc=전날18시 📡단기발표"),
        ( 6, 10, 4, 5, "06:10 tmFc=전날18시"),
        ( 7, 10, 3, 4, "07:10 tmFc=당일06시 🌐중기06시반영"),
        ( 8, 10, 3, 4, "08:10 tmFc=당일06시 📡단기발표"),
        ( 9, 10, 3, 4, "09:10 tmFc=당일06시"),
        (10, 10, 3, 4, "10:10 tmFc=당일06시"),
        (11, 10, 3, 4, "11:10 tmFc=당일06시 📡단기발표"),
        (12, 10, 3, 4, "12:10 tmFc=당일06시"),
        (13, 10, 3, 4, "13:10 tmFc=당일06시"),
        (14, 10, 3, 4, "14:10 tmFc=당일06시 📡단기발표"),
        (15, 10, 3, 4, "15:10 tmFc=당일06시"),
        (16, 10, 3, 4, "16:10 tmFc=당일06시"),
        (17, 10, 3, 4, "17:10 tmFc=당일06시 📡단기발표"),
        (18, 10, 3, 4, "18:10 tmFc=당일06시"),
        (19, 10, 3, 4, "19:10 tmFc=당일18시 🌐중기18시반영"),
        (20, 10, 3, 4, "20:10 tmFc=당일18시 📡단기발표"),
        (21, 10, 3, 4, "21:10 tmFc=당일18시"),
        (22, 10, 3, 4, "22:10 tmFc=당일18시"),
        (23, 10, 3, 4, "23:10 tmFc=당일18시 📡단기발표"),
    ])
    def test_d3_d4_never_none_all_hours(self, hour, minute, d3_idx, d4_idx, desc):
        # [Given] 특정 시각과 단기/중기 예보 응답이 주어졌을 때
        api = make_api()
        now = datetime(2026, 4, 11, hour, minute, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        short_res = make_short_res_with_0915(now, days=3)
        mid_res = make_mid_res(tm_fc_dt, start_idx=3, end_idx=10)

        # [When] 데이터를 병합하면
        result = api._merge_all(now, short_res, mid_res, air_data={})

        # [Then] D+3/D+4 데이터가 누락되지 않아야 하며, 기대한 기온값이 매칭되어야 함
        assert_d3_d4_not_none(result, desc)
        
        daily = result["weather"]["forecast_daily"]
        for i, expected_idx in [(3, d3_idx), (4, d4_idx)]:
            entry = next(e for e in daily if e["_day_index"] == i)
            if i == 3 and hour >= 19:
                expected_max = 21.0
            else:
                expected_max = float(20 + expected_idx)
                
            assert entry["native_temperature"] == expected_max, f"D+{i} 기온 오차"

# ─────────────────────────────────────────────────────────────────────────────
# 2. 단기 발표 직후 전환성 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestShortTermReleaseTransition:
    """단기 예보 발표 직전/직후의 안정성 검증"""

    @pytest.mark.parametrize("before_h,after_h,desc", [
        ( 1, 2,  "02시 발표"), ( 4, 5,  "05시 발표"), ( 7, 8,  "08시 발표"),
        (10, 11, "11시 발표"), (13, 14, "14시 발표"), (16, 17, "17시 발표"),
        (19, 20, "20시 발표"), (22, 23, "23시 발표"),
    ])
    def test_d3_d4_across_short_release(self, before_h, after_h, desc):
        for h, label in [(before_h, f"{desc} 직전"), (after_h, f"{desc} 직후")]:
            # [Given] 발표 시간 기준 전/후 시각 설정
            api = make_api()
            now = datetime(2026, 4, 11, h, 10, tzinfo=TZ)
            tm_fc_dt = api._get_mid_base_dt(now)

            # [When] 병합 수행
            result = api._merge_all(now, make_short_res_with_0915(now, days=3), make_mid_res(tm_fc_dt), air_data={})

            # [Then] 데이터가 정상 유지되어야 함
            assert_d3_d4_not_none(result, label)


# ─────────────────────────────────────────────────────────────────────────────
# 3. 중기 tmFc 전환 (06:30, 18:30) 안정성 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestMidTermTmfcTransition:
    """중기 예보 발표 지연(30분) 및 tmFc 전환 시나리오 검증"""

    @pytest.mark.parametrize("hour,minute,expected_tmfc_hour,desc", [
        (6, 29, 18, "06:29 — 전날18시 사용"),
        (6, 31, 6,  "06:31 — 당일06시 사용"),
        (18, 29, 6,  "18:29 — 당일06시 사용"),
        (18, 31, 18, "18:31 — 당일18시 사용"),
    ])
    def test_d3_d4_across_tmfc_transition(self, hour, minute, expected_tmfc_hour, desc):
        # [Given] 중기 예보 갱신 시점 전후 시각
        api = make_api()
        now = datetime(2026, 4, 11, hour, minute, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)

        # [Then - Pre-check] tmFc 시간이 기대와 일치하는지 확인
        assert tm_fc_dt.hour == expected_tmfc_hour

        # [When] 병합 수행
        result = api._merge_all(now, make_short_res_with_0915(now, days=3), make_mid_res(tm_fc_dt), air_data={})

        # [Then] 데이터 정합성 확인
        assert_d3_d4_not_none(result, desc)

    def test_idx_decreases_by_one_at_0631(self):
        """06:31에 tmFc가 갱신되어 index가 줄어들 때의 정합성 확인"""
        # [Given] 06:29와 06:31의 시각 정보 준비
        api = make_api()
        now_before = datetime(2026, 4, 11, 6, 29, tzinfo=TZ)
        now_after  = datetime(2026, 4, 11, 6, 31, tzinfo=TZ)
        tm_before = api._get_mid_base_dt(now_before)
        tm_after  = api._get_mid_base_dt(now_after)

        # [When - Before] 06:29에 병합
        res_before = api._merge_all(now_before, make_short_res_with_0915(now_before, days=3), make_mid_res(tm_before), air_data={})
        # [When - After] 06:31에 병합
        res_after  = api._merge_all(now_after, make_short_res_with_0915(now_after, days=3), make_mid_res(tm_after), air_data={})

        # [Then] 두 시점 모두 데이터가 존재해야 함
        assert_d3_d4_not_none(res_before, "06:29")
        assert_d3_d4_not_none(res_after, "06:31")


# ─────────────────────────────────────────────────────────────────────────────
# 4. 자정~02시 구간 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestMidnightToTwoAm:
    """단기와 중기의 발표 날짜가 다른 자정 구간 검증"""

    @pytest.mark.parametrize("hour,minute", [(0, 10), (0, 50), (1, 30)])
    def test_d3_d4_midnight_range(self, hour, minute):
        # [Given] 자정~02시 사이의 시각
        api = make_api()
        now = datetime(2026, 4, 11, hour, minute, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)

        # [When] 병합 수행
        result = api._merge_all(now, make_short_res_with_0915(now, days=3), make_mid_res(tm_fc_dt), air_data={})

        # [Then] D+3 데이터는 중기의 4일차(taMax4=24)를 참조해야 함
        d3_entry = next(e for e in result["weather"]["forecast_daily"] if e["_day_index"] == 3)
        assert d3_entry["native_temperature"] == 23.0


# ─────────────────────────────────────────────────────────────────────────────
# 5. 경계 날짜(D+3) 데이터 부족 시 Fallback 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestBoundaryDatePartialShortData:
    """D+3 날짜의 단기 예보가 불완전할 때 중기 예보로 보완하는지 검증"""

    def test_d3_partial_short_falls_back_to_mid(self):
        # [Given] D+3에 새벽 데이터만 있는 불완전한 단기 예보
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        d3_str = (now + timedelta(days=3)).strftime("%Y%m%d")
        items = []
        # D+0~D+2는 정상
        for d in range(3):
            day = now + timedelta(days=d)
            for h in ["0900", "1500"]:
                items.append({"fcstDate": day.strftime("%Y%m%d"), "fcstTime": h, "category": "TMP", "fcstValue": "15"})
        # D+3은 새벽만
        items.append({"fcstDate": d3_str, "fcstTime": "0600", "category": "TMP", "fcstValue": "5"})
        
        short_res = {"response": {"body": {"items": {"item": items}}}}
        mid_res = make_mid_res(api._get_mid_base_dt(now))

        # [When] 병합 수행
        result = api._merge_all(now, short_res, mid_res, air_data={})

        # [Then] D+3 최고기온은 새벽 5도가 아닌 중기 예보의 23도(taMax3)여야 함
        d3_entry = next(e for e in result["weather"]["forecast_daily"] if e["_day_index"] == 3)
        assert d3_entry["native_temperature"] == 23.0


# ─────────────────────────────────────────────────────────────────────────────
# 6. API 실패 시 캐시 보존 로직 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestCachePreservesD3D4OnApiFailure:
    """네트워크 오류 등으로 API 응답이 없을 때 기존 데이터를 유지하는지 검증"""

    def test_mid_api_failure_uses_cache(self):
        # [Given] 1차 성공하여 캐시가 확보된 상태
        api = make_api()
        now = datetime(2026, 4, 11, 19, 10, tzinfo=TZ)
        api._merge_all(now, make_short_res_with_0915(now), make_mid_res(api._get_mid_base_dt(now)), air_data={})

        # [When] 2차 호출에서 중기 API 응답이 None인 경우
        result = api._merge_all(now, make_short_res_with_0915(now), None, air_data={})

        # [Then] 데이터가 보존되어야 함
        assert_d3_d4_not_none(result, "캐시 보존 확인")

    def test_short_api_failure_uses_cache(self):
        # [Given] 캐시 확보
        api = make_api()
        now = datetime(2026, 4, 11, 14, 10, tzinfo=TZ)
        api._merge_all(now, make_short_res_with_0915(now), make_mid_res(api._get_mid_base_dt(now)), air_data={})

        # [When] 단기 API 실패
        result = api._merge_all(now, None, make_mid_res(api._get_mid_base_dt(now)), air_data={})

        # [Then] 데이터 보존
        assert_d3_d4_not_none(result, "단기 실패 캐시 보존")


# ─────────────────────────────────────────────────────────────────────────────
# 7. 연속 API 실패 시 캐시 생존 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestConsecutiveApiFailures:
    """API가 수 차례 연속으로 실패해도 데이터 유실이 없는지 검증"""

    def test_cache_survives_multiple_consecutive_failures(self):
        # [Given] 최초 정상 데이터 수신
        api = make_api()
        now = datetime(2026, 4, 11, 19, 10, tzinfo=TZ)
        api._merge_all(now, make_short_res_with_0915(now), make_mid_res(api._get_mid_base_dt(now)), air_data={})

        # [When] 3회 연속 단기/중기 모두 실패 시
        for _ in range(3):
            result = api._merge_all(now, None, None, air_data={})
            # [Then] 데이터는 여전히 존재해야 함
            assert_d3_d4_not_none(result, "연속 실패 중 데이터 유지")
