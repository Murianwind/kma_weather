"""
test_daily_forecast_fields.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
forecast_daily / forecast_twice_daily 에 강수확률·강수량·풍속·습도 필드
추가 검증 (weather.get_forecasts 액션에서 일간/오전오후 예보에 정보가
없다는 이슈 대응)

검증 대상:
  - api_kma.py::_merge_all() 의 forecast_daily, forecast_twice_daily
"""
import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock

from custom_components.kma_weather.api_kma import KMAWeatherAPI

TZ = ZoneInfo("Asia/Seoul")


def make_api():
    return KMAWeatherAPI(MagicMock(), "TEST_KEY")


def make_short_res(base_date, days=4, pop_by_hour=None, wsd_by_hour=None,
                    reh_by_hour=None, pcp_by_hour=None):
    """
    시간대별 POP/WSD/REH/PCP를 지정할 수 있는 단기예보 응답 생성.
    지정 안 한 시각은 POP=0, WSD=2.0, REH=50, PCP=강수없음 기본값 사용.
    """
    pop_by_hour = pop_by_hour or {}
    wsd_by_hour = wsd_by_hour or {}
    reh_by_hour = reh_by_hour or {}
    pcp_by_hour = pcp_by_hour or {}
    items = []
    for d in range(days):
        day = base_date + timedelta(days=d)
        d_str = day.strftime("%Y%m%d")
        for h in range(24):
            t_str = f"{h:02d}00"
            items += [
                {"fcstDate": d_str, "fcstTime": t_str, "category": "TMP", "fcstValue": str(15 + h % 10)},
                {"fcstDate": d_str, "fcstTime": t_str, "category": "SKY", "fcstValue": "1"},
                {"fcstDate": d_str, "fcstTime": t_str, "category": "PTY", "fcstValue": "0"},
                {"fcstDate": d_str, "fcstTime": t_str, "category": "POP", "fcstValue": str(pop_by_hour.get(h, 0))},
                {"fcstDate": d_str, "fcstTime": t_str, "category": "WSD", "fcstValue": str(wsd_by_hour.get(h, 2.0))},
                {"fcstDate": d_str, "fcstTime": t_str, "category": "REH", "fcstValue": str(reh_by_hour.get(h, 50))},
                {"fcstDate": d_str, "fcstTime": t_str, "category": "PCP", "fcstValue": pcp_by_hour.get(h, "강수없음")},
            ]
    return {"response": {"body": {"items": {"item": items}}}}


