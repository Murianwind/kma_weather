# tests/test_mid_term_forecast.py
"""
단기·중기 예보 연결 로직 검증 테스트

검증 항목:
  1. _get_mid_base_dt — 시간대별 tmFc 발표 기준 datetime 계산 (30분 게시 지연 포함)
  2. _merge_all — 단기/중기 날짜 분기 (short_covered_dates)
  3. _merge_all — 중기 mid_day_idx = (target_date - tm_fc_dt.date()).days 정확성
  4. _merge_all — 10일치 forecast_daily/twice_daily 연속성 (None 포함)
  5. _merge_all — 단기·중기 중복 날짜 처리 (단기 우선)
  6. _get_mid_term — 반환 튜플 구조 (ta, land, tm_fc_dt)
  7. 경계 시각별 전체 시나리오 (오전 5:50, 오전 10:00, 오후 18:30, 자정 0:10)
"""

import pytest
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.kma_weather.api_kma import KMAWeatherAPI

TZ = ZoneInfo("Asia/Seoul")


# ─────────────────────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def make_api() -> KMAWeatherAPI:
    api = KMAWeatherAPI(MagicMock(), "TEST_KEY", "11B10101", "11B00000")
    api.lat, api.lon, api.nx, api.ny = 37.56, 126.98, 60, 127
    return api


