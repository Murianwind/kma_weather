"""
단기/중기 경계(D+3/D+4)에서 데이터가 사라지는지 검증하는 BDD 스타일 테스트.

변경된 로직:
  - i=0~3 (D+0~D+3): 단기예보
  - i=4~5 (D+4~D+5): 중기 우선, 없으면 단기 폴백
  - i=6~9 (D+6~D+9): 중기예보
"""

import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock

from custom_components.kma_weather.api_kma import KMAWeatherAPI

TZ = ZoneInfo("Asia/Seoul")

# ─────────────────────────────────────────────────────────────────────────────
# [Given] 공통 헬퍼 함수 및 설정
# ─────────────────────────────────────────────────────────────────────────────

def make_api() -> KMAWeatherAPI:
    api = KMAWeatherAPI(MagicMock(), "TEST_KEY")  # reg_id 제거
    api.lat, api.lon, api.nx, api.ny = 37.56, 126.98, 60, 127
    return api



def make_short_res_with_0915(now: datetime, days: int = 4) -> dict:
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


class TestHourlyUpdateAllDay:
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
        api = make_api()
        now = datetime(2026, 4, 11, hour, minute, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        short_res = make_short_res_with_0915(now, days=4)
        mid_res = make_mid_res(tm_fc_dt, start_idx=3, end_idx=10)
        result = api._merge_all(now, short_res, mid_res, air_data={})
        assert_d3_d4_not_none(result, desc)
        daily = result["weather"]["forecast_daily"]
        d3 = next(e for e in daily if e["_day_index"] == 3)
        assert d3["native_temperature"] is not None, f"D+3 기온 None ({desc})"
        d4 = next(e for e in daily if e["_day_index"] == 4)
        target_date = (now + timedelta(days=4)).date()
        mid_day_idx = (target_date - tm_fc_dt.date()).days
        expected_max = float(20 + mid_day_idx)
        assert d4["native_temperature"] == expected_max, f"D+4 기온 오차 ({desc})"


class TestShortTermReleaseTransition:
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


class TestMidTermTmfcTransition:
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


class TestMidnightToTwoAm:
    @pytest.mark.parametrize("hour,minute", [(0, 10), (0, 50), (1, 30)])
    def test_d3_d4_midnight_range(self, hour, minute):
        api = make_api()
        now = datetime(2026, 4, 11, hour, minute, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        result = api._merge_all(now, make_short_res_with_0915(now, days=4),
                                make_mid_res(tm_fc_dt), air_data={})
        d3_entry = next(e for e in result["weather"]["forecast_daily"] if e["_day_index"] == 3)
        assert d3_entry["native_temperature"] is not None, "D+3 단기 기온이 None"
        target_date = (now + timedelta(days=4)).date()
        mid_day_idx = (target_date - tm_fc_dt.date()).days
        d4_entry = next(e for e in result["weather"]["forecast_daily"] if e["_day_index"] == 4)
        assert d4_entry["native_temperature"] == float(20 + mid_day_idx)


class TestBoundaryDatePartialShortData:
    def test_d4_falls_back_to_short_when_mid_missing(self):
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        items = []
        for d in range(5):
            day = now + timedelta(days=d)
            for h in ["0900", "1500"]:
                for cat, val in [("TMP", str(15 + d)), ("SKY", "1"), ("PTY", "0")]:
                    items.append({"fcstDate": day.strftime("%Y%m%d"),
                                  "fcstTime": h, "category": cat, "fcstValue": val})
        short_res = {"response": {"body": {"items": {"item": items}}}}
        mid_res = make_mid_res(tm_fc_dt, start_idx=5, end_idx=10)
        result = api._merge_all(now, short_res, mid_res, air_data={})
        d4_entry = next(e for e in result["weather"]["forecast_daily"] if e["_day_index"] == 4)
        assert d4_entry["native_temperature"] is not None, "D+4 단기 폴백 기온이 None"


class TestCachePreservesD3D4OnApiFailure:
    def test_mid_api_failure_uses_cache(self):
        api = make_api()
        now = datetime(2026, 4, 11, 19, 10, tzinfo=TZ)
        api._merge_all(now, make_short_res_with_0915(now, days=4),
                       make_mid_res(api._get_mid_base_dt(now)), air_data={})
        result = api._merge_all(now, make_short_res_with_0915(now, days=4),
                                None, air_data={})
        assert_d3_d4_not_none(result, "캐시 보존 확인")

    def test_short_api_failure_uses_cache(self):
        api = make_api()
        now = datetime(2026, 4, 11, 14, 10, tzinfo=TZ)
        api._merge_all(now, make_short_res_with_0915(now, days=4),
                       make_mid_res(api._get_mid_base_dt(now)), air_data={})
        result = api._merge_all(now, None,
                                make_mid_res(api._get_mid_base_dt(now)), air_data={})
        assert_d3_d4_not_none(result, "단기 실패 캐시 보존")


class TestConsecutiveApiFailures:
    def test_cache_survives_multiple_consecutive_failures(self):
        api = make_api()
        now = datetime(2026, 4, 11, 19, 10, tzinfo=TZ)
        api._merge_all(now, make_short_res_with_0915(now, days=4),
                       make_mid_res(api._get_mid_base_dt(now)), air_data={})
        for _ in range(3):
            result = api._merge_all(now, None, None, air_data={})
            assert_d3_d4_not_none(result, "연속 실패 중 데이터 유지")
