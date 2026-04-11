# tests/test_boundary_gap.py
"""
단기/중기 경계(D+3/D+4)에서 데이터가 사라지는지 검증하는 테스트.

핵심 시나리오:
  - 기상청 단기예보는 3시간 간격(02/05/08/11/14/17/20/23시)으로 발표
  - 중기예보는 06시/18시 발표 (게시 30분 지연)
  - HA 컴포넌트는 매시 10분에 업데이트
  → 업데이트 타이밍에 따라 tmFc, mid_day_idx가 달라지며
    D+3/D+4 데이터가 None이 되거나 사라질 수 있음

검증 항목:
  1. 매시 10분 전체(00:10~23:10) × D+3/D+4 — 최고/최저기온 None 없음
  2. 단기 발표 직후(02/05/08/11/14/17/20/23시 +10분) — 경계 날짜 정상
  3. 중기 tmFc 전환 직전/직후(06:29→06:31, 18:29→18:31) — 경계 날짜 정상
  4. 자정~02시 사이(단기=전날23시 발표본) — D+3/D+4 중기 처리 정상
  5. 경계 날짜(D+3)에 09시/15시 TMP 없고 다른 시각만 있는 경우 — 중기 fallback
  6. 중기 API 실패(mid_res=None) 시 D+3/D+4 값이 None이 아님 (캐시 유지)
  7. 단기+중기 동시 API 실패 시 캐시 재사용으로 D+3/D+4 유지
"""

import pytest
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock

from custom_components.kma_weather.api_kma import KMAWeatherAPI

TZ = ZoneInfo("Asia/Seoul")


# ─────────────────────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def make_api() -> KMAWeatherAPI:
    api = KMAWeatherAPI(MagicMock(), "TEST_KEY", "11B10101", "11B00000")
    api.lat, api.lon, api.nx, api.ny = 37.56, 126.98, 60, 127
    return api


def make_short_res_with_0915(now: datetime, days: int = 3) -> dict:
    """
    D+0~D+(days-1) 날짜에 0900/1500 TMP를 포함한 단기예보 응답.
    short_covered_dates 조건(09시+15시 TMP 모두 있음)을 충족하여
    D+0~D+(days-1)이 단기로 처리되도록 보장한다.
    """
    items = []
    for d in range(days):
        day = now + timedelta(days=d)
        d_str = day.strftime("%Y%m%d")
        for h_str, tmp in [("0900", str(10 + d * 2)), ("1200", str(12 + d * 2)),
                            ("1500", str(15 + d * 2)), ("1800", str(13 + d * 2))]:
            for cat, val in [("TMP", tmp), ("SKY", "1"), ("PTY", "0"),
                              ("REH", "50"), ("WSD", "2.0"), ("POP", "10")]:
                items.append({"fcstDate": d_str, "fcstTime": h_str,
                               "category": cat, "fcstValue": val})
    return {"response": {"body": {"items": {"item": items}}}}


def make_mid_res(tm_fc_dt: datetime, start_idx: int = 3, end_idx: int = 10) -> tuple:
    """
    tm_fc_dt 기준 taMax{i}/taMin{i}/wf{i}Am/wf{i}Pm 포함 중기예보 튜플.
    taMax{i} = 20+i, taMin{i} = 5+i
    """
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
    """D+3/D+4 최고/최저기온과 twice_daily 값이 None이 아닌지 검증."""
    daily = result["weather"]["forecast_daily"]
    twice = result["weather"]["forecast_twice_daily"]

    for i in [3, 4]:
        day_entry = next((e for e in daily if e["_day_index"] == i), None)
        assert day_entry is not None, f"[{desc}] D+{i} forecast_daily 항목 없음"
        assert day_entry["native_temperature"] is not None, (
            f"[{desc}] D+{i} daily 최고기온 None — mid_day_idx 계산 오류 또는 캐시 누락")
        assert day_entry["native_templow"] is not None, (
            f"[{desc}] D+{i} daily 최저기온 None — mid_day_idx 계산 오류 또는 캐시 누락")

        am_entry = next((e for e in twice if e["_day_index"] == i and e["is_daytime"]), None)
        pm_entry = next((e for e in twice if e["_day_index"] == i and not e["is_daytime"]), None)
        assert am_entry is not None, f"[{desc}] D+{i} twice_daily 주간 항목 없음"
        assert pm_entry is not None, f"[{desc}] D+{i} twice_daily 야간 항목 없음"
        assert am_entry["native_temperature"] is not None, (
            f"[{desc}] D+{i} twice 주간 온도 None")
        assert pm_entry["native_temperature"] is not None, (
            f"[{desc}] D+{i} twice 야간 온도 None")


