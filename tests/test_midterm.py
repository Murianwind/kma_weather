"""단기·중기 예보 연결 로직 검증 테스트"""
import pytest
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock

from custom_components.kma_weather.api_kma import KMAWeatherAPI

TZ = ZoneInfo("Asia/Seoul")


def make_api():
    api = KMAWeatherAPI(MagicMock(), "TEST_KEY", "11B10101", "11B00000")
    api.lat, api.lon, api.nx, api.ny = 37.56, 126.98, 60, 127
    return api


def make_short_res(base_date, days=3):
    items = []
    for d in range(days):
        day = base_date + timedelta(days=d)
        d_str = day.strftime("%Y%m%d")
        for h in range(6, 22, 3):
            t_str = f"{h:02d}00"
            tmp_val = str(10 + d * 2 + h // 3)
            for cat, val in [("TMP", tmp_val), ("SKY", "1"), ("PTY", "0"),
                              ("REH", "50"), ("WSD", "2.0"), ("POP", "10")]:
                items.append({"fcstDate": d_str, "fcstTime": t_str,
                               "category": cat, "fcstValue": val})
        for t_str, sky in [("0900", "1"), ("1500", "3")]:
            for cat, val in [("SKY", sky), ("PTY", "0")]:
                items.append({"fcstDate": d_str, "fcstTime": t_str,
                               "category": cat, "fcstValue": val})
    return {"response": {"body": {"items": {"item": items}}}}


def make_mid_res(tm_fc_dt, start_idx=3, end_idx=10):
    ta_item, land_item = {}, {}
    for i in range(start_idx, end_idx + 1):
        ta_item[f"taMax{i}"] = str(20 + i)
        ta_item[f"taMin{i}"] = str(5 + i)
        wf = "맑음" if i % 2 == 0 else "흐림"
        land_item[f"wf{i}Am"] = wf
        land_item[f"wf{i}Pm"] = wf

    def wrap(item):
        return {"response": {"body": {"items": {"item": [item]}}}}
    return (wrap(ta_item), wrap(land_item), tm_fc_dt)


class TestGetMidBaseDt:
    @pytest.mark.parametrize("hour,minute,expected_date_offset,expected_hour,desc", [
        (5,  59, -1, 18, "06시 발표 30분 전 → 전날 18시"),
        (6,  30,  0,  6, "06:30 → 오늘 06시"),
        (11, 30,  0,  6, "낮 11:30 → 오늘 06시"),
        (17, 59,  0,  6, "17:59 → 오늘 06시"),
        (18, 30,  0, 18, "18:30 → 오늘 18시"),
        (23, 30,  0, 18, "23:30 → 오늘 18시"),
        (0,  30, -1, 18, "자정 00:30 → 전날 18시"),
    ])
    def test_tmfc_calculation(self, hour, minute, expected_date_offset, expected_hour, desc):
        # Given: 특정 시각(hour:minute)에 API가 실행된다
        # When:  _get_mid_base_dt를 호출하면
        # Then:  30분 게시 지연을 감안한 올바른 tmFc(발표기준시각)를 반환한다
        api = make_api()
        now = datetime(2026, 4, 11, hour, minute, tzinfo=TZ)
        result = api._get_mid_base_dt(now)
        if hour == 0 and minute == 30:
            expected_date = now.date() - timedelta(days=1)
        elif hour == 5 and minute == 59:
            expected_date = now.date() - timedelta(days=1)
        else:
            expected_date = (now - timedelta(minutes=30)).date() + timedelta(days=expected_date_offset)
        assert result.hour == expected_hour, f"[{desc}] 기대={expected_hour}, 실제={result.hour}"
        assert result.date() == expected_date, f"[{desc}] 기대={expected_date}, 실제={result.date()}"
        assert result.minute == 0 and result.second == 0

    def test_returns_datetime_with_timezone(self):
        # Given: KMAWeatherAPI 인스턴스와 임의의 현재 시각이 주어진다
        # When:  _get_mid_base_dt를 호출하면
        # Then:  반환값이 timezone-aware datetime이어야 한다
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        result = api._get_mid_base_dt(now)
        assert result.tzinfo is not None

    def test_boundary_exactly_0630(self):
        # Given: 정각 06:30에 API가 실행된다 (effective=06:00, 06시 발표 게시 직후)
        # When:  _get_mid_base_dt를 호출하면
        # Then:  오늘 06:00을 tmFc로 반환한다
        api = make_api()
        now = datetime(2026, 4, 11, 6, 30, tzinfo=TZ)
        result = api._get_mid_base_dt(now)
        assert result.date() == now.date() and result.hour == 6

    def test_boundary_exactly_0600(self):
        # Given: 정각 06:00에 API가 실행된다 (effective=05:30, 아직 게시 전)
        # When:  _get_mid_base_dt를 호출하면
        # Then:  전날 18:00을 tmFc로 반환한다
        api = make_api()
        now = datetime(2026, 4, 11, 6, 0, tzinfo=TZ)
        result = api._get_mid_base_dt(now)
        assert result.date() == now.date() - timedelta(days=1) and result.hour == 18

    def test_boundary_exactly_1830(self):
        # Given: 정각 18:30에 API가 실행된다 (effective=18:00, 18시 발표 게시 직후)
        # When:  _get_mid_base_dt를 호출하면
        # Then:  오늘 18:00을 tmFc로 반환한다
        api = make_api()
        now = datetime(2026, 4, 11, 18, 30, tzinfo=TZ)
        result = api._get_mid_base_dt(now)
        assert result.date() == now.date() and result.hour == 18

    def test_boundary_exactly_1800(self):
        # Given: 정각 18:00에 API가 실행된다 (effective=17:30, 아직 게시 전)
        # When:  _get_mid_base_dt를 호출하면
        # Then:  오늘 06:00을 tmFc로 반환한다
        api = make_api()
        now = datetime(2026, 4, 11, 18, 0, tzinfo=TZ)
        result = api._get_mid_base_dt(now)
        assert result.date() == now.date() and result.hour == 6


class TestGetMidTerm:
    @pytest.mark.asyncio
    async def test_returns_three_tuple(self):
        # Given: KMAWeatherAPI 인스턴스와 _fetch를 mock으로 교체한다
        # When:  _get_mid_term을 호출하면
        # Then:  (기온응답, 육상응답, tm_fc_dt) 3-튜플을 반환하며
        #        세 번째 원소는 timezone-aware datetime이어야 한다
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        mock_resp = {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": []}}}}

        async def mock_fetch(url, params, **kwargs):
            return mock_resp

        api._fetch = mock_fetch
        result = await api._get_mid_term(now)
        assert isinstance(result, tuple) and len(result) == 3
        _, _, tm_fc_dt = result
        assert isinstance(tm_fc_dt, datetime) and tm_fc_dt.tzinfo is not None

    @pytest.mark.asyncio
    async def test_tmfc_format_matches_api_param(self):
        # Given: KMAWeatherAPI 인스턴스와 호출된 tmFc 파라미터를 수집하는 mock이 있다
        # When:  _get_mid_term을 호출하면
        # Then:  getMidTa, getMidLandFcst 두 API 모두 동일한 tmFc 값으로 호출된다
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        expected_base = api._get_mid_base_dt(now).strftime("%Y%m%d%H%M")
        called_params = []

        async def mock_fetch(url, params, **kwargs):
            called_params.append(params.get("tmFc"))
            return {}

        api._fetch = mock_fetch
        await api._get_mid_term(now)
        assert len(called_params) == 2
        for p in called_params:
            assert p == expected_base


class TestMidDayIndexCalculation:
    def _run_merge(self, now, short_days=3):
        api = make_api()
        tm_fc_dt = api._get_mid_base_dt(now)
        short_res = make_short_res(now, days=short_days)
        mid_res = make_mid_res(tm_fc_dt, start_idx=3, end_idx=10)
        return api._merge_all(now, short_res, mid_res, {})

    @pytest.mark.parametrize("now_hour,now_minute,desc", [
        (10, 0,  "오전 10:00"),
        (19, 0,  "오후 19:00"),
        (5,  50, "오전 5:50"),
        (0,  30, "자정 0:30"),
    ])
    def test_mid_day_idx_for_day_3_to_6(self, now_hour, now_minute, desc):
        # Given: 단기예보가 D+0~D+2를 커버하고 중기예보가 taMax3~taMax10을 포함한다
        # When:  _merge_all을 호출하면
        # Then:  D+3~D+6의 최고기온이 중기예보 taMax{mid_day_idx}와 정확히 일치한다
        now = datetime(2026, 4, 11, now_hour, now_minute, tzinfo=TZ)
        api = make_api()
        tm_fc_dt = api._get_mid_base_dt(now)
        result = api._merge_all(now, make_short_res(now, days=3),
                                 make_mid_res(tm_fc_dt, start_idx=3, end_idx=10), {})
        daily = result["weather"]["forecast_daily"]
        twice = result["weather"]["forecast_twice_daily"]
        for i in range(3, 7):
            target_date = (now + timedelta(days=i)).date()
            mid_day_idx = (target_date - tm_fc_dt.date()).days
            expected_max = 20 + mid_day_idx
            day_entry = next((e for e in daily if e["_day_index"] == i), None)
            assert day_entry is not None
            assert day_entry["native_temperature"] == float(expected_max)
            am_entry = next((e for e in twice if e["_day_index"] == i and e["is_daytime"]), None)
            assert am_entry is not None
            assert am_entry["native_temperature"] == float(expected_max)

    def test_no_gap_between_short_and_mid(self):
        # Given: 단기예보가 D+0~D+2를 커버하고 중기예보가 D+3 이후를 커버한다
        # When:  _merge_all을 호출하면
        # Then:  D+0~D+5 모든 날짜의 최고/최저 기온이 None 없이 연속적으로 채워진다
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        result = self._run_merge(now, short_days=3)
        daily = result["weather"]["forecast_daily"]
        for i in range(6):
            entry = next((e for e in daily if e["_day_index"] == i), None)
            assert entry is not None
            assert entry["native_temperature"] is not None
            assert entry["native_templow"] is not None

    def test_short_term_priority_over_mid(self):
        # Given: 단기예보가 D+0~D+2를 TMP=100으로 커버하고 중기예보도 동일 날짜를 포함한다
        # When:  _merge_all을 호출하면
        # Then:  D+0~D+2는 중기예보 값이 아닌 단기예보 값(100)이 사용된다
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        api = make_api()
        tm_fc_dt = api._get_mid_base_dt(now)
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
        result = api._merge_all(now, short_res, make_mid_res(tm_fc_dt), {})
        daily = result["weather"]["forecast_daily"]
        for i in range(3):
            entry = next(e for e in daily if e["_day_index"] == i)
            assert entry["native_temperature"] == 100.0


class TestForecastContinuity:
    def test_forecast_daily_always_10_entries(self):
        # Given: 단기예보 3일치와 중기예보 응답이 빈 항목으로 주어진다
        # When:  _merge_all을 호출하면
        # Then:  forecast_daily는 항상 정확히 10개 항목을 포함한다
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        empty_mid = (
            {"response": {"body": {"items": {"item": [{}]}}}},
            {"response": {"body": {"items": {"item": [{}]}}}},
            tm_fc_dt
        )
        result = api._merge_all(now, make_short_res(now, days=3), empty_mid, {})
        assert len(result["weather"]["forecast_daily"]) == 10

    def test_forecast_twice_daily_always_20_entries(self):
        # Given: 단기예보 3일치와 정상적인 중기예보 응답이 주어진다
        # When:  _merge_all을 호출하면
        # Then:  forecast_twice_daily는 항상 정확히 20개 항목을 포함한다
        #        (10일 × 주간/야간 각 1회)
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        result = api._merge_all(now, make_short_res(now, days=3), make_mid_res(tm_fc_dt), {})
        assert len(result["weather"]["forecast_twice_daily"]) == 20

    def test_day_index_sequential(self):
        # Given: 단기예보 3일치와 정상적인 중기예보 응답이 주어진다
        # When:  _merge_all을 호출하면
        # Then:  forecast_daily의 _day_index가 0부터 9까지 순서대로 존재한다
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        result = api._merge_all(now, make_short_res(now, days=3), make_mid_res(tm_fc_dt), {})
        daily_indices = [e["_day_index"] for e in result["weather"]["forecast_daily"]]
        assert daily_indices == list(range(10))

    def test_mid_term_none_does_not_crash(self):
        # Given: 단기예보 3일치가 있고 중기예보 응답은 None이다
        # When:  _merge_all을 호출하면
        # Then:  예외 없이 10일치 forecast_daily와 20개의 forecast_twice_daily를 반환한다
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        result = api._merge_all(now, make_short_res(now, days=3), None, {})
        assert len(result["weather"]["forecast_daily"]) == 10
        assert len(result["weather"]["forecast_twice_daily"]) == 20


class TestBoundaryTimeScenarios:
    @pytest.mark.parametrize("hour,minute,desc", [
        (5,  50, "오전 5:50"), (10, 0,  "오전 10:00"),
        (18, 30, "오후 18:30"), (0,  10, "자정 00:10"),
        (6,  29, "오전 6:29"),  (6,  31, "오전 6:31"),
    ])
    def test_day4_and_day5_have_valid_temperature(self, hour, minute, desc):
        # Given: 경계 시각(중기 tmFc 전환 전후, 단기 발표 전후)에 단기 3일치와 중기예보가 있다
        # When:  _merge_all을 호출하면
        # Then:  D+4, D+5의 최고/최저 기온이 None이 아니며
        #        twice_daily의 주간/야간 온도도 모두 값을 가진다
        api = make_api()
        now = datetime(2026, 4, 11, hour, minute, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        result = api._merge_all(now, make_short_res(now, days=3),
                                 make_mid_res(tm_fc_dt, start_idx=3, end_idx=10), {})
        daily = result["weather"]["forecast_daily"]
        twice = result["weather"]["forecast_twice_daily"]
        for i in [4, 5]:
            day_entry = next((e for e in daily if e["_day_index"] == i), None)
            assert day_entry is not None
            assert day_entry["native_temperature"] is not None, f"[{desc}] i={i} 최고기온 None"
            assert day_entry["native_templow"] is not None, f"[{desc}] i={i} 최저기온 None"
            am = next((e for e in twice if e["_day_index"] == i and e["is_daytime"]), None)
            pm = next((e for e in twice if e["_day_index"] == i and not e["is_daytime"]), None)
            assert am["native_temperature"] is not None
            assert pm["native_temperature"] is not None

    def test_day4_temperature_matches_expected_mid_key(self):
        # Given: 오전 5:50에 실행되어 tmFc=전날 18:00이 선택된다
        #        단기예보 3일치와 taMax3~taMax10을 포함한 중기예보가 있다
        # When:  _merge_all을 호출하면
        # Then:  D+4의 최고기온은 taMax5(=25)와 정확히 일치한다
        #        (D+4 날짜 - tmFc 날짜 = 5일)
        api = make_api()
        now = datetime(2026, 4, 11, 5, 50, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        assert tm_fc_dt.date() == date(2026, 4, 10) and tm_fc_dt.hour == 18
        result = api._merge_all(now, make_short_res(now, days=3),
                                 make_mid_res(tm_fc_dt, start_idx=3, end_idx=10), {})
        entry_4 = next(e for e in result["weather"]["forecast_daily"] if e["_day_index"] == 4)
        assert entry_4["native_temperature"] == float(20 + 5)  # taMax5

    def test_day4_temperature_normal_morning(self):
        # Given: 오전 10:00에 실행되어 tmFc=오늘 06:00이 선택된다
        #        단기예보 3일치와 taMax3~taMax10을 포함한 중기예보가 있다
        # When:  _merge_all을 호출하면
        # Then:  D+4의 최고기온은 taMax4(=24)와 정확히 일치한다
        #        (D+4 날짜 - tmFc 날짜 = 4일)
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        assert tm_fc_dt.date() == date(2026, 4, 11) and tm_fc_dt.hour == 6
        result = api._merge_all(now, make_short_res(now, days=3),
                                 make_mid_res(tm_fc_dt, start_idx=3, end_idx=10), {})
        entry_4 = next(e for e in result["weather"]["forecast_daily"] if e["_day_index"] == 4)
        assert entry_4["native_temperature"] == float(20 + 4)  # taMax4


class TestMidResTupleUnpacking:
    def test_fallback_when_mid_res_is_none(self):
        # Given: 단기예보 3일치가 있고 mid_res=None이다
        # When:  _merge_all을 호출하면
        # Then:  예외 없이 10일치 forecast_daily를 반환한다
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        result = api._merge_all(now, make_short_res(now, days=3), None, {})
        assert len(result["weather"]["forecast_daily"]) == 10

    def test_fallback_when_mid_res_is_2tuple(self):
        # Given: 단기예보 3일치가 있고 mid_res가 2-tuple(ta응답, land응답)이다
        # When:  _merge_all을 호출하면
        # Then:  _get_mid_base_dt(now)로 폴백하여 10일치 forecast_daily를 반환한다
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        ta_wrap, land_wrap, _ = make_mid_res(tm_fc_dt)
        result = api._merge_all(now, make_short_res(now, days=3), (ta_wrap, land_wrap), {})
        assert len(result["weather"]["forecast_daily"]) == 10

    def test_3tuple_is_preferred(self):
        # Given: 단기예보 3일치가 있고 mid_res가 올바른 3-tuple이다
        # When:  _merge_all을 호출하면
        # Then:  3-tuple의 세 번째 원소 tm_fc_dt가 사용되어 D+4 온도가 정상 계산된다
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        mid_res = make_mid_res(tm_fc_dt)
        assert len(mid_res) == 3
        result = api._merge_all(now, make_short_res(now, days=3), mid_res, {})
        entry_4 = next(e for e in result["weather"]["forecast_daily"] if e["_day_index"] == 4)
        assert entry_4["native_temperature"] is not None
