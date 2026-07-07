"""
test_daily_forecast_pop.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
forecast_daily / forecast_twice_daily 의 강수확률(precipitation_probability)
검증. 강수량/풍속/습도는 다른 날씨 서비스 관례와 맞지 않아 추가하지 않기로
결정했으므로 여기서는 강수확률만 다룬다.

핵심 설계 원칙:
  - 강수확률의 시간 범위는 기존 날씨상태(condition) 계산과 반드시 동일해야 함
    (오늘: 지금~12시/12시~24시, 내일 이후: 06~11시/12~17시 또는 12시 슬롯)
  - 중기예보(4일 이후)는 rnSt{n}Am/rnSt{n}Pm 필드를 새로 활용해 채움
"""
import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock

from custom_components.kma_weather.api_kma import KMAWeatherAPI

TZ = ZoneInfo("Asia/Seoul")


def make_api():
    return KMAWeatherAPI(MagicMock(), "TEST_KEY")


def make_short_res(base_date, days=4, pop_by_hour: dict[int, int] | None = None):
    pop_by_hour = pop_by_hour or {}
    items = []
    for d in range(days):
        day = base_date + timedelta(days=d)
        d_str = day.strftime("%Y%m%d")
        for h in range(24):
            t_str = f"{h:02d}00"
            items += [
                {"fcstDate": d_str, "fcstTime": t_str, "category": "TMP", "fcstValue": "20"},
                {"fcstDate": d_str, "fcstTime": t_str, "category": "SKY", "fcstValue": "1"},
                {"fcstDate": d_str, "fcstTime": t_str, "category": "PTY", "fcstValue": "0"},
                {"fcstDate": d_str, "fcstTime": t_str, "category": "POP", "fcstValue": str(pop_by_hour.get(h, 0))},
            ]
    return {"response": {"body": {"items": {"item": items}}}}


def make_mid_res(now, rn_am=None, rn_pm=None, day_idx=4):
    mid_ta = {"response": {"body": {"items": {"item": [{f"taMax{day_idx}": "25", f"taMin{day_idx}": "15"}]}}}}
    land_item = {f"wf{day_idx}Am": "맑음", f"wf{day_idx}Pm": "흐림"}
    if rn_am is not None:
        land_item[f"rnSt{day_idx}Am"] = str(rn_am)
    if rn_pm is not None:
        land_item[f"rnSt{day_idx}Pm"] = str(rn_pm)
    mid_land = {"response": {"body": {"items": {"item": [land_item]}}}}
    return (mid_ta, mid_land, now)


# ─────────────────────────────────────────────────────────────────────────────
# 1. 강수량/풍속/습도는 존재하지 않아야 함 (의도적으로 뺀 필드)
# ─────────────────────────────────────────────────────────────────────────────

class TestExcludedFieldsAreAbsent:
    """다른 날씨 서비스 관례에 맞춰 뺀 필드들이 정말 없는지 확인"""

    def test_daily_has_no_precipitation_amount_wind_or_humidity(self):
        """
        [Given] 임의의 단기예보 데이터
        [When]  _merge_all 호출
        [Then]  forecast_daily 항목에 native_precipitation, native_wind_speed,
                humidity 키가 존재하지 않음 (강수확률만 제공하기로 결정)
        """
        api = make_api()
        now = datetime(2026, 7, 7, 6, 0, tzinfo=TZ)
        short_res = make_short_res(now)

        result = api._merge_all(now, short_res, None, {})
        day0 = result["weather"]["forecast_daily"][0]

        assert "native_precipitation" not in day0
        assert "native_wind_speed" not in day0
        assert "humidity" not in day0

    def test_twice_daily_has_no_precipitation_amount_wind_or_humidity(self):
        """
        [Given] 임의의 단기예보 데이터
        [When]  _merge_all 호출
        [Then]  forecast_twice_daily 항목에도 동일하게 세 필드가 없음
        """
        api = make_api()
        now = datetime(2026, 7, 7, 6, 0, tzinfo=TZ)
        short_res = make_short_res(now)

        result = api._merge_all(now, short_res, None, {})
        entry = result["weather"]["forecast_twice_daily"][0]

        assert "native_precipitation" not in entry
        assert "native_wind_speed" not in entry
        assert "humidity" not in entry


# ─────────────────────────────────────────────────────────────────────────────
# 2. 강수확률 시간 범위가 날씨상태(condition)와 정확히 일치하는지
# ─────────────────────────────────────────────────────────────────────────────

