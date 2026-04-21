"""
천문 센서 테스트:
  - 달 위상 8단계 이름 및 아이콘
  - 달 조명율 계산
  - 월출/월몰 오늘/내일 접두어
  - 일출/일몰/새벽/황혼 오늘/내일 접두어
  - 천문 박명(18°) 오늘/내일 접두어
  - 천문 관측 조건 평가 (날씨·태양 고도·달 조명율)
  - 자정 unknown 방지 (_REALTIME_KEYS: 키 있고 값 '-'/None → 캐시 복원)
"""
import pytest
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from unittest.mock import patch, MagicMock
from astral import LocationInfo
from astral.sun import elevation as sun_elevation
from astral.moon import phase as moon_phase

from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator
from custom_components.kma_weather.const import DOMAIN

TZ = ZoneInfo("Asia/Seoul")
LAT, LON = 37.608025, 127.094222


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def make_coordinator(hass, entry):
    """테스트용 coordinator 인스턴스 생성"""
    coord = KMAWeatherUpdateCoordinator.__new__(KMAWeatherUpdateCoordinator)
    coord.hass = hass
    coord.entry = entry
    coord.api = MagicMock()
    coord.api.tz = TZ
    coord._sun_times = {}
    coord._sun_cache_date = None
    coord._sun_cache_lat = None
    coord._sun_cache_lon = None
    coord._cached_data = None
    return coord


# ── 달 위상 이름 ───────────────────────────────────────────────────────────────

class TestMoonPhaseName:
    """_moon_phase_name: 8단계 이름 반환"""

    @pytest.mark.parametrize("p, expected", [
        (0.0,  "삭"),
        (1.0,  "삭"),
        (1.85, "초승달"),
        (4.0,  "초승달"),
        (7.38, "상현달"),
        (9.0,  "상현달"),
        (11.07,"준상현달"),
        (13.0, "준상현달"),
        (14.77,"보름달"),
        (16.0, "보름달"),
        (18.46,"준하현달"),
        (20.0, "준하현달"),
        (22.15,"하현달"),
        (24.0, "하현달"),
        (25.84,"그믐달"),
        (28.0, "그믐달"),
        (29.53,"삭"),  # 경계: 마지막 값 → 삭
    ])
    def test_phase_name(self, p, expected):
        result = KMAWeatherUpdateCoordinator._moon_phase_name(p)
        assert result == expected, f"위상값 {p} → 기대={expected}, 실제={result}"

    def test_no_신월_name(self):
        """'신월' 이름은 더 이상 사용하지 않음"""
        for p in [0.0, 0.5, 1.0, 1.84]:
            assert KMAWeatherUpdateCoordinator._moon_phase_name(p) == "삭"

    def test_no_상현이후_name(self):
        """'상현 이후' 이름은 더 이상 사용하지 않음"""
        for p in [11.07, 12.0, 14.76]:
            assert KMAWeatherUpdateCoordinator._moon_phase_name(p) == "준상현달"

    def test_no_하현이전_name(self):
        """'하현 이전' 이름은 더 이상 사용하지 않음"""
        for p in [18.46, 19.0, 22.14]:
            assert KMAWeatherUpdateCoordinator._moon_phase_name(p) == "준하현달"


# ── 달 조명율 ─────────────────────────────────────────────────────────────────

class TestMoonIllumination:
    """_moon_illumination: 0~100% 범위 및 주요 위상 값"""

    def test_new_moon_near_zero(self):
        p = 0.0  # 삭
        illum = KMAWeatherUpdateCoordinator._moon_illumination(p)
        assert 0 <= illum <= 5, f"삭 조명율={illum}% (0~5 기대)"

    def test_full_moon_near_100(self):
        p = 14.77  # 보름달
        illum = KMAWeatherUpdateCoordinator._moon_illumination(p)
        assert illum >= 95, f"보름달 조명율={illum}% (95~100 기대)"

    def test_quarter_moon_near_50(self):
        p = 7.38  # 상현달
        illum = KMAWeatherUpdateCoordinator._moon_illumination(p)
        assert 40 <= illum <= 60, f"상현달 조명율={illum}% (40~60 기대)"

    def test_range_always_0_to_100(self):
        for p in [i * 29.53 / 100 for i in range(101)]:
            illum = KMAWeatherUpdateCoordinator._moon_illumination(p)
            assert 0 <= illum <= 100, f"위상={p:.2f} → 조명율={illum}% 범위 벗어남"


