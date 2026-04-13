"""단기·중기 예보 연결 로직 검증 테스트"""
import pytest
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock

from custom_components.kma_weather.api_kma import KMAWeatherAPI

TZ = ZoneInfo("Asia/Seoul")

# ─────────────────────────────────────────────────────────────────────────────
# [Given] 공통 헬퍼 함수 (원본 로직 100% 유지)
# ─────────────────────────────────────────────────────────────────────────────
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

# ─────────────────────────────────────────────────────────────────────────────
# 1. TestGetMidBaseDt: 중기예보 기준 시각(tmFc) 산출 로직
# ─────────────────────────────────────────────────────────────────────────────
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
        # [Given] 특정 시각에 API 업데이트가 트리거되었을 때
        api = make_api()
        now = datetime(2026, 4, 11, hour, minute, tzinfo=TZ)
        
        # [When] 중기예보 기준 발표 시각을 계산하면
        result = api._get_mid_base_dt(now)
        
        # [Then] 기상청 30분 게시 지연 정책이 반영된 날짜와 시간이 반환되어야 함
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
        # [Given/When] 기준 시각 계산 시
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        result = api._get_mid_base_dt(now)
        # [Then] 반환값은 반드시 timezone 정보(Asia/Seoul)를 포함해야 함
        assert result.tzinfo is not None