class TestPrecipitationProbabilityMatchesConditionRange:
    """강수확률이 날씨상태 계산과 동일한 시간 범위를 참조하는지 검증"""

    def test_today_am_pm_pop_uses_now_to_noon_and_noon_to_midnight(self):
        """
        [Given] 오늘 오전(지금~12시)엔 POP=20, 오후(12시~24시)엔 POP=70
        [When]  06시에 _merge_all 호출
        [Then]  오전 항목 precipitation_probability=20, 오후=70
                (날씨상태 wf_am/wf_pm과 동일한 시간 경계 사용)
        """
        api = make_api()
        now = datetime(2026, 7, 7, 6, 0, tzinfo=TZ)
        short_res = make_short_res(now, pop_by_hour={h: (20 if h < 12 else 70) for h in range(24)})

        result = api._merge_all(now, short_res, None, {})
        today = [e for e in result["weather"]["forecast_twice_daily"] if e["_day_index"] == 0]
        am = next(e for e in today if e["is_daytime"])
        pm = next(e for e in today if not e["is_daytime"])

        assert am["precipitation_probability"] == 20
        assert pm["precipitation_probability"] == 70

    def test_day2_3_pop_uses_fixed_06_11_and_12_17_range(self):
        """
        [Given] 2일 뒤(i=2) 06~11시 POP=15, 12~17시 POP=85,
                18~23시에는 다른 값(POP=99, 참조되면 안 됨)
        [When]  _merge_all 호출
        [Then]  오전=15(06~11시만 반영), 오후=85(12~17시만 반영)
                → 18~23시 값(99)은 무시되어야 함
                (날씨상태 _get_short_ampm과 동일한 고정 시간 범위)
        """
        api = make_api()
        now = datetime(2026, 7, 7, 6, 0, tzinfo=TZ)
        pop_map = {h: 15 for h in range(6, 12)}
        pop_map.update({h: 85 for h in range(12, 18)})
        pop_map.update({h: 99 for h in range(18, 24)})
        short_res = make_short_res(now, pop_by_hour=pop_map)

        result = api._merge_all(now, short_res, None, {})
        day2 = [e for e in result["weather"]["forecast_twice_daily"] if e["_day_index"] == 2]
        am = next(e for e in day2 if e["is_daytime"])
        pm = next(e for e in day2 if not e["is_daytime"])

        assert am["precipitation_probability"] == 15
        assert pm["precipitation_probability"] == 85

    def test_tomorrow_uses_noon_slot_when_available(self):
        """
        [Given] 내일(i=1)의 12시 슬롯에 POP=45가 존재
        [When]  _merge_all 호출
        [Then]  내일 오전/오후 강수확률 모두 12시 슬롯 값(45)을 그대로 사용
                (날씨상태도 12시 슬롯 하나를 오전/오후 공통으로 쓰는 것과 동일)
        """
        api = make_api()
        now = datetime(2026, 7, 7, 6, 0, tzinfo=TZ)
        short_res = make_short_res(now, pop_by_hour={12: 45})

        result = api._merge_all(now, short_res, None, {})
        tomorrow = [e for e in result["weather"]["forecast_twice_daily"] if e["_day_index"] == 1]
        am = next(e for e in tomorrow if e["is_daytime"])
        pm = next(e for e in tomorrow if not e["is_daytime"])

        assert am["precipitation_probability"] == 45
        assert pm["precipitation_probability"] == 45

    def test_daily_forecast_uses_pm_value_as_representative(self):
        """
        [Given] 오전 POP=10, 오후 POP=90
        [When]  _merge_all 호출
        [Then]  forecast_daily(일 1회 요약)의 precipitation_probability는
                90(오후 값)을 대표로 사용 — condition이 wf_pm을 대표로
                쓰는 것과 동일한 원칙
        """
        api = make_api()
        now = datetime(2026, 7, 7, 6, 0, tzinfo=TZ)
        short_res = make_short_res(now, pop_by_hour={h: (10 if h < 12 else 90) for h in range(24)})

        result = api._merge_all(now, short_res, None, {})

        assert result["weather"]["forecast_daily"][0]["precipitation_probability"] == 90


# ─────────────────────────────────────────────────────────────────────────────
# 3. 중기예보(4일 이후): rnSt{n}Am/Pm 필드 신규 활용
# ─────────────────────────────────────────────────────────────────────────────

class TestMidTermPrecipitationProbability:
    """중기예보 구간도 rnSt{n}Am/Pm 필드로 강수확률을 채우는지 검증"""

    def test_mid_term_day_gets_precipitation_probability_from_rnst_fields(self):
        """
        [Given] 4일 뒤 중기예보 응답에 rnSt4Am=30, rnSt4Pm=60이 포함됨
        [When]  _merge_all 호출
        [Then]  forecast_twice_daily의 4일차 오전=30, 오후=60,
                forecast_daily의 4일차는 오후값(60)을 대표로 사용
                (이전에는 이 정보가 아예 None이었으나 API가 실제로 제공하므로 채움)
        """
        api = make_api()
        now = datetime(2026, 7, 7, 6, 0, tzinfo=TZ)
        short_res = make_short_res(now, days=4)
        mid_res = make_mid_res(now, rn_am=30, rn_pm=60, day_idx=4)

        result = api._merge_all(now, short_res, mid_res, {})

        day4_twice = [e for e in result["weather"]["forecast_twice_daily"] if e["_day_index"] == 4]
        am = next(e for e in day4_twice if e["is_daytime"])
        pm = next(e for e in day4_twice if not e["is_daytime"])
        assert am["precipitation_probability"] == 30
        assert pm["precipitation_probability"] == 60

        day4_daily = result["weather"]["forecast_daily"][4]
        assert day4_daily["precipitation_probability"] == 60

    def test_mid_term_day_without_rnst_fields_stays_none(self):
        """
        [Given] 중기예보 응답에 rnSt 필드가 아예 없는 경우(API 구버전 등 방어)
        [When]  _merge_all 호출
        [Then]  precipitation_probability는 None으로 남고 크래시 없음
        """
        api = make_api()
        now = datetime(2026, 7, 7, 6, 0, tzinfo=TZ)
        short_res = make_short_res(now, days=4)
        mid_res = make_mid_res(now, rn_am=None, rn_pm=None, day_idx=4)

        result = api._merge_all(now, short_res, mid_res, {})

        assert result["weather"]["forecast_daily"][4]["precipitation_probability"] is None