# ─────────────────────────────────────────────────────────────────────────────
# 1. forecast_daily 필드 채워짐 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestDailyForecastExtraFields:
    """단기예보 구간(0~3일)의 forecast_daily에 강수확률 등이 채워지는지 검증"""

    def test_short_term_day_has_precipitation_probability(self):
        """
        [Given] 단기예보 구간(오늘)에 시간대별 POP 값이 존재
        [When]  _merge_all 호출
        [Then]  forecast_daily[0]의 precipitation_probability가
                그날 시간대 중 최댓값으로 채워짐
        """
        api = make_api()
        now = datetime(2026, 7, 7, 6, 0, tzinfo=TZ)
        short_res = make_short_res(now, pop_by_hour={9: 30, 15: 80, 20: 20})

        result = api._merge_all(now, short_res, None, {})

        assert result["weather"]["forecast_daily"][0]["precipitation_probability"] == 80

    def test_short_term_day_has_precipitation_amount_sum(self):
        """
        [Given] 하루 중 여러 시간대에 강수량이 기록됨
        [When]  _merge_all 호출
        [Then]  forecast_daily[0]의 native_precipitation은 하루 합계
        """
        api = make_api()
        now = datetime(2026, 7, 7, 6, 0, tzinfo=TZ)
        short_res = make_short_res(now, pcp_by_hour={9: "2.0mm", 15: "3.0mm"})

        result = api._merge_all(now, short_res, None, {})

        assert result["weather"]["forecast_daily"][0]["native_precipitation"] == 5.0

    def test_short_term_day_has_average_wind_speed(self):
        """
        [Given] 하루 시간대별 풍속이 다양하게 기록됨
        [When]  _merge_all 호출
        [Then]  forecast_daily[0]의 native_wind_speed는 하루 평균값
        """
        api = make_api()
        now = datetime(2026, 7, 7, 6, 0, tzinfo=TZ)
        # 24시간 전부 동일 값이면 평균도 동일값이 되어 검증이 쉬움
        short_res = make_short_res(now, wsd_by_hour={h: 4.0 for h in range(24)})

        result = api._merge_all(now, short_res, None, {})

        assert result["weather"]["forecast_daily"][0]["native_wind_speed"] == 4.0

    def test_short_term_day_has_average_humidity(self):
        """
        [Given] 하루 시간대별 습도가 동일하게 기록됨
        [When]  _merge_all 호출
        [Then]  forecast_daily[0]의 humidity는 하루 평균값(정수)
        """
        api = make_api()
        now = datetime(2026, 7, 7, 6, 0, tzinfo=TZ)
        short_res = make_short_res(now, reh_by_hour={h: 70 for h in range(24)})

        result = api._merge_all(now, short_res, None, {})

        assert result["weather"]["forecast_daily"][0]["humidity"] == 70

    def test_snow_day_uses_sno_for_precipitation_amount(self):
        """
        [Given] PTY=3(눈)인 시간대에 강수량 대신 적설량이 기록됨
        [When]  _merge_all 호출
        [Then]  native_precipitation 계산 시 PCP가 아닌 SNO 값 사용
        """
        api = make_api()
        now = datetime(2026, 1, 15, 6, 0, tzinfo=TZ)
        items = []
        for h in range(24):
            t_str = f"{h:02d}00"
            pty = "3" if h == 9 else "0"
            items += [
                {"fcstDate": "20260115", "fcstTime": t_str, "category": "TMP", "fcstValue": "0"},
                {"fcstDate": "20260115", "fcstTime": t_str, "category": "SKY", "fcstValue": "1"},
                {"fcstDate": "20260115", "fcstTime": t_str, "category": "PTY", "fcstValue": pty},
                {"fcstDate": "20260115", "fcstTime": t_str, "category": "POP", "fcstValue": "0"},
                {"fcstDate": "20260115", "fcstTime": t_str, "category": "WSD", "fcstValue": "1.0"},
                {"fcstDate": "20260115", "fcstTime": t_str, "category": "REH", "fcstValue": "50"},
                {"fcstDate": "20260115", "fcstTime": t_str, "category": "PCP", "fcstValue": "강수없음"},
                {"fcstDate": "20260115", "fcstTime": t_str, "category": "SNO", "fcstValue": "5.0cm" if h == 9 else "적설없음"},
            ]
        short_res = {"response": {"body": {"items": {"item": items}}}}

        result = api._merge_all(now, short_res, None, {})

        assert result["weather"]["forecast_daily"][0]["native_precipitation"] == 5.0

    def test_no_data_returns_none_not_zero(self):
        """
        [Given] POP/WSD/REH 카테고리가 아예 없는 시간대만 있는 경우
        [When]  _merge_all 호출
        [Then]  0으로 오해할 값이 아니라 None으로 남아야 함
                (실제로 "비 올 확률 0%"인지 "데이터 없음"인지 구분되어야 하므로)
        """
        api = make_api()
        now = datetime(2026, 7, 7, 6, 0, tzinfo=TZ)
        items = [
            {"fcstDate": "20260707", "fcstTime": "0600", "category": "TMP", "fcstValue": "20"},
            {"fcstDate": "20260707", "fcstTime": "0600", "category": "SKY", "fcstValue": "1"},
            {"fcstDate": "20260707", "fcstTime": "0600", "category": "PTY", "fcstValue": "0"},
            # POP/WSD/REH 카테고리 자체가 없음
        ]
        short_res = {"response": {"body": {"items": {"item": items}}}}

        result = api._merge_all(now, short_res, None, {})
        d0 = result["weather"]["forecast_daily"][0]

        assert d0["precipitation_probability"] is None
        assert d0["native_wind_speed"] is None
        assert d0["humidity"] is None