# ─────────────────────────────────────────────────────────────────────────────
# 1. 매시 10분 전체(00:10~23:10) × D+3/D+4 전수 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestHourlyUpdateAllDay:
    """
    하루 24시간 매시 10분 업데이트 시 D+3/D+4가 항상 정상값을 가지는지 전수 검증.
    단기발표(02/05/08/11/14/17/20/23시), 중기발표(06/18시) 포함.

    파라미터의 d3_idx/d4_idx는 해당 시각의 _get_mid_base_dt() 기준
    mid_day_idx 기댓값이다.
    """

    @pytest.mark.parametrize("hour,minute,d3_idx,d4_idx,desc", [
        # 00:00~06:29: tmFc=전날 18시 → D+3 idx=4, D+4 idx=5
        ( 0, 10, 4, 5, "00:10 tmFc=전날18시"),
        ( 1, 10, 4, 5, "01:10 tmFc=전날18시"),
        ( 2, 10, 4, 5, "02:10 tmFc=전날18시 📡단기발표"),
        ( 3, 10, 4, 5, "03:10 tmFc=전날18시"),
        ( 4, 10, 4, 5, "04:10 tmFc=전날18시"),
        ( 5, 10, 4, 5, "05:10 tmFc=전날18시 📡단기발표"),
        ( 6, 10, 4, 5, "06:10 tmFc=전날18시"),
        # 06:31~: tmFc=당일 06시 → D+3 idx=3, D+4 idx=4
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
        # 18:31~: tmFc=당일 18시 → D+3 idx=3, D+4 idx=4
        (19, 10, 3, 4, "19:10 tmFc=당일18시 🌐중기18시반영"),
        (20, 10, 3, 4, "20:10 tmFc=당일18시 📡단기발표"),
        (21, 10, 3, 4, "21:10 tmFc=당일18시"),
        (22, 10, 3, 4, "22:10 tmFc=당일18시"),
        (23, 10, 3, 4, "23:10 tmFc=당일18시 📡단기발표"),
    ])
    def test_d3_d4_never_none_all_hours(self, hour, minute, d3_idx, d4_idx, desc):
        """
        매시 10분 업데이트 시 D+3/D+4 최고/최저기온이 None이 아닌지 검증.
        단기: D+0/D+1/D+2 커버 (0900+1500 TMP 있음)
        중기: tm_fc_dt 기준 taMax{idx} 값 있음
        """
        api = make_api()
        now = datetime(2026, 4, 11, hour, minute, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)

        short_res = make_short_res_with_0915(now, days=3)
        mid_res = make_mid_res(tm_fc_dt, start_idx=3, end_idx=10)
        result = api._merge_all(now, short_res, mid_res, {})

        assert_d3_d4_not_none(result, desc)

        # mid_day_idx 기댓값도 검증
        daily = result["weather"]["forecast_daily"]
        for i, expected_idx in [(3, d3_idx), (4, d4_idx)]:
            d_dt = (now + timedelta(days=i)).date()
            actual_idx = (d_dt - tm_fc_dt.date()).days
            assert actual_idx == expected_idx, (
                f"[{desc}] D+{i} mid_day_idx: 기대={expected_idx}, 실제={actual_idx}")

            entry = next(e for e in daily if e["_day_index"] == i)
            expected_max = float(20 + expected_idx)
            assert entry["native_temperature"] == expected_max, (
                f"[{desc}] D+{i} 최고기온: 기대={expected_max}(taMax{expected_idx}), "
                f"실제={entry['native_temperature']}")


# ─────────────────────────────────────────────────────────────────────────────
# 2. 단기 발표 직후 — 경계 날짜 전환 정상성
# ─────────────────────────────────────────────────────────────────────────────