def make_short_res(base_date: datetime, days: int = 3) -> dict:
    """
    base_date 당일부터 days일치 단기예보 응답을 생성한다.
    각 날짜에 0600~2100 시간대 TMP 데이터를 포함한다.
    """
    items = []
    for d in range(days):
        day = base_date + timedelta(days=d)
        d_str = day.strftime("%Y%m%d")
        for h in range(6, 22, 3):
            t_str = f"{h:02d}00"
            tmp_val = str(10 + d * 2 + h // 3)  # 날짜·시간마다 다른 온도
            sky_val = "1" if h < 15 else "3"
            for cat, val in [("TMP", tmp_val), ("SKY", sky_val), ("PTY", "0"),
                              ("REH", "50"), ("WSD", "2.0"), ("POP", "10")]:
                items.append({"fcstDate": d_str, "fcstTime": t_str,
                               "category": cat, "fcstValue": val})
        # 0900, 1500 슬롯 명시 (wf_am/pm 추출용)
        for t_str, sky in [("0900", "1"), ("1500", "3")]:
            for cat, val in [("SKY", sky), ("PTY", "0")]:
                items.append({"fcstDate": d_str, "fcstTime": t_str,
                               "category": cat, "fcstValue": val})
    return {"response": {"body": {"items": {"item": items}}}}


def make_mid_res(tm_fc_dt: datetime, start_idx: int = 3, end_idx: int = 10) -> tuple:
    """
    tm_fc_dt 기준으로 taMax{i}/taMin{i}/wf{i}Am/wf{i}Pm 키를 포함한 중기예보 튜플을 생성한다.
    온도: taMax{i} = 20+i, taMin{i} = 5+i
    날씨: 짝수 인덱스 맑음, 홀수 인덱스 흐림
    """
    ta_item = {}
    land_item = {}
    for i in range(start_idx, end_idx + 1):
        ta_item[f"taMax{i}"] = str(20 + i)
        ta_item[f"taMin{i}"] = str(5 + i)
        wf = "맑음" if i % 2 == 0 else "흐림"
        land_item[f"wf{i}Am"] = wf
        land_item[f"wf{i}Pm"] = wf

    def wrap(item):
        return {"response": {"body": {"items": {"item": [item]}}}}

    return (wrap(ta_item), wrap(land_item), tm_fc_dt)


# ─────────────────────────────────────────────────────────────────────────────
# 1. _get_mid_base_dt — 발표 기준 datetime 계산
# ─────────────────────────────────────────────────────────────────────────────

class TestGetMidBaseDt:
    """
    _get_mid_base_dt()가 시간대별로 올바른 tmFc datetime을 반환하는지 검증한다.
    30분 게시 지연: effective = now - 30분
    """

    @pytest.mark.parametrize("hour,minute,expected_date_offset,expected_hour,desc", [
        # effective = 05:29 → < 06:00 → 전날 18:00
        (5,  59, -1, 18, "06시 발표 30분 전 → 전날 18시"),
        # effective = 06:00 → >= 06:00, < 18:00 → 당일 06:00
        (6,  30, 0,  6,  "06:30 (06시 발표 직후) → 오늘 06시"),
        # effective = 11:00 → >= 06:00, < 18:00 → 당일 06:00
        (11, 30, 0,  6,  "낮 11:30 → 오늘 06시"),
        # effective = 17:59 → >= 06:00, < 18:00 → 당일 06:00
        (17, 59, 0,  6,  "17:59 → 오늘 06시 (18시 발표 미게시)"),
        # effective = 18:00 → >= 18:00 → 당일 18:00
        (18, 30, 0,  18, "18:30 (18시 발표 직후) → 오늘 18시"),
        # effective = 23:00 → >= 18:00 → 당일 18:00
        (23, 30, 0,  18, "23:30 → 오늘 18시"),
        # effective = 00:00 → < 06:00 → 전날 18:00
        (0,  30, -1, 18, "자정 00:30 → 전날 18시"),
    ])
    def test_tmfc_calculation(self, hour, minute, expected_date_offset, expected_hour, desc):
        api = make_api()
        now = datetime(2026, 4, 11, hour, minute, tzinfo=TZ)
        result = api._get_mid_base_dt(now)

        expected_date = (now - timedelta(minutes=30)).date() + timedelta(days=expected_date_offset)
        # 전날로 넘어가는 경우 날짜 보정
        if hour == 0 and minute == 30:
            expected_date = now.date() - timedelta(days=1)
        elif hour == 5 and minute == 59:
            # effective = 05:29 → 전날
            expected_date = now.date() - timedelta(days=1)

        assert result.hour == expected_hour, f"[{desc}] 기대 시각={expected_hour}시, 실제={result.hour}시"
        assert result.date() == expected_date, f"[{desc}] 기대 날짜={expected_date}, 실제={result.date()}"
        assert result.minute == 0 and result.second == 0, "분·초는 항상 0이어야 한다"

    def test_returns_datetime_with_timezone(self):
        """반환값이 timezone-aware datetime인지 확인"""
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        result = api._get_mid_base_dt(now)
        assert result.tzinfo is not None, "반환값은 timezone-aware이어야 한다"

    def test_boundary_exactly_0630(self):
        """06:30 정각: effective=06:00 → 오늘 06시"""
        api = make_api()
        now = datetime(2026, 4, 11, 6, 30, tzinfo=TZ)
        result = api._get_mid_base_dt(now)
        assert result.date() == now.date()
        assert result.hour == 6

    def test_boundary_exactly_0600(self):
        """06:00 정각: effective=05:30 → 전날 18시"""
        api = make_api()
        now = datetime(2026, 4, 11, 6, 0, tzinfo=TZ)
        result = api._get_mid_base_dt(now)
        assert result.date() == now.date() - timedelta(days=1)
        assert result.hour == 18

    def test_boundary_exactly_1830(self):
        """18:30 정각: effective=18:00 → 오늘 18시"""
        api = make_api()
        now = datetime(2026, 4, 11, 18, 30, tzinfo=TZ)
        result = api._get_mid_base_dt(now)
        assert result.date() == now.date()
        assert result.hour == 18

    def test_boundary_exactly_1800(self):
        """18:00 정각: effective=17:30 → 오늘 06시"""
        api = make_api()
        now = datetime(2026, 4, 11, 18, 0, tzinfo=TZ)
        result = api._get_mid_base_dt(now)
        assert result.date() == now.date()
        assert result.hour == 6


# ─────────────────────────────────────────────────────────────────────────────
# 2. _get_mid_term — 반환 튜플 구조
# ─────────────────────────────────────────────────────────────────────────────

class TestGetMidTerm:
    @pytest.mark.asyncio
    async def test_returns_three_tuple(self):
        """_get_mid_term()이 (ta응답, land응답, tm_fc_dt) 3-튜플을 반환하는지 검증"""
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)

        mock_resp = {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": []}}}}

        async def mock_fetch(url, params, **kwargs):
            return mock_resp

        api._fetch = mock_fetch
        result = await api._get_mid_term(now)

        assert isinstance(result, tuple), "반환값은 tuple이어야 한다"
        assert len(result) == 3, "튜플 길이는 3이어야 한다"
        ta_res, land_res, tm_fc_dt = result
        assert isinstance(tm_fc_dt, datetime), "세 번째 원소는 datetime이어야 한다"
        assert tm_fc_dt.tzinfo is not None, "tm_fc_dt는 timezone-aware이어야 한다"

    @pytest.mark.asyncio
    async def test_tmfc_format_matches_api_param(self):
        """_get_mid_term()이 _get_mid_base_dt()와 동일한 tmFc로 API를 호출하는지 검증"""
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        expected_tm_fc_dt = api._get_mid_base_dt(now)
        expected_base = expected_tm_fc_dt.strftime("%Y%m%d%H%M")

        called_params = []

        async def mock_fetch(url, params, **kwargs):
            called_params.append(params.get("tmFc"))
            return {}

        api._fetch = mock_fetch
        await api._get_mid_term(now)

        assert len(called_params) == 2, "getMidTa와 getMidLandFcst 두 번 호출되어야 한다"
        for p in called_params:
            assert p == expected_base, f"tmFc 파라미터가 불일치: 기대={expected_base}, 실제={p}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. _merge_all — mid_day_idx 정확성 (핵심)
# ─────────────────────────────────────────────────────────────────────────────

class TestMidDayIndexCalculation:
    """
    중기예보 mid_day_idx = (target_date - tm_fc_dt.date()).days 로직 검증.
    단기예보가 today+0,1,2를 커버할 때 today+3부터 taMax3, taMax4... 가 매핑되어야 한다.
    """

    def _run_merge(self, now: datetime, short_days: int = 3) -> dict:
        """short_days일치 단기예보 + 중기예보로 _merge_all을 실행하고 결과 반환"""
        api = make_api()
        tm_fc_dt = api._get_mid_base_dt(now)
        short_res = make_short_res(now, days=short_days)
        mid_res = make_mid_res(tm_fc_dt, start_idx=3, end_idx=10)
        return api._merge_all(now, short_res, mid_res, {})

    @pytest.mark.parametrize("now_hour,now_minute,desc", [
        (10,  0,  "오전 10:00 → tm_fc=오늘 06시"),
        (19,  0,  "오후 19:00 → tm_fc=오늘 18시"),
        (5,  50,  "오전 5:50 → tm_fc=전날 18시 (경계)"),
        (0,  30,  "자정 0:30 → tm_fc=전날 18시"),
    ])
    def test_mid_day_idx_for_day_3_to_6(self, now_hour, now_minute, desc):
        """
        단기예보가 today+0,1,2를 커버할 때,
        today+3~6의 온도가 중기예보 taMax3~6과 정확히 일치하는지 검증한다.
        """
        now = datetime(2026, 4, 11, now_hour, now_minute, tzinfo=TZ)
        api = make_api()
        tm_fc_dt = api._get_mid_base_dt(now)
        short_res = make_short_res(now, days=3)
        mid_res = make_mid_res(tm_fc_dt, start_idx=3, end_idx=10)
        result = api._merge_all(now, short_res, mid_res, {})

        daily = result["weather"]["forecast_daily"]
        twice = result["weather"]["forecast_twice_daily"]

        for i in range(3, 7):
            target_date = (now + timedelta(days=i)).date()
            mid_day_idx = (target_date - tm_fc_dt.date()).days
            expected_max = 20 + mid_day_idx
            expected_min = 5 + mid_day_idx

            # forecast_daily 검증
            day_entry = next((e for e in daily if e["_day_index"] == i), None)
            assert day_entry is not None, f"[{desc}] i={i} forecast_daily 항목이 없음"
            assert day_entry["native_temperature"] == float(expected_max), (
                f"[{desc}] i={i} daily max: 기대={expected_max}, 실제={day_entry['native_temperature']}")
            assert day_entry["native_templow"] == float(expected_min), (
                f"[{desc}] i={i} daily min: 기대={expected_min}, 실제={day_entry['native_templow']}")

            # forecast_twice_daily 주간 검증
            am_entry = next((e for e in twice if e["_day_index"] == i and e["is_daytime"]), None)
            assert am_entry is not None, f"[{desc}] i={i} twice_daily 주간 항목이 없음"
            assert am_entry["native_temperature"] == float(expected_max), (
                f"[{desc}] i={i} twice max: 기대={expected_max}, 실제={am_entry['native_temperature']}")

    def test_no_gap_between_short_and_mid(self):
        """단기→중기 전환 지점(today+2→today+3)에서 온도 값이 끊기지 않는지 검증"""
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        result = self._run_merge(now, short_days=3)
        daily = result["weather"]["forecast_daily"]

        for i in range(6):  # 0~5일차 모두 값이 있어야 함
            entry = next((e for e in daily if e["_day_index"] == i), None)
            assert entry is not None, f"i={i} forecast_daily 항목 없음"
            assert entry["native_temperature"] is not None, f"i={i} native_temperature가 None"
            assert entry["native_templow"] is not None, f"i={i} native_templow가 None"

    def test_short_term_priority_over_mid(self):
        """단기예보가 커버하는 날짜는 중기예보 값이 아닌 단기예보 값을 사용해야 한다"""
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        api = make_api()
        tm_fc_dt = api._get_mid_base_dt(now)

        # 단기예보: today+0,1,2 → TMP=100 (중기예보와 구별되는 큰 값)
        items = []
        for d in range(3):
            day = now + timedelta(days=d)
            d_str = day.strftime("%Y%m%d")
            for h in [6, 9, 12, 15, 18]:
                t_str = f"{h:02d}00"
                for cat, val in [("TMP", "100"), ("SKY", "1"), ("PTY", "0"),
                                  ("REH", "50"), ("WSD", "1"), ("POP", "0")]:
                    items.append({"fcstDate": d_str, "fcstTime": t_str,
                                   "category": cat, "fcstValue": val})
        short_res = {"response": {"body": {"items": {"item": items}}}}
        mid_res = make_mid_res(tm_fc_dt)
        result = api._merge_all(now, short_res, mid_res, {})

        daily = result["weather"]["forecast_daily"]
        for i in range(3):
            entry = next(e for e in daily if e["_day_index"] == i)
            assert entry["native_temperature"] == 100.0, (
                f"i={i}: 단기예보 값(100)이 아닌 중기예보 값({entry['native_temperature']})이 사용됨")

        # today+3 이후는 중기예보 값이어야 함
        for i in range(3, 7):
            entry = next(e for e in daily if e["_day_index"] == i)
            mid_day_idx = ((now + timedelta(days=i)).date() - tm_fc_dt.date()).days
            assert entry["native_temperature"] == float(20 + mid_day_idx), (
                f"i={i}: 중기예보 값이 아님 → {entry['native_temperature']}")


# ─────────────────────────────────────────────────────────────────────────────
# 4. _merge_all — 10일 연속성
# ─────────────────────────────────────────────────────────────────────────────

class TestForecastContinuity:
    """forecast_daily와 forecast_twice_daily가 항상 10일/20개인지 검증"""

    def test_forecast_daily_always_10_entries(self):
        """중기예보 데이터가 없어도 forecast_daily는 항상 10개"""
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        short_res = make_short_res(now, days=3)
        # 중기예보 빈 응답
        empty_mid = (
            {"response": {"body": {"items": {"item": [{}]}}}},
            {"response": {"body": {"items": {"item": [{}]}}}},
            tm_fc_dt
        )
        result = api._merge_all(now, short_res, empty_mid, {})
        daily = result["weather"]["forecast_daily"]

        assert len(daily) == 10, f"forecast_daily 길이={len(daily)}, 10이어야 함"

    def test_forecast_twice_daily_always_20_entries(self):
        """forecast_twice_daily는 항상 20개 (10일 × 주간/야간)"""
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        short_res = make_short_res(now, days=3)
        mid_res = make_mid_res(tm_fc_dt)
        result = api._merge_all(now, short_res, mid_res, {})
        twice = result["weather"]["forecast_twice_daily"]

        assert len(twice) == 20, f"forecast_twice_daily 길이={len(twice)}, 20이어야 함"

    def test_day_index_sequential(self):
        """_day_index가 0~9로 순서대로 존재하는지 검증"""
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        short_res = make_short_res(now, days=3)
        mid_res = make_mid_res(tm_fc_dt)
        result = api._merge_all(now, short_res, mid_res, {})

        daily_indices = [e["_day_index"] for e in result["weather"]["forecast_daily"]]
        assert daily_indices == list(range(10)), f"_day_index 순서 이상: {daily_indices}"

        twice_indices_am = [e["_day_index"] for e in result["weather"]["forecast_twice_daily"] if e["is_daytime"]]
        assert twice_indices_am == list(range(10)), f"twice_daily 주간 _day_index 이상: {twice_indices_am}"

    def test_mid_term_none_does_not_crash(self):
        """중기예보가 완전히 None이어도 예외 없이 10일 항목을 반환해야 한다"""
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        short_res = make_short_res(now, days=3)
        result = api._merge_all(now, short_res, None, {})

        assert len(result["weather"]["forecast_daily"]) == 10
        assert len(result["weather"]["forecast_twice_daily"]) == 20


# ─────────────────────────────────────────────────────────────────────────────
# 5. _merge_all — 단기·중기 중복 날짜 처리
# ─────────────────────────────────────────────────────────────────────────────

class TestShortMidOverlapHandling:
    """단기예보가 4일치(today+0~3)를 커버할 때 today+3이 단기 값으로 남는지 검증"""

    def test_short_4days_mid_starts_at_4(self):
        """단기예보가 4일치일 때 today+3은 단기, today+4부터 중기 사용"""
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)

        items = []
        for d in range(4):  # today+0,1,2,3 단기
            day = now + timedelta(days=d)
            d_str = day.strftime("%Y%m%d")
            for h in [9, 15]:
                t_str = f"{h:02d}00"
                for cat, val in [("TMP", "99"), ("SKY", "1"), ("PTY", "0"),
                                  ("REH", "50"), ("WSD", "1"), ("POP", "0")]:
                    items.append({"fcstDate": d_str, "fcstTime": t_str,
                                   "category": cat, "fcstValue": val})
        short_res = {"response": {"body": {"items": {"item": items}}}}
        mid_res = make_mid_res(tm_fc_dt)
        result = api._merge_all(now, short_res, mid_res, {})
        daily = result["weather"]["forecast_daily"]

        # today+3: 단기예보 값(99)
        entry_3 = next(e for e in daily if e["_day_index"] == 3)
        assert entry_3["native_temperature"] == 99.0, (
            f"today+3은 단기예보 값이어야 함. 실제={entry_3['native_temperature']}")

        # today+4: 중기예보 값
        mid_day_idx_4 = ((now + timedelta(days=4)).date() - tm_fc_dt.date()).days
        entry_4 = next(e for e in daily if e["_day_index"] == 4)
        assert entry_4["native_temperature"] == float(20 + mid_day_idx_4), (
            f"today+4는 중기예보 값이어야 함. 기대={20 + mid_day_idx_4}, 실제={entry_4['native_temperature']}")

    def test_short_covered_dates_does_not_include_empty_days(self):
        """TMP가 없는 날짜는 short_covered_dates에 포함되지 않아야 한다"""
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)

        # today+0만 단기예보 (TMP 있음), today+1은 SKY만 있고 TMP 없음
        day0 = now.strftime("%Y%m%d")
        day1 = (now + timedelta(days=1)).strftime("%Y%m%d")
        items = [
            {"fcstDate": day0, "fcstTime": "1200", "category": "TMP", "fcstValue": "15"},
            {"fcstDate": day0, "fcstTime": "1200", "category": "SKY", "fcstValue": "1"},
            {"fcstDate": day0, "fcstTime": "1200", "category": "PTY", "fcstValue": "0"},
            # day1: TMP 없음
            {"fcstDate": day1, "fcstTime": "1200", "category": "SKY", "fcstValue": "3"},
        ]
        short_res = {"response": {"body": {"items": {"item": items}}}}
        mid_res = make_mid_res(tm_fc_dt)
        result = api._merge_all(now, short_res, mid_res, {})
        daily = result["weather"]["forecast_daily"]

        # today+1: TMP 없으므로 중기예보 값이어야 함
        mid_day_idx_1 = ((now + timedelta(days=1)).date() - tm_fc_dt.date()).days
        entry_1 = next(e for e in daily if e["_day_index"] == 1)
        expected = float(20 + mid_day_idx_1)
        assert entry_1["native_temperature"] == expected, (
            f"today+1 TMP 없으면 중기예보 값이어야 함. 기대={expected}, 실제={entry_1['native_temperature']}")


