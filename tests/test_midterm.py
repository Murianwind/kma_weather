"""단기·중기 예보 연결 로직 검증 테스트"""
import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock  # AsyncMock 추가

from custom_components.kma_weather.api_kma import KMAWeatherAPI

TZ = ZoneInfo("Asia/Seoul")

def make_api():
    api = KMAWeatherAPI(MagicMock(), "TEST_KEY")
    api.lat, api.lon, api.nx, api.ny = 37.56, 126.98, 60, 127
    return api

def make_short_res(base_date, days=4):
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
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        result = api._get_mid_base_dt(now)
        assert result.tzinfo is not None


class TestGetMidTerm:
    @pytest.mark.asyncio
    async def test_returns_three_tuple(self):
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        mock_resp = {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": [{"taMax3": "25"}]}}}}

        async def mock_fetch(url, params, **kwargs):
            return mock_resp

        api._fetch = mock_fetch
        result = await api._get_mid_term(now, "11B10101", "11B00000")
        assert isinstance(result, tuple) and len(result) == 3
        _, _, tm_fc_dt = result
        assert isinstance(tm_fc_dt, datetime) and tm_fc_dt.tzinfo is not None

    @pytest.mark.asyncio
    async def test_tmfc_format_matches_api_param(self):
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        expected_base = api._get_mid_base_dt(now).strftime("%Y%m%d%H%M")
        called_params = []

        async def mock_fetch(url, params, **kwargs):
            called_params.append(params.get("tmFc"))
            return {"response": {"body": {"items": {"item": [{"taMax3": "25"}]}}}}

        api._fetch = mock_fetch
        await api._get_mid_term(now, "11B10101", "11B00000")
        assert len(called_params) == 2
        for p in called_params:
            assert p == expected_base


class TestMidDayIndexCalculation:
    def _run_merge(self, now, short_days=4):
        api = make_api()
        tm_fc_dt = api._get_mid_base_dt(now)
        short_res = make_short_res(now, days=short_days)
        mid_res = make_mid_res(tm_fc_dt, start_idx=3, end_idx=10)
        return api._merge_all(now, short_res, mid_res, {})

    @pytest.mark.parametrize("now_hour,now_minute,desc", [
        (10, 0,  "오전 10:00"), (19, 0,  "오후 19:00"),
        (5,  50, "오전 5:50"), (0,  30, "자정 0:30"),
    ])
    def test_mid_day_idx_for_day_4_to_6(self, now_hour, now_minute, desc):
        now = datetime(2026, 4, 11, now_hour, now_minute, tzinfo=TZ)
        api = make_api()
        tm_fc_dt = api._get_mid_base_dt(now)
        result = api._merge_all(now, make_short_res(now, days=4),
                                 make_mid_res(tm_fc_dt, start_idx=3, end_idx=10), {})
        daily = result["weather"]["forecast_daily"]
        d3 = next((e for e in daily if e["_day_index"] == 3), None)
        assert d3 is not None and d3["native_temperature"] is not None, f"D+3 단기 데이터 없음 ({desc})"
        for i in range(4, 7):
            target_date = (now + timedelta(days=i)).date()
            mid_day_idx = (target_date - tm_fc_dt.date()).days
            expected_max = 20 + mid_day_idx
            day_entry = next((e for e in daily if e["_day_index"] == i), None)
            assert day_entry is not None
            assert day_entry["native_temperature"] == float(expected_max), \
                f"D+{i} 기온 오차 ({desc}): 기대={expected_max}, 실제={day_entry['native_temperature']}"

    def test_no_gap_between_short_and_mid(self):
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        result = self._run_merge(now, short_days=4)
        daily = result["weather"]["forecast_daily"]
        for i in range(6):
            entry = next((e for e in daily if e["_day_index"] == i), None)
            assert entry is not None
            assert entry["native_temperature"] is not None, f"D+{i} 기온 None"
            assert entry["native_templow"] is not None, f"D+{i} 최저기온 None"


class TestForecastContinuity:
    def test_forecast_daily_always_10_entries(self):
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        empty_mid = ({"response": {"body": {"items": {"item": [{}]}}}},
                     {"response": {"body": {"items": {"item": [{}]}}}}, tm_fc_dt)
        result = api._merge_all(now, make_short_res(now, days=4), empty_mid, {})
        assert len(result["weather"]["forecast_daily"]) == 10

    def test_day_index_sequential(self):
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        result = api._merge_all(now, make_short_res(now, days=4),
                                make_mid_res(api._get_mid_base_dt(now)), {})
        daily_indices = [e["_day_index"] for e in result["weather"]["forecast_daily"]]
        assert daily_indices == list(range(10))


class TestBoundaryTimeScenarios:
    def test_day4_temperature_matches_expected_mid_key(self):
        api = make_api()
        now = datetime(2026, 4, 11, 5, 50, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        result = api._merge_all(now, make_short_res(now, days=4),
                                 make_mid_res(tm_fc_dt, start_idx=3, end_idx=10), {})
        target_date = (now + timedelta(days=4)).date()
        mid_day_idx = (target_date - tm_fc_dt.date()).days
        entry_4 = next(e for e in result["weather"]["forecast_daily"] if e["_day_index"] == 4)
        assert entry_4["native_temperature"] == float(20 + mid_day_idx)


# ══════════════════════════════════════════════════════════════════════════════
# _get_mid_term 재시도 및 _is_valid 추가 커버리지
# ══════════════════════════════════════════════════════════════════════════════

class TestGetMidTermRetry:
    """_get_mid_term 재시도 분기 추가 테스트"""

    @pytest.mark.asyncio
    async def test_empty_response_retries_and_succeeds(self):
        """
        [Given] 최신 tmFc 응답이 빈 배열
        [When]  _get_mid_term 호출
        [Then]  이전 시각으로 재시도 후 정상 응답 반환 (총 호출 4회)
        """
        api = make_api()
        now = datetime(2026, 6, 1, 10, 0, tzinfo=TZ)

        empty_resp = {"response": {"header": {"resultCode": "00"},
                                   "body": {"items": {"item": []}}}}
        valid_ta   = {"response": {"body": {"items": {"item": [{"taMax3": "25", "taMin3": "15"}]}}}}
        valid_land = {"response": {"body": {"items": {"item": [{"wf3Am": "맑음", "wf3Pm": "흐림"}]}}}}

        call_count = {"n": 0}

        async def mock_fetch(url, params, **kwargs):
            call_count["n"] += 1
            if call_count["n"] <= 2:
                return empty_resp
            if "getMidTa" in url:
                return valid_ta
            return valid_land

        api._fetch = mock_fetch
        r0, r1, dt = await api._get_mid_term(now, "11B10101", "11B00000")
        assert r0 is not None
        assert r1 is not None
        assert call_count["n"] == 4

    @pytest.mark.asyncio
    async def test_empty_response_retries_and_fails(self):
        """
        [Given] 최신 + 재시도 모두 빈 응답
        [When]  _get_mid_term 호출
        [Then]  tm_fc_dt는 반환되고 r0/r1은 빈 응답 그대로
        """
        api = make_api()
        now = datetime(2026, 6, 1, 10, 0, tzinfo=TZ)

        empty_resp = {"response": {"header": {"resultCode": "00"},
                                   "body": {"items": {"item": []}}}}
        api._fetch = AsyncMock(return_value=empty_resp)
        r0, r1, dt = await api._get_mid_term(now, "11B10101", "11B00000")
        assert dt is not None

    @pytest.mark.asyncio
    async def test_is_valid_returns_false_for_unsubscribed(self):
        """
        [Given] 미신청 응답 코드(30)
        [When]  _get_mid_term 호출
        [Then]  ("UNSUBSCRIBED", None, tm_fc_dt) 반환
        """
        api = make_api()
        now = datetime(2026, 6, 1, 10, 0, tzinfo=TZ)

        unsubscribed_resp = {"response": {"header": {"resultCode": "30"}}}
        api._fetch = AsyncMock(return_value=unsubscribed_resp)
        result = await api._get_mid_term(now, "11B10101", "11B00000")
        assert result[0] == "UNSUBSCRIBED"
        assert result[1] is None

    @pytest.mark.asyncio
    async def test_retry_uses_prev_hour_correctly_at_06(self):
        """
        [Given] tmFc=당일 06시 (06:31 기준)
        [When]  빈 응답으로 재시도 발생
        [Then]  재시도 tmFc는 전날 18시여야 함
        """
        api = make_api()
        now = datetime(2026, 6, 1, 6, 31, tzinfo=TZ)
        assert api._get_mid_base_dt(now).hour == 6

        called_bases = []
        empty_resp = {"response": {"header": {"resultCode": "00"},
                                   "body": {"items": {"item": []}}}}

        async def mock_fetch(url, params, **kwargs):
            called_bases.append(params.get("tmFc", ""))
            return empty_resp

        api._fetch = mock_fetch
        await api._get_mid_term(now, "11B10101", "11B00000")
        retry_bases = called_bases[2:]
        assert any("18" in b[-4:] for b in retry_bases)

    @pytest.mark.asyncio
    async def test_retry_uses_prev_hour_correctly_at_18(self):
        """
        [Given] tmFc=당일 18시 (18:31 기준)
        [When]  빈 응답으로 재시도 발생
        [Then]  재시도 tmFc는 당일 06시여야 함
        """
        api = make_api()
        now = datetime(2026, 6, 1, 18, 31, tzinfo=TZ)
        assert api._get_mid_base_dt(now).hour == 18

        called_bases = []
        empty_resp = {"response": {"header": {"resultCode": "00"},
                                   "body": {"items": {"item": []}}}}

        async def mock_fetch(url, params, **kwargs):
            called_bases.append(params.get("tmFc", ""))
            return empty_resp

        api._fetch = mock_fetch
        await api._get_mid_term(now, "11B10101", "11B00000")
        retry_bases = called_bases[2:]
        assert any("0600" in b[-4:] for b in retry_bases)