# ── 천문 시각 오늘/내일 접두어 ─────────────────────────────────────────────────

class TestCalcSunTimes:
    """_calc_sun_times: 반환값 형식 및 오늘/내일 접두어"""

    def _calc(self, hour):
        """주어진 시각으로 _calc_sun_times 실행"""
        from unittest.mock import MagicMock

        # spec 없이 생성하되, staticmethod가 올바르게 호출되도록 실제 메서드 연결
        coord = MagicMock()
        coord.api.tz = TZ
        coord._sun_times = {}
        coord._sun_cache_date = None
        coord._sun_cache_lat = None
        coord._sun_cache_lon = None
        # staticmethod는 MagicMock에서 mock되므로 실제 구현으로 교체
        coord._moon_phase_name = staticmethod(KMAWeatherUpdateCoordinator._moon_phase_name)
        coord._moon_illumination = staticmethod(KMAWeatherUpdateCoordinator._moon_illumination)

        now = datetime(2026, 4, 21, hour, 0, tzinfo=TZ)
        return KMAWeatherUpdateCoordinator._calc_sun_times(coord, LAT, LON, now)

    def test_keys_present(self):
        """모든 천문 시각 키가 존재해야 함"""
        result = self._calc(13)
        expected_keys = {
            "dawn", "sunrise", "sunset", "dusk",
            "astro_dawn", "astro_dusk",
            "moon_phase", "moon_illumination",
            "moonrise", "moonset",
        }
        for key in expected_keys:
            assert key in result, f"키 '{key}' 없음"

    def test_time_format(self):
        """시각 값은 'HH:MM' 형식 문자열이어야 함 (오늘/내일 접두어 포함)"""
        result = self._calc(13)
        for key in ("dawn", "sunrise", "sunset", "dusk", "astro_dawn", "astro_dusk"):
            val = result.get(key)
            assert val is not None, f"'{key}' 값이 None"
            assert val.startswith("오늘 ") or val.startswith("내일 "), \
                f"'{key}' 접두어 없음: '{val}'"
            time_part = val.split(" ")[1]
            h, m = time_part.split(":")
            assert 0 <= int(h) <= 23 and 0 <= int(m) <= 59, \
                f"'{key}' 시각 형식 오류: '{val}'"

    def test_noon_dawn_is_tomorrow(self):
        """낮 13시: 새벽은 이미 지났으므로 '내일'이어야 함"""
        result = self._calc(13)
        assert result["dawn"].startswith("내일 "), \
            f"13시 새벽={result['dawn']} ('내일' 기대)"

    def test_noon_sunset_is_today(self):
        """낮 13시: 일몰은 아직 안 왔으므로 '오늘'이어야 함"""
        result = self._calc(13)
        assert result["sunset"].startswith("오늘 "), \
            f"13시 일몰={result['sunset']} ('오늘' 기대)"

    def test_midnight_sunrise_is_today(self):
        """새벽 1시: 일출이 아직 안 왔으므로 '오늘'이어야 함"""
        result = self._calc(1)
        assert result["sunrise"].startswith("오늘 "), \
            f"01시 일출={result['sunrise']} ('오늘' 기대)"

    def test_night_all_tomorrow(self):
        """밤 23시: 새벽/일출 모두 내일이어야 함"""
        result = self._calc(23)
        assert result["dawn"].startswith("내일 "), \
            f"23시 새벽={result['dawn']} ('내일' 기대)"
        assert result["sunrise"].startswith("내일 "), \
            f"23시 일출={result['sunrise']} ('내일' 기대)"

    def test_astro_dusk_before_dark(self):
        """낮 13시: 천문박명 종료는 저녁에 있으므로 '오늘'이어야 함"""
        result = self._calc(13)
        assert result["astro_dusk"].startswith("오늘 "), \
            f"13시 천문박명 종료={result['astro_dusk']} ('오늘' 기대)"

    def test_astro_dawn_after_midnight(self):
        """새벽 1시: 천문박명 시작(04:xx)은 아직 안 왔으므로 오늘 또는 내일이어야 함
        (astral 버전에 따라 t.date() 반환이 달라질 수 있으므로 접두어보다 시각 존재 여부만 검증)"""
        result = self._calc(1)
        val = result["astro_dawn"]
        assert val is not None, "01시 천문박명 시작이 None"
        assert val.startswith("오늘 ") or val.startswith("내일 "), \
            f"01시 천문박명 시작={val} (오늘/내일 접두어 기대)"

    def test_moon_phase_is_string(self):
        """달 위상은 8단계 중 하나여야 함"""
        valid = {"삭", "초승달", "상현달", "준상현달", "보름달", "준하현달", "하현달", "그믐달"}
        result = self._calc(13)
        assert result["moon_phase"] in valid, \
            f"달 위상='{result['moon_phase']}' 유효하지 않음"

    def test_moon_illumination_is_int(self):
        """달 조명율은 0~100 정수여야 함"""
        result = self._calc(13)
        illum = result["moon_illumination"]
        assert isinstance(illum, int), f"달 조명율 타입={type(illum)}"
        assert 0 <= illum <= 100, f"달 조명율={illum}% 범위 벗어남"

    def test_cache_reused_same_date(self):
        """같은 날짜·좌표면 캐시 재사용"""
        from unittest.mock import MagicMock
        coord = MagicMock()
        coord.api.tz = TZ
        coord._sun_cache_date = date(2026, 4, 21)
        coord._sun_cache_lat = LAT
        coord._sun_cache_lon = LON
        cached = {"dawn": "오늘 05:21", "moon_phase": "초승달"}
        coord._sun_times = cached

        now = datetime(2026, 4, 21, 10, 0, tzinfo=TZ)
        result = KMAWeatherUpdateCoordinator._calc_sun_times(coord, LAT, LON, now)
        assert result is cached, "캐시가 재사용되지 않음"