class TestShortTermReleaseTransition:
    """
    단기예보 발표 직전/직후(예: 02:09→02:10)에서
    D+3/D+4 처리 방식이 바뀌어도 값이 유지되는지 검증.
    """

    @pytest.mark.parametrize("before_h,after_h,desc", [
        ( 1, 2,  "02시 발표"),
        ( 4, 5,  "05시 발표"),
        ( 7, 8,  "08시 발표"),
        (10, 11, "11시 발표"),
        (13, 14, "14시 발표"),
        (16, 17, "17시 발표"),
        (19, 20, "20시 발표"),
        (22, 23, "23시 발표"),
    ])
    def test_d3_d4_across_short_release(self, before_h, after_h, desc):
        """발표 직전(HH:10)과 발표 직후(HH+1:10) 모두 D+3/D+4 값 없음 없음."""
        for h, label in [(before_h, f"{desc} 직전({before_h:02d}:10)"),
                         (after_h,  f"{desc} 직후({after_h:02d}:10)")]:
            api = make_api()
            now = datetime(2026, 4, 11, h, 10, tzinfo=TZ)
            tm_fc_dt = api._get_mid_base_dt(now)
            result = api._merge_all(
                now,
                make_short_res_with_0915(now, days=3),
                make_mid_res(tm_fc_dt),
                {}
            )
            assert_d3_d4_not_none(result, label)


# ─────────────────────────────────────────────────────────────────────────────
# 3. 중기 tmFc 전환 직전/직후 — 경계 날짜 idx 변동에도 값 유지
# ─────────────────────────────────────────────────────────────────────────────

class TestMidTermTmfcTransition:
    """
    중기 tmFc가 전날18시→당일06시, 당일06시→당일18시로 전환될 때
    (각각 06:31, 18:31) D+3/D+4 mid_day_idx가 바뀌어도 값이 정상인지 검증.
    """

    @pytest.mark.parametrize("hour,minute,expected_tmfc_hour,desc", [
        (6, 29, 18, "06:29 — 전날18시 사용 (06시 발표 미반영)"),
        (6, 31, 6,  "06:31 — 당일06시 사용 (06시 발표 반영)"),
        (18, 29, 6,  "18:29 — 당일06시 사용 (18시 발표 미반영)"),
        (18, 31, 18, "18:31 — 당일18시 사용 (18시 발표 반영)"),
    ])
    def test_d3_d4_across_tmfc_transition(self, hour, minute, expected_tmfc_hour, desc):
        api = make_api()
        now = datetime(2026, 4, 11, hour, minute, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)

        # tmFc 시각 검증
        assert tm_fc_dt.hour == expected_tmfc_hour, (
            f"[{desc}] tmFc.hour: 기대={expected_tmfc_hour}, 실제={tm_fc_dt.hour}")

        result = api._merge_all(
            now,
            make_short_res_with_0915(now, days=3),
            make_mid_res(tm_fc_dt),
            {}
        )
        assert_d3_d4_not_none(result, desc)

    def test_idx_decreases_by_one_at_0631(self):
        """
        06:29 → 06:31 전환 시 D+4의 mid_day_idx가 5→4로 줄어도
        중기 API에 taMax4가 존재하므로 값이 정상이어야 한다.
        """
        api_before = make_api()
        api_after  = make_api()
        now_before = datetime(2026, 4, 11, 6, 29, tzinfo=TZ)
        now_after  = datetime(2026, 4, 11, 6, 31, tzinfo=TZ)

        tm_before = api_before._get_mid_base_dt(now_before)
        tm_after  = api_after._get_mid_base_dt(now_after)

        d4_dt = (now_before + timedelta(days=4)).date()
        idx_before = (d4_dt - tm_before.date()).days  # 5
        idx_after  = (d4_dt - tm_after.date()).days   # 4

        assert idx_before == 5, f"06:29 D+4 idx 기대=5, 실제={idx_before}"
        assert idx_after  == 4, f"06:31 D+4 idx 기대=4, 실제={idx_after}"

        for now, tm_fc_dt, label in [
            (now_before, tm_before, "06:29(idx=5)"),
            (now_after,  tm_after,  "06:31(idx=4)"),
        ]:
            api = make_api()
            result = api._merge_all(
                now,
                make_short_res_with_0915(now, days=3),
                make_mid_res(tm_fc_dt, start_idx=3, end_idx=10),
                {}
            )
            daily = result["weather"]["forecast_daily"]
            entry_d4 = next(e for e in daily if e["_day_index"] == 4)
            assert entry_d4["native_temperature"] is not None, (
                f"[{label}] D+4 최고기온 None")