# ─────────────────────────────────────────────────────────────────────────────
# 2. TestGetMidTerm: 중기예보 데이터 수집 로직 (오류 수정됨)
# ─────────────────────────────────────────────────────────────────────────────
class TestGetMidTerm:
    @pytest.mark.asyncio
    async def test_returns_three_tuple(self):
        # [Given] 기상청 중기예보 API 응답이 준비되었을 때
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        # [Fix] 비어있는 items가 아닌, 유효한 데이터가 있는 Mock 응답을 주어 재시도(Retry) 방지
        mock_resp = {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": [{"taMax3": "25"}]}}}}

        async def mock_fetch(url, params, **kwargs):
            return mock_resp

        api._fetch = mock_fetch
        # [When] 중기예보 데이터를 요청하면
        result = await api._get_mid_term(now)
        
        # [Then] (기온응답, 육상응답, 기준시각)의 3-튜플이 반환되어야 함
        assert isinstance(result, tuple) and len(result) == 3
        _, _, tm_fc_dt = result
        assert isinstance(tm_fc_dt, datetime) and tm_fc_dt.tzinfo is not None

    @pytest.mark.asyncio
    async def test_tmfc_format_matches_api_param(self):
        # [Given] 현재 시각 기준 기대되는 tmFc 파라미터 계산
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        expected_base = api._get_mid_base_dt(now).strftime("%Y%m%d%H%M")
        called_params = []

        # [Fix] 유효 데이터({"item": [{...}]})를 반환하여 재시도 로직(prev_dt 호출)을 타지 않게 함
        async def mock_fetch(url, params, **kwargs):
            called_params.append(params.get("tmFc"))
            return {"response": {"body": {"items": {"item": [{"taMax3": "25"}]}}}}

        api._fetch = mock_fetch
        # [When] 중기예보 API를 호출하면
        await api._get_mid_term(now)
        
        # [Then] 기온(getMidTa)과 육상(getMidLandFcst) 두 API가 동일한 시각 파라미터로 호출되어야 함 (총 2회)
        assert len(called_params) == 2
        for p in called_params:
            assert p == expected_base

# ─────────────────────────────────────────────────────────────────────────────
# 3. TestMidDayIndexCalculation: 단기-중기 데이터 병합 인덱스 검증
# ─────────────────────────────────────────────────────────────────────────────
class TestMidDayIndexCalculation:
    def _run_merge(self, now, short_days=3):
        api = make_api()
        tm_fc_dt = api._get_mid_base_dt(now)
        short_res = make_short_res(now, days=short_days)
        mid_res = make_mid_res(tm_fc_dt, start_idx=3, end_idx=10)
        return api._merge_all(now, short_res, mid_res, {})

    @pytest.mark.parametrize("now_hour,now_minute,desc", [
        (10, 0,  "오전 10:00"), (19, 0,  "오후 19:00"),
        (5,  50, "오전 5:50"), (0,  30, "자정 0:30"),
    ])
    def test_mid_day_idx_for_day_3_to_6(self, now_hour, now_minute, desc):
        # [Given] 단기(D+0~D+2)와 중기(D+3~D+10) 데이터가 병합 준비된 상태
        now = datetime(2026, 4, 11, now_hour, now_minute, tzinfo=TZ)
        api = make_api()
        tm_fc_dt = api._get_mid_base_dt(now)
        
        # [When] 병합 로직(_merge_all)을 수행하면
        result = api._merge_all(now, make_short_res(now, days=3),
                                 make_mid_res(tm_fc_dt, start_idx=3, end_idx=10), {})
        
        # [Then] D+3~D+6 날짜의 기온이 발표 시각에 따른 중기예보 인덱스(mid_day_idx)와 정확히 일치해야 함
        daily = result["weather"]["forecast_daily"]
        for i in range(3, 7):
            target_date = (now + timedelta(days=i)).date()
            mid_day_idx = (target_date - tm_fc_dt.date()).days
            expected_max = 20 + mid_day_idx
            day_entry = next((e for e in daily if e["_day_index"] == i), None)
            assert day_entry is not None
            assert day_entry["native_temperature"] == float(expected_max)

    def test_no_gap_between_short_and_mid(self):
        # [Given] 단기와 중기 예보가 연결되는 지점에서
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        # [When] 데이터를 병합하면
        result = self._run_merge(now, short_days=3)
        daily = result["weather"]["forecast_daily"]
        # [Then] D+0부터 D+5까지 기온 누락(None) 없이 연속적인 예보가 생성되어야 함
        for i in range(6):
            entry = next((e for e in daily if e["_day_index"] == i), None)
            assert entry is not None
            assert entry["native_temperature"] is not None
            assert entry["native_templow"] is not None

# ─────────────────────────────────────────────────────────────────────────────
# 4. TestForecastContinuity: 예보 항목 개수 및 순서 안정성
# ─────────────────────────────────────────────────────────────────────────────
class TestForecastContinuity:
    def test_forecast_daily_always_10_entries(self):
        # [Given/When] 중기예보 응답이 비어있는 최악의 상황에서도
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        empty_mid = ({"response": {"body": {"items": {"item": [{}]}}}}, 
                     {"response": {"body": {"items": {"item": [{}]}}}}, tm_fc_dt)
        result = api._merge_all(now, make_short_res(now, days=3), empty_mid, {})
        # [Then] 일일 예보는 항상 10일치를 유지해야 함
        assert len(result["weather"]["forecast_daily"]) == 10

    def test_day_index_sequential(self):
        # [Given/When] 병합 완료 후
        api = make_api()
        now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)
        result = api._merge_all(now, make_short_res(now, days=3), make_mid_res(api._get_mid_base_dt(now)), {})
        # [Then] 예보 데이터의 _day_index는 0부터 9까지 순차적으로 정렬되어야 함
        daily_indices = [e["_day_index"] for e in result["weather"]["forecast_daily"]]
        assert daily_indices == list(range(10))

# ─────────────────────────────────────────────────────────────────────────────
# 5. TestBoundaryTimeScenarios: 시각 경계 조건에서의 무결성
# ─────────────────────────────────────────────────────────────────────────────
class TestBoundaryTimeScenarios:
    def test_day4_temperature_matches_expected_mid_key(self):
        # [Given] 오전 5:50 (중기 발표 전환 직전 시각)에 실행되어 전날 18:00 기준 tmFc가 선택되었을 때
        api = make_api()
        now = datetime(2026, 4, 11, 5, 50, tzinfo=TZ)
        tm_fc_dt = api._get_mid_base_dt(now)
        # [When] 데이터를 병합하면
        result = api._merge_all(now, make_short_res(now, days=3),
                                 make_mid_res(tm_fc_dt, start_idx=3, end_idx=10), {})
        # [Then] D+4의 인덱스는 5(4/15 - 4/10)가 되어 taMax5 값이 매핑되어야 함
        entry_4 = next(e for e in result["weather"]["forecast_daily"] if e["_day_index"] == 4)
        assert entry_4["native_temperature"] == float(20 + 5)  # taMax5