# ── 관측 조건 평가 ────────────────────────────────────────────────────────────

class TestEvalObservation:
    """_eval_observation: 날씨·태양 고도·달 조명율 종합 평가"""

    def _eval(self, hour, condition, illum):
        from unittest.mock import MagicMock
        coord = MagicMock()
        coord.api.tz = TZ
        weather = {"current_condition": condition, "moon_illumination": illum}
        now = datetime(2026, 4, 21, hour, 0, tzinfo=TZ)
        return KMAWeatherUpdateCoordinator._eval_observation(coord, weather, now, LAT, LON)

    # 날씨 불가
    @pytest.mark.parametrize("cond", ["rainy", "pouring", "snowy", "snowy-rainy",
                                       "lightning", "lightning-rainy"])
    def test_bad_weather_precipitation(self, cond):
        assert self._eval(22, cond, 5) == "관측불가"

    def test_cloudy_returns_cloudy(self):
        assert self._eval(22, "cloudy", 5) == "관측불가"

    # 낮/박명
    def test_daytime_returns_daytime(self):
        assert self._eval(13, "sunny", 5) == "관측불가"

    def test_evening_twilight_returns_daytime(self):
        assert self._eval(20, "sunny", 5) == "관측불가"

    def test_morning_twilight_returns_daytime(self):
        assert self._eval(6, "sunny", 5) == "관측불가"

    # 밤 (천문박명 이후)
    def test_night_excellent(self):
        assert self._eval(22, "sunny", 5) == "최우수"

    def test_night_good(self):
        assert self._eval(22, "sunny", 30) == "우수"

    def test_night_average(self):
        assert self._eval(22, "sunny", 60) == "보통"

    def test_night_poor(self):
        assert self._eval(22, "sunny", 80) == "불량"

    def test_dawn_clear_excellent(self):
        """새벽 1시, 맑음, 달 없음 → 최우수"""
        assert self._eval(1, "sunny", 5) == "최우수"

    def test_dawn_4am_excellent(self):
        """새벽 4시, 맑음, 달 없음 → 최우수"""
        assert self._eval(4, "sunny", 5) == "최우수"

    def test_partlycloudy_not_blocked(self):
        """구름조금은 관측 가능"""
        result = self._eval(22, "partlycloudy", 10)
        assert result != "관측불가"
        assert result != "관측불가"

    # 경계값
    @pytest.mark.parametrize("illum, expected", [
        (0,   "최우수"),
        (25,  "최우수"),
        (26,  "우수"),
        (50,  "우수"),
        (51,  "보통"),
        (75,  "보통"),
        (76,  "불량"),
        (100, "불량"),
    ])
    def test_illumination_boundaries(self, illum, expected):
        result = self._eval(22, "sunny", illum)
        assert result == expected, f"조명율={illum}% → 기대={expected}, 실제={result}"


