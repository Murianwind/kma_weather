"""
단기/중기 경계(D+3/D+4)에서 데이터가 사라지는지 검증하는 BDD 스타일 테스트.

변경된 로직:
  - i=0~3 (D+0~D+3): 단기예보
  - i=4~5 (D+4~D+5): 중기 우선, 없으면 단기 폴백
  - i=6~9 (D+6~D+9): 중기예보
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
    api = KMAWeatherAPI(MagicMock(), "TEST_KEY", "11B10101", "11B00000")
    api.lat, api.lon, api.nx, api.ny = 37.56, 126.98, 60, 127
    return api


def make_short_res_with_0915(now: datetime, days: int = 4) -> dict:
    """
    D+0~D+(days-1) 날짜에 0900/1500 TMP를 포함한 단기예보 응답 생성.
    새 로직에서 D+3까지 단기이므로 기본값을 4일치로 변경.
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

    @pytest.mark.parametrize("hour,minute,desc", [
        ( 0, 10, "00:10 tmFc=전날18시"),
        ( 1, 10, "01:10 tmFc=전날18시"),
        ( 2, 10, "02:10 tmFc=전날18시 📡단기발표"),
        ( 3, 10, "03:10 tmFc=전날18시"),
        ( 4, 10, "04:10 tmFc=전날18시"),
        ( 5, 10, "05:10 tmFc=전날18시 📡단기발표"),
        ( 6, 10, "06:10 tmFc=전날18시"),
        ( 7, 10, "07:10 tmFc=당일06시 🌐중기06시반영"),
        ( 8, 10, "08:10 tmFc=당일06시 📡단기발표"),
        ( 9, 10, "09:10 tmFc=당일06시"),
        (10, 10, "10:10 tmFc=당일06시"),
        (11, 10, "11:10 tmFc=당일06시 📡단기발표"),
        (12, 10, "12:10 tmFc=당일06시"),
        (13, 10, "13:10 tmFc=당일06시"),
        (14, 10, "14:10 tmFc=당일06시 📡단기발표"),
        (15, 10, "15:10 tmFc=당일06시"),
        (16, 10, "16:10 tmFc=당일06시"),
        (17, 10, "17:10 tmFc=당일06시 📡단기발표"),
        (18, 10, "18:10 tmFc=당일06시"),
        (19, 10, "19:10 tmFc=당일18시 🌐중기18시반영"),
        (20, 10, "20:10 tmFc=당일18시 📡단기발표"),
        (21, 10, "21:10 tmFc=당일18시"),
        (22, 10, "22:10 tmFc=당일18시"),
        (23, 10, "23:10 tmFc=당일18시 📡단기발표"),
    ])
    def test_d3_d4_never_none_all_hours(self, hour, minute, desc):
        # [Given] 특정 시각과 단기(4일치)/중기 예보 응답이 주어졌을 때
        api = make_api()
        now = datetime(2026, 4, 11, hour, minute, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        # D+3까지 단기이므로 4일치 생성
        short_res = make_short_res_with_0915(now, days=4)
        mid_res = make_mid_res(tm_fc_dt, start_idx=3, end_idx=10)

        # [When] 데이터를 병합하면
        result = api._merge_all(now, short_res, mid_res, air_data={})

        # [Then] D+3/D+4 데이터가 누락되지 않아야 함
        assert_d3_d4_not_none(result, desc)

        # D+3은 단기(4일치 데이터의 마지막), D+4는 중기
        daily = result["weather"]["forecast_daily"]
        # D+3: 단기 데이터 존재 확인
        d3 = next(e for e in daily if e["_day_index"] == 3)
        assert d3["native_temperature"] is not None, f"D+3 기온 None ({desc})"

        # D+4: 중기 데이터 (mid_day_idx 기준)
        d4 = next(e for e in daily if e["_day_index"] == 4)
        target_date = (now + timedelta(days=4)).date()
        mid_day_idx = (target_date - tm_fc_dt.date()).days
        expected_max = float(20 + mid_day_idx)
        assert d4["native_temperature"] == expected_max, f"D+4 기온 오차 ({desc})"


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
            api = make_api()
            now = datetime(2026, 4, 11, h, 10, tzinfo=TZ)
            tm_fc_dt = api._get_mid_base_dt(now)

            result = api._merge_all(now, make_short_res_with_0915(now, days=4),
                                    make_mid_res(tm_fc_dt), air_data={})

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
        api = make_api()
        now = datetime(2026, 4, 11, hour, minute, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)

        assert tm_fc_dt.hour == expected_tmfc_hour

        result = api._merge_all(now, make_short_res_with_0915(now, days=4),
                                make_mid_res(tm_fc_dt), air_data={})

        assert_d3_d4_not_none(result, desc)

    def test_idx_decreases_by_one_at_0631(self):
        """06:31에 tmFc가 갱신되어 index가 줄어들 때의 정합성 확인"""
        api = make_api()
        now_before = datetime(2026, 4, 11, 6, 29, tzinfo=TZ)
        now_after  = datetime(2026, 4, 11, 6, 31, tzinfo=TZ)
        tm_before = api._get_mid_base_dt(now_before)
        tm_after  = api._get_mid_base_dt(now_after)

        res_before = api._merge_all(now_before, make_short_res_with_0915(now_before, days=4),
                                    make_mid_res(tm_before), air_data={})
        res_after  = api._merge_all(now_after, make_short_res_with_0915(now_after, days=4),
                                    make_mid_res(tm_after), air_data={})

        assert_d3_d4_not_none(res_before, "06:29")
        assert_d3_d4_not_none(res_after, "06:31")


# ─────────────────────────────────────────────────────────────────────────────
# 4. 자정~02시 구간 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestMidnightToTwoAm:
    """단기와 중기의 발표 날짜가 다른 자정 구간 검증"""

    @pytest.mark.parametrize("hour,minute", [(0, 10), (0, 50), (1, 30)])
    def test_d3_d4_midnight_range(self, hour, minute):
        api = make_api()
        now = datetime(2026, 4, 11, hour, minute, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)

        result = api._merge_all(now, make_short_res_with_0915(now, days=4),
                                make_mid_res(tm_fc_dt), air_data={})

        # D+3은 단기 데이터로 채워져야 함
        d3_entry = next(e for e in result["weather"]["forecast_daily"] if e["_day_index"] == 3)
        assert d3_entry["native_temperature"] is not None, "D+3 단기 기온이 None"

        # D+4는 중기 데이터 (mid_day_idx 기준)
        target_date = (now + timedelta(days=4)).date()
        mid_day_idx = (target_date - tm_fc_dt.date()).days
        d4_entry = next(e for e in result["weather"]["forecast_daily"] if e["_day_index"] == 4)
        assert d4_entry["native_temperature"] == float(20 + mid_day_idx)


# ─────────────────────────────────────────────────────────────────────────────
# 5. 경계 날짜(D+4) 중기 없을 때 단기 폴백 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestBoundaryDatePartialShortData:
    """D+4 날짜에 중기가 없을 때 단기 폴백이 동작하는지 검증"""

    def test_d4_falls_back_to_short_when_mid_missing(self):
        # [Given] 중기예보에 D+4(mid_day_idx=4) 데이터가 없는 상황
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)  # 당일 06시

        # 단기예보: D+0~D+4 (5일치)
        items = []
        for d in range(5):
            day = now + timedelta(days=d)
            for h in ["0900", "1500"]:
                for cat, val in [("TMP", str(15 + d)), ("SKY", "1"), ("PTY", "0")]:
                    items.append({"fcstDate": day.strftime("%Y%m%d"),
                                  "fcstTime": h, "category": cat, "fcstValue": val})
        short_res = {"response": {"body": {"items": {"item": items}}}}

        # 중기예보: mid_day_idx=5부터만 존재 (4 없음)
        mid_res = make_mid_res(tm_fc_dt, start_idx=5, end_idx=10)

        # [When]
        result = api._merge_all(now, short_res, mid_res, air_data={})

        # [Then] D+4는 단기 폴백으로 채워져야 함 (15+4=19도)
        d4_entry = next(e for e in result["weather"]["forecast_daily"] if e["_day_index"] == 4)
        assert d4_entry["native_temperature"] is not None, "D+4 단기 폴백 기온이 None"