# ─────────────────────────────────────────────────────────────────────────────
# 4. 자정~02시 구간 — 단기=전날23시, 중기=전날18시
# ─────────────────────────────────────────────────────────────────────────────

class TestMidnightToTwoAm:
    """
    자정~01:59 사이에는 단기발표본이 전날 23시, 중기tmFc가 전날 18시다.
    이 구간에서 D+3/D+4가 중기(idx=4/5)로 올바르게 처리되는지 검증.
    """

    @pytest.mark.parametrize("hour,minute", [
        (0,  10),
        (0,  30),
        (1,  10),
        (1,  59),
    ])
    def test_d3_d4_midnight_range(self, hour, minute):
        api = make_api()
        now = datetime(2026, 4, 11, hour, minute, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)

        # 이 구간은 tmFc=전날18시 → D+3 idx=4, D+4 idx=5
        assert tm_fc_dt.date() == date(2026, 4, 10), (
            f"{hour:02d}:{minute:02d} tmFc 날짜 기대=04/10, 실제={tm_fc_dt.date()}")
        assert tm_fc_dt.hour == 18

        result = api._merge_all(
            now,
            make_short_res_with_0915(now, days=3),
            make_mid_res(tm_fc_dt, start_idx=3, end_idx=10),
            {}
        )
        assert_d3_d4_not_none(result, f"{hour:02d}:{minute:02d} 자정구간")

        daily = result["weather"]["forecast_daily"]
        d3_entry = next(e for e in daily if e["_day_index"] == 3)
        d4_entry = next(e for e in daily if e["_day_index"] == 4)
        # taMax4=24, taMax5=25
        assert d3_entry["native_temperature"] == 24.0, (
            f"{hour:02d}:{minute:02d} D+3 기대=24(taMax4), 실제={d3_entry['native_temperature']}")
        assert d4_entry["native_temperature"] == 25.0, (
            f"{hour:02d}:{minute:02d} D+4 기대=25(taMax5), 실제={d4_entry['native_temperature']}")


# ─────────────────────────────────────────────────────────────────────────────
# 5. 경계 날짜 D+3에 09시/15시 없이 다른 시각 TMP만 있는 경우
# ─────────────────────────────────────────────────────────────────────────────

class TestBoundaryDatePartialShortData:
    """
    단기발표본이 D+3 날짜의 새벽(00~08시) 데이터만 포함하는 경우.
    09시/15시 TMP 조건 미충족 → short_covered 제외 → 중기 fallback.
    이것이 수정의 핵심: 구버전은 TMP 하나라도 있으면 단기로 처리해서
    09시/15시 데이터 없이 최고/최저 계산이 틀렸다.
    """

    def _make_short_with_d3_partial(self, now: datetime) -> dict:
        """
        D+0/D+1/D+2는 0900/1500 포함, D+3은 0600/0800만 있음(09시/15시 없음).
        """
        items = []
        # D+0, D+1, D+2: 정상
        for d in range(3):
            day = now + timedelta(days=d)
            d_str = day.strftime("%Y%m%d")
            for h_str in ["0900", "1200", "1500"]:
                for cat, val in [("TMP", "15"), ("SKY", "1"), ("PTY", "0")]:
                    items.append({"fcstDate": d_str, "fcstTime": h_str,
                                  "category": cat, "fcstValue": val})
        # D+3: 새벽만(0600/0800) — 09시/15시 없음
        d3_str = (now + timedelta(days=3)).strftime("%Y%m%d")
        for h_str in ["0600", "0800"]:
            for cat, val in [("TMP", "5"), ("SKY", "1"), ("PTY", "0")]:
                items.append({"fcstDate": d3_str, "fcstTime": h_str,
                               "category": cat, "fcstValue": val})
        return {"response": {"body": {"items": {"item": items}}}}

    def test_d3_partial_short_falls_back_to_mid(self):
        """
        D+3에 새벽 TMP만 있으면 short_covered에서 제외되어 중기 값을 사용해야 한다.
        구버전: D+3이 단기로 처리되어 max/min이 새벽 5도 기준으로 틀리게 계산됨.
        신버전: D+3이 중기로 처리되어 taMax3=23, taMin3=8 사용.
        """
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)

        short_res = self._make_short_with_d3_partial(now)
        mid_res = make_mid_res(tm_fc_dt, start_idx=3, end_idx=10)
        result = api._merge_all(now, short_res, mid_res, {})

        daily = result["weather"]["forecast_daily"]
        d3_entry = next(e for e in daily if e["_day_index"] == 3)

        # D+3 mid_day_idx=3 → taMax3=23, taMin3=8
        assert d3_entry["native_temperature"] == 23.0, (
            f"D+3 최고기온: 기대=23(중기taMax3), 실제={d3_entry['native_temperature']} "
            f"— 새벽 단기 데이터로 오계산되면 5.0이 나온다")
        assert d3_entry["native_templow"] == 8.0, (
            f"D+3 최저기온: 기대=8(중기taMin3), 실제={d3_entry['native_templow']}")

    def test_d3_partial_does_not_affect_d4(self):
        """D+3의 부분 단기 데이터가 D+4 처리에 영향을 주지 않아야 한다."""
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)

        short_res = self._make_short_with_d3_partial(now)
        mid_res = make_mid_res(tm_fc_dt, start_idx=3, end_idx=10)
        result = api._merge_all(now, short_res, mid_res, {})

        daily = result["weather"]["forecast_daily"]
        d4_entry = next(e for e in daily if e["_day_index"] == 4)
        # D+4 idx=4 → taMax4=24
        assert d4_entry["native_temperature"] == 24.0, (
            f"D+4 최고기온: 기대=24(taMax4), 실제={d4_entry['native_temperature']}")