# ─────────────────────────────────────────────────────────────────────────────
# 2. forecast_twice_daily (오전/오후) 필드 채워짐 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestTwiceDailyForecastExtraFields:
    """오전/오후 예보가 각각 독립적으로 집계되는지 검증"""

    def test_am_and_pm_have_independent_precipitation_probability(self):
        """
        [Given] 오전에는 강수확률이 낮고, 오후에는 높게 설정된 하루
        [When]  _merge_all 호출
        [Then]  오전/오후 항목의 precipitation_probability가 서로 다르게,
                각 시간대에 맞게 반영됨
        """
        api = make_api()
        now = datetime(2026, 7, 7, 0, 30, tzinfo=TZ)  # 자정 직후 → 오늘 오전/오후 모두 생성
        short_res = make_short_res(now, pop_by_hour={9: 10, 20: 90})

        result = api._merge_all(now, short_res, None, {})
        today_entries = [e for e in result["weather"]["forecast_twice_daily"] if e["_day_index"] == 0]
        am_entry = next(e for e in today_entries if e["is_daytime"])
        pm_entry = next(e for e in today_entries if not e["is_daytime"])

        assert am_entry["precipitation_probability"] == 10
        assert pm_entry["precipitation_probability"] == 90

    def test_am_and_pm_have_independent_wind_and_humidity(self):
        """
        [Given] 오전/오후 풍속·습도가 서로 다르게 설정된 하루
        [When]  _merge_all 호출
        [Then]  오전/오후 각각 자신의 시간대 평균값만 반영 (서로 섞이지 않음)
        """
        api = make_api()
        now = datetime(2026, 7, 7, 0, 30, tzinfo=TZ)
        short_res = make_short_res(
            now,
            wsd_by_hour={h: 1.0 for h in range(12)} | {h: 9.0 for h in range(12, 24)},
            reh_by_hour={h: 30 for h in range(12)} | {h: 80 for h in range(12, 24)},
        )

        result = api._merge_all(now, short_res, None, {})
        today_entries = [e for e in result["weather"]["forecast_twice_daily"] if e["_day_index"] == 0]
        am_entry = next(e for e in today_entries if e["is_daytime"])
        pm_entry = next(e for e in today_entries if not e["is_daytime"])

        assert am_entry["native_wind_speed"] == 1.0
        assert pm_entry["native_wind_speed"] == 9.0
        assert am_entry["humidity"] == 30
        assert pm_entry["humidity"] == 80


# ─────────────────────────────────────────────────────────────────────────────
# 3. 중기예보 구간(4일 이후) - API 자체 한계로 None 유지 확인
# ─────────────────────────────────────────────────────────────────────────────

class TestMidTermDaysHaveNoDetailedFields:
    """중기예보(4일 이후)는 최고/최저기온·날씨상태만 제공되고
    강수확률/강수량/풍속/습도는 API 자체에 없으므로 None이어야 함"""

    def test_mid_term_day_precip_and_wind_are_none(self):
        """
        [Given] 단기예보만 있고 중기예보 데이터는 없음(mid_res=None)
        [When]  _merge_all 호출
        [Then]  4일 이후(forecast_daily[4] 이상)는 기온도 조건도 없고
                강수확률/강수량/풍속/습도도 전부 None
                (중기예보 API 자체가 이 정보를 제공하지 않으므로 정상 동작)
        """
        api = make_api()
        now = datetime(2026, 7, 7, 6, 0, tzinfo=TZ)
        short_res = make_short_res(now, days=4)  # 0~3일치만 존재

        result = api._merge_all(now, short_res, None, {})
        day4 = result["weather"]["forecast_daily"][4]

        assert day4["precipitation_probability"] is None
        assert day4["native_precipitation"] is None
        assert day4["native_wind_speed"] is None
        assert day4["humidity"] is None