# ─────────────────────────────────────────────────────────────────────────────
# 6. 경계 시각별 전체 시나리오
# ─────────────────────────────────────────────────────────────────────────────

class TestBoundaryTimeScenarios:
    """
    실제 운영에서 문제가 되는 경계 시각에서 4~5일차 온도가 올바르게 나오는지
    end-to-end로 검증한다.
    """

    @pytest.mark.parametrize("hour,minute,desc", [
        (5,  50, "오전 5:50 (06시 발표 30분 전 → 전날 18시 사용)"),
        (10,  0, "오전 10:00 (통상 낮)"),
        (18, 30, "오후 18:30 (18시 발표 직후)"),
        (0,  10, "자정 00:10 (전날 18시 사용)"),
        (6,  29, "오전 6:29 (06시 발표 직전 → 전날 18시 사용)"),
        (6,  31, "오전 6:31 (06시 발표 1분 후 → 오늘 06시 사용)"),
    ])
    def test_day4_and_day5_have_valid_temperature(self, hour, minute, desc):
        """
        4일차(i=4)와 5일차(i=5)의 최고·최저온도가 None이 아니어야 한다.
        이것이 이번 버그 수정의 핵심 검증이다.
        """
        api = make_api()
        now = datetime(2026, 4, 11, hour, minute, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        short_res = make_short_res(now, days=3)
        mid_res = make_mid_res(tm_fc_dt, start_idx=3, end_idx=10)
        result = api._merge_all(now, short_res, mid_res, {})

        daily = result["weather"]["forecast_daily"]
        twice = result["weather"]["forecast_twice_daily"]

        for i in [4, 5]:
            day_entry = next((e for e in daily if e["_day_index"] == i), None)
            assert day_entry is not None, f"[{desc}] i={i} forecast_daily 항목 없음"
            assert day_entry["native_temperature"] is not None, (
                f"[{desc}] i={i} daily 최고온도가 None — mid_day_idx 계산 오류")
            assert day_entry["native_templow"] is not None, (
                f"[{desc}] i={i} daily 최저온도가 None — mid_day_idx 계산 오류")

            am_entry = next((e for e in twice if e["_day_index"] == i and e["is_daytime"]), None)
            pm_entry = next((e for e in twice if e["_day_index"] == i and not e["is_daytime"]), None)
            assert am_entry["native_temperature"] is not None, (
                f"[{desc}] i={i} twice 주간 온도 None")
            assert pm_entry["native_temperature"] is not None, (
                f"[{desc}] i={i} twice 야간 온도 None")

    def test_day4_temperature_matches_expected_mid_key(self):
        """
        오전 5:50 시나리오: tm_fc_dt=전날 18시 기준으로
        i=4(today+4)의 mid_day_idx가 정확히 계산되어 taMax{idx} 값이 사용되는지 검증
        """
        api = make_api()
        now = datetime(2026, 4, 11, 5, 50, tzinfo=TZ)   # 전날 18시 발표본 사용
        tm_fc_dt = api._get_mid_base_dt(now)
        assert tm_fc_dt.date() == date(2026, 4, 10), f"tm_fc_dt 날짜가 4/10이어야 함: {tm_fc_dt}"
        assert tm_fc_dt.hour == 18

        short_res = make_short_res(now, days=3)
        mid_res = make_mid_res(tm_fc_dt, start_idx=3, end_idx=10)
        result = api._merge_all(now, short_res, mid_res, {})

        # i=4: target=4/15, tm_fc_dt=4/10 → mid_day_idx=5 → taMax5=25
        entry_4 = next(e for e in result["weather"]["forecast_daily"] if e["_day_index"] == 4)
        expected_max = 20 + 5  # taMax5
        assert entry_4["native_temperature"] == float(expected_max), (
            f"i=4 최고온도: 기대={expected_max}(taMax5), 실제={entry_4['native_temperature']}")

    def test_day4_temperature_normal_morning(self):
        """
        오전 10:00 시나리오: tm_fc_dt=오늘 06시 기준으로
        i=4(today+4)의 mid_day_idx=4 → taMax4=24
        """
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        assert tm_fc_dt.date() == date(2026, 4, 11)
        assert tm_fc_dt.hour == 6

        short_res = make_short_res(now, days=3)
        mid_res = make_mid_res(tm_fc_dt, start_idx=3, end_idx=10)
        result = api._merge_all(now, short_res, mid_res, {})

        entry_4 = next(e for e in result["weather"]["forecast_daily"] if e["_day_index"] == 4)
        expected_max = 20 + 4  # taMax4
        assert entry_4["native_temperature"] == float(expected_max), (
            f"i=4 최고온도: 기대={expected_max}(taMax4), 실제={entry_4['native_temperature']}")


# ─────────────────────────────────────────────────────────────────────────────
# 7. _merge_all 튜플 언패킹 — 레거시 형식 호환성
# ─────────────────────────────────────────────────────────────────────────────

class TestMidResTupleUnpacking:
    """mid_res가 3-튜플이 아닌 경우에도 폴백이 작동하는지 검증"""

    def test_fallback_when_mid_res_is_none(self):
        """mid_res=None → 예외 없이 10일 항목 반환"""
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        short_res = make_short_res(now, days=3)
        result = api._merge_all(now, short_res, None, {})
        assert len(result["weather"]["forecast_daily"]) == 10

    def test_fallback_when_mid_res_is_2tuple(self):
        """mid_res=(ta, land) 2-튜플 → _get_mid_base_dt(now)로 폴백"""
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        short_res = make_short_res(now, days=3)
        tm_fc_dt = api._get_mid_base_dt(now)
        ta_wrap, land_wrap, _ = make_mid_res(tm_fc_dt)
        # 2-튜플로 전달
        result = api._merge_all(now, short_res, (ta_wrap, land_wrap), {})
        assert len(result["weather"]["forecast_daily"]) == 10

    def test_3tuple_is_preferred(self):
        """3-튜플이면 세 번째 원소 tm_fc_dt를 사용 (폴백 미발생)"""
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        short_res = make_short_res(now, days=3)
        mid_res = make_mid_res(tm_fc_dt)
        # 3-튜플임을 명시적으로 확인
        assert len(mid_res) == 3
        result = api._merge_all(now, short_res, mid_res, {})

        # i=4 값이 정상적으로 나오면 3-튜플 경로 사용됨을 간접 증명
        entry_4 = next(e for e in result["weather"]["forecast_daily"] if e["_day_index"] == 4)
        assert entry_4["native_temperature"] is not None