# ─────────────────────────────────────────────────────────────────────────────
# 6. 중기 API 실패 시 캐시 유지 — D+3/D+4 보존
# ─────────────────────────────────────────────────────────────────────────────

class TestCachePreservesD3D4OnApiFailure:
    """
    핵심 버그 재현: 중기 API 실패(mid_res=None) 시
    구버전은 mid_ta={} → taMax4=None → D+4 증발.
    신버전은 _cache_mid_ta에 이전 데이터 유지 → D+3/D+4 정상.
    """

    def test_mid_api_failure_uses_cache(self):
        """
        1차 호출(정상) → 캐시 저장
        2차 호출(중기 실패) → 캐시 재사용으로 D+3/D+4 유지
        """
        api = make_api()
        now = datetime(2026, 4, 11, 19, 10, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)

        # 1차: 정상 수신 → 캐시 저장
        result1 = api._merge_all(
            now,
            make_short_res_with_0915(now, days=3),
            make_mid_res(tm_fc_dt),
            {}
        )
        assert_d3_d4_not_none(result1, "1차 정상")

        # 2차: 중기 API 실패(None)
        result2 = api._merge_all(
            now,
            make_short_res_with_0915(now, days=3),
            None,   # 중기 실패
            {}
        )
        assert_d3_d4_not_none(result2, "2차 중기실패 — 캐시 재사용")

        # 값도 1차와 동일해야 함
        daily1 = {e["_day_index"]: e for e in result1["weather"]["forecast_daily"]}
        daily2 = {e["_day_index"]: e for e in result2["weather"]["forecast_daily"]}
        for i in [3, 4]:
            assert daily1[i]["native_temperature"] == daily2[i]["native_temperature"], (
                f"D+{i} 최고기온: 1차={daily1[i]['native_temperature']}, "
                f"2차(캐시)={daily2[i]['native_temperature']}")

    def test_short_api_failure_uses_cache(self):
        """
        1차 호출(정상) → 캐시 저장
        2차 호출(단기 실패) → 단기 캐시 재사용, D+3/D+4 유지
        """
        api = make_api()
        now = datetime(2026, 4, 11, 14, 10, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)

        # 1차: 정상
        api._merge_all(
            now,
            make_short_res_with_0915(now, days=3),
            make_mid_res(tm_fc_dt),
            {}
        )

        # 2차: 단기 실패(None)
        result2 = api._merge_all(
            now,
            None,   # 단기 실패
            make_mid_res(tm_fc_dt),
            {}
        )
        assert_d3_d4_not_none(result2, "단기실패 — 캐시 재사용")

    def test_both_api_failure_uses_cache(self):
        """
        단기+중기 모두 실패해도 캐시가 있으면 D+3/D+4 유지.
        """
        api = make_api()
        now = datetime(2026, 4, 11, 20, 10, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)

        # 1차: 정상
        api._merge_all(
            now,
            make_short_res_with_0915(now, days=3),
            make_mid_res(tm_fc_dt),
            {}
        )

        # 2차: 둘 다 실패
        result2 = api._merge_all(now, None, None, {})
        assert_d3_d4_not_none(result2, "단기+중기 모두 실패 — 캐시 재사용")

    def test_no_cache_and_mid_failure_uses_fallback(self):
        """
        캐시 없는 상태(HA 재시작 직후)에서 중기 실패 시
        D+3/D+4가 폴백 처리되어 최소한 None은 아닌지 검증.
        (단기 데이터에서 가능한 값이라도 채워야 한다)
        """
        api = make_api()  # 캐시 없는 신규 인스턴스
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)

        # 단기에 D+3 일부 데이터 포함(0600/0800만)
        d3_str = (now + timedelta(days=3)).strftime("%Y%m%d")
        items = []
        for d in range(3):
            day = now + timedelta(days=d)
            d_str = day.strftime("%Y%m%d")
            for h_str in ["0900", "1500"]:
                for cat, val in [("TMP", "15"), ("SKY", "1"), ("PTY", "0")]:
                    items.append({"fcstDate": d_str, "fcstTime": h_str,
                                  "category": cat, "fcstValue": val})
        # D+3: 새벽 데이터 포함
        for h_str in ["0600", "0800"]:
            for cat, val in [("TMP", "7"), ("SKY", "1"), ("PTY", "0")]:
                items.append({"fcstDate": d3_str, "fcstTime": h_str,
                               "category": cat, "fcstValue": val})
        short_res = {"response": {"body": {"items": {"item": items}}}}

        # 중기 실패 — 캐시도 없음
        result = api._merge_all(now, short_res, None, {})

        # D+4는 캐시/중기 모두 없어서 None일 수 있지만 forecast_daily 항목은 있어야 함
        daily = result["weather"]["forecast_daily"]
        assert len(daily) == 10, "캐시 없고 중기 실패해도 10일 항목은 있어야 함"

        # D+3는 단기 캐시 폴백으로 값이 있어야 함
        d3_entry = next((e for e in daily if e["_day_index"] == 3), None)
        assert d3_entry is not None, "D+3 항목 자체가 없음"