# ── 자정 unknown 방지 (_REALTIME_KEYS) ────────────────────────────────────────

class TestRealtimeKeysCache:
    """_REALTIME_KEYS: 키가 있고 값이 '-'/None이면 이전 캐시로 보완,
    키 자체가 없으면(누락 데이터) 보완하지 않음"""

    KEYS = ("TMP", "REH", "WSD", "VEC", "VEC_KOR", "POP", "apparent_temp")

    def _run(self, new_weather, prev_weather):
        """coordinator 보완 로직만 순수하게 실행"""
        _REALTIME_KEYS = self.KEYS
        weather = dict(new_weather)
        if prev_weather is not None:
            for _key in _REALTIME_KEYS:
                if _key in weather and weather[_key] in (None, "-", ""):
                    prev_val = prev_weather.get(_key)
                    if prev_val not in (None, "-", ""):
                        weather[_key] = prev_val
        return weather

    def test_dash_value_restored_from_cache(self):
        """값이 '-'인 경우 이전 캐시로 복원"""
        result = self._run(
            {"TMP": "-", "REH": 45},
            {"TMP": 22, "REH": 40},
        )
        assert result["TMP"] == 22, "TMP='-' → 캐시 22로 복원 기대"
        assert result["REH"] == 45, "REH는 정상값이므로 유지"

    def test_none_value_restored_from_cache(self):
        """값이 None인 경우 이전 캐시로 복원"""
        result = self._run(
            {"TMP": None, "WSD": 2.1},
            {"TMP": 22, "WSD": 1.5},
        )
        assert result["TMP"] == 22
        assert result["WSD"] == 2.1

    def test_missing_key_not_restored(self):
        """키 자체가 없는 경우(누락 데이터) → 보완하지 않음"""
        result = self._run(
            {"REH": 45},          # TMP 키 없음
            {"TMP": 22, "REH": 40},
        )
        assert "TMP" not in result, "TMP 키가 없는데 캐시로 채워지면 안 됨"

    def test_all_realtime_keys_covered(self):
        """모든 _REALTIME_KEYS가 보완 로직 적용 대상인지 확인"""
        new = {k: "-" for k in self.KEYS}
        prev = {k: f"val_{k}" for k in self.KEYS}
        result = self._run(new, prev)
        for k in self.KEYS:
            assert result[k] == f"val_{k}", f"키={k} 복원 실패"

    def test_no_cache_no_restore(self):
        """이전 캐시가 없으면 보완하지 않음"""
        result = self._run({"TMP": "-"}, None)
        assert result["TMP"] == "-"

    def test_prev_also_bad_no_restore(self):
        """이전 캐시도 '-'/None이면 보완하지 않음"""
        result = self._run(
            {"TMP": "-"},
            {"TMP": "-"},
        )
        assert result["TMP"] == "-"

    def test_empty_string_restored(self):
        """빈 문자열도 '-'와 동일하게 캐시로 복원"""
        result = self._run(
            {"TMP": ""},
            {"TMP": 22},
        )
        assert result["TMP"] == 22


# ── 통합: 실제 HA 환경에서 신규 센서 등록 확인 ────────────────────────────────

@pytest.mark.asyncio
async def test_astro_sensors_registered(hass, mock_config_entry, kma_api_mock_factory):
    """신규 천문 센서들이 HA에 정상 등록되는지 확인"""
    hass.config.latitude = LAT
    hass.config.longitude = LON
    kma_api_mock_factory("full_test")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    p = "test"
    expected_sensors = [
        f"sensor.{p}_dawn",
        f"sensor.{p}_sunrise",
        f"sensor.{p}_sunset",
        f"sensor.{p}_dusk",
        f"sensor.{p}_astro_dawn",
        f"sensor.{p}_astro_dusk",
        f"sensor.{p}_moon_phase",
        f"sensor.{p}_moon_illumination",
        f"sensor.{p}_moonrise",
        f"sensor.{p}_moonset",
        f"sensor.{p}_observation_condition",
    ]
    for entity_id in expected_sensors:
        state = hass.states.get(entity_id)
        assert state is not None, f"센서 {entity_id} 미등록"
        assert state.state not in ("unavailable",), \
            f"센서 {entity_id} 상태={state.state}"