# ─────────────────────────────────────────────────────────────────────────────
# 6. API 실패 시 캐시 보존 로직 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestCachePreservesD3D4OnApiFailure:
    """네트워크 오류 등으로 API 응답이 없을 때 기존 데이터를 유지하는지 검증"""

    def test_mid_api_failure_uses_cache(self):
        api = make_api()
        now = datetime(2026, 4, 11, 19, 10, tzinfo=TZ)
        # 1차: 정상 데이터로 캐시 확보 (단기 4일치)
        api._merge_all(now, make_short_res_with_0915(now, days=4),
                       make_mid_res(api._get_mid_base_dt(now)), air_data={})

        # 2차: 중기 API None
        result = api._merge_all(now, make_short_res_with_0915(now, days=4),
                                None, air_data={})

        assert_d3_d4_not_none(result, "캐시 보존 확인")

    def test_short_api_failure_uses_cache(self):
        api = make_api()
        now = datetime(2026, 4, 11, 14, 10, tzinfo=TZ)
        # 1차: 캐시 확보
        api._merge_all(now, make_short_res_with_0915(now, days=4),
                       make_mid_res(api._get_mid_base_dt(now)), air_data={})

        # 2차: 단기 API 실패
        result = api._merge_all(now, None,
                                make_mid_res(api._get_mid_base_dt(now)), air_data={})

        assert_d3_d4_not_none(result, "단기 실패 캐시 보존")


# ─────────────────────────────────────────────────────────────────────────────
# 7. 연속 API 실패 시 캐시 생존 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestConsecutiveApiFailures:
    """API가 수 차례 연속으로 실패해도 데이터 유실이 없는지 검증"""

    def test_cache_survives_multiple_consecutive_failures(self):
        api = make_api()
        now = datetime(2026, 4, 11, 19, 10, tzinfo=TZ)
        # 최초 정상 데이터 (단기 4일치)
        api._merge_all(now, make_short_res_with_0915(now, days=4),
                       make_mid_res(api._get_mid_base_dt(now)), air_data={})

        # 3회 연속 실패
        for _ in range(3):
            result = api._merge_all(now, None, None, air_data={})
            assert_d3_d4_not_none(result, "연속 실패 중 데이터 유지")