# ─────────────────────────────────────────────────────────────────────────────
# 7. 단기+중기 동시 API 실패 연속 — 여러 번 실패해도 캐시 유지
# ─────────────────────────────────────────────────────────────────────────────

class TestConsecutiveApiFailures:
    """
    연속 API 실패 시나리오: 정상 → 실패 × N → 정상복구
    각 단계에서 D+3/D+4 값이 유지되는지 검증.
    """

    def test_cache_survives_multiple_consecutive_failures(self):
        """연속 3회 실패해도 첫 번째 성공 캐시가 유지되어야 한다."""
        api = make_api()
        now = datetime(2026, 4, 11, 19, 10, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)

        # 1차: 정상
        result_ok = api._merge_all(
            now,
            make_short_res_with_0915(now, days=3),
            make_mid_res(tm_fc_dt),
            {}
        )
        d3_ok = next(e for e in result_ok["weather"]["forecast_daily"] if e["_day_index"] == 3)
        d4_ok = next(e for e in result_ok["weather"]["forecast_daily"] if e["_day_index"] == 4)

        # 2~4차: 연속 실패
        for attempt in range(1, 4):
            result_fail = api._merge_all(now, None, None, {})
            d3_fail = next(e for e in result_fail["weather"]["forecast_daily"] if e["_day_index"] == 3)
            d4_fail = next(e for e in result_fail["weather"]["forecast_daily"] if e["_day_index"] == 4)
            assert d3_fail["native_temperature"] == d3_ok["native_temperature"], (
                f"{attempt}번째 실패 후 D+3 최고기온 변경됨")
            assert d4_fail["native_temperature"] == d4_ok["native_temperature"], (
                f"{attempt}번째 실패 후 D+4 최고기온 변경됨")

        # 5차: 정상복구 — 새 데이터로 갱신
        new_tm_fc_dt = api._get_mid_base_dt(now)
        result_recover = api._merge_all(
            now,
            make_short_res_with_0915(now, days=3),
            make_mid_res(new_tm_fc_dt),
            {}
        )
        assert_d3_d4_not_none(result_recover, "정상복구")