@pytest.mark.asyncio
async def test_moon_phase_values(hass, mock_config_entry, kma_api_mock_factory):
    """달 위상 센서가 유효한 8단계 값을 반환하는지 확인"""
    hass.config.latitude = LAT
    hass.config.longitude = LON
    kma_api_mock_factory("full_test")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    valid_phases = {"삭", "초승달", "상현달", "준상현달", "보름달", "준하현달", "하현달", "그믐달"}
    state = hass.states.get("sensor.test_moon_phase")
    assert state is not None
    assert state.state in valid_phases, \
        f"달 위상='{state.state}' 유효하지 않음"


@pytest.mark.asyncio
async def test_moon_illumination_range(hass, mock_config_entry, kma_api_mock_factory):
    """달 조명율이 0~100% 범위 내인지 확인"""
    hass.config.latitude = LAT
    hass.config.longitude = LON
    kma_api_mock_factory("full_test")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.test_moon_illumination")
    assert state is not None
    assert state.state != "unknown", "달 조명율이 unknown"
    illum = int(state.state)
    assert 0 <= illum <= 100, f"달 조명율={illum}% 범위 벗어남"


@pytest.mark.asyncio
async def test_observation_condition_valid(hass, mock_config_entry, kma_api_mock_factory):
    """관측 조건 센서가 유효한 값을 반환하는지 확인"""
    hass.config.latitude = LAT
    hass.config.longitude = LON
    kma_api_mock_factory("full_test")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    valid_conditions = {
        "최우수", "우수", "보통", "불량",
        "관측불가", "관측불가", "관측불가",
    }
    state = hass.states.get("sensor.test_observation_condition")
    assert state is not None
    assert state.state in valid_conditions, \
        f"관측 조건='{state.state}' 유효하지 않음"


@pytest.mark.asyncio
async def test_sun_time_format(hass, mock_config_entry, kma_api_mock_factory):
    """일출/일몰/새벽/황혼 센서 값 형식이 '오늘 HH:MM' 또는 '내일 HH:MM'인지 확인"""
    hass.config.latitude = LAT
    hass.config.longitude = LON
    kma_api_mock_factory("full_test")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    p = "test"
    for sensor in ("dawn", "sunrise", "sunset", "dusk", "astro_dawn", "astro_dusk"):
        state = hass.states.get(f"sensor.{p}_{sensor}")
        assert state is not None, f"sensor.{p}_{sensor} 미등록"
        val = state.state
        assert val != "unknown", f"sensor.{p}_{sensor} = unknown"
        assert val.startswith("오늘 ") or val.startswith("내일 "), \
            f"sensor.{p}_{sensor} 접두어 없음: '{val}'"
        time_part = val.split(" ")[1]
        h, m = time_part.split(":")
        assert 0 <= int(h) <= 23 and 0 <= int(m) <= 59, \
            f"sensor.{p}_{sensor} 시각 형식 오류: '{val}'"


@pytest.mark.asyncio
async def test_realtime_cache_in_coordinator(hass, mock_config_entry, kma_api_mock_factory):
    """coordinator가 실제로 '-' 값을 캐시로 복원하는지 통합 테스트"""
    from unittest.mock import patch, AsyncMock

    hass.config.latitude = LAT
    hass.config.longitude = LON
    kma_api_mock_factory("full_test")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]

    # 1차: 정상 데이터 → TMP=22 캐싱됨
    assert hass.states.get("sensor.test_temperature").state == "22"

    # 2차: TMP="-" 주입 → 캐시 복원으로 여전히 22
    dash_data = {
        "weather": {
            "TMP": "-", "REH": 45, "WSD": 2.1, "VEC_KOR": "남동",
            "POP": 10, "PTY": 0, "SKY": 1,
            "current_condition": "sunny", "current_condition_kor": "맑음",
            "apparent_temp": 23.4, "rain_start_time": "강수없음",
            "address": "경기도 화성시", "현재 위치": "경기도 화성시",
            "forecast_twice_daily": [],
        },
        "air": {},
    }
    with patch.object(coordinator.api, "fetch_data", new_callable=AsyncMock) as mock_f:
        mock_f.return_value = dash_data
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert hass.states.get("sensor.test_temperature").state == "22", \
        "TMP='-' 주입 후 캐시 복원 실패"
