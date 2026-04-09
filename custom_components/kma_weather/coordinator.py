import logging
import asyncio
import math
from datetime import datetime, timedelta, timezone
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .api_kma import KMAWeatherAPI
from .const import DOMAIN, CONF_API_KEY, CONF_LOCATION_ENTITY, convert_grid

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 기상청 중기예보 기온 구역코드 (getMidTa) 좌표 테이블
#
# 출처: custom_components/kma_weather/region_codes.json (기상청 공식 목록)
# 각 항목: temp_id → (위도, 경도)  — 해당 지점의 대표 좌표 (도심/관측소 기준)
#
# 실제 동작: 엔티티 좌표에서 가장 가까운 지점을 haversine 거리로 탐색
# 유지보수: region_codes.json에 지점 추가/수정 시 이 테이블만 갱신하면 됨
# ---------------------------------------------------------------------------
_TEMP_ID_COORDS: dict[str, tuple[float, float]] = {
    # ── 서해5도 ──────────────────────────────────────────
    "11A00101": (37.96, 124.71),  # 백령도

    # ── 서울 / 경기 / 인천 ───────────────────────────────
    "11B10101": (37.56, 126.98),  # 서울
    "11B10102": (37.43, 126.99),  # 과천
    "11B10103": (37.48, 126.87),  # 광명
    "11B20101": (37.74, 126.49),  # 강화
    "11B20102": (37.61, 126.71),  # 김포
    "11B20201": (37.46, 126.70),  # 인천
    "11B20202": (37.38, 126.80),  # 시흥
    "11B20203": (37.32, 126.83),  # 안산
    "11B20204": (37.50, 126.78),  # 부천
    "11B20301": (37.74, 127.03),  # 의정부
    "11B20302": (37.66, 126.83),  # 고양
    "11B20304": (37.78, 127.04),  # 양주
    "11B20305": (37.76, 126.78),  # 파주
    "11B20401": (37.90, 127.06),  # 동두천
    "11B20402": (38.09, 127.07),  # 연천
    "11B20403": (37.90, 127.20),  # 포천
    "11B20404": (37.83, 127.51),  # 가평
    "11B20501": (37.60, 127.13),  # 구리
    "11B20502": (37.64, 127.22),  # 남양주
    "11B20503": (37.49, 127.49),  # 양평
    "11B20504": (37.54, 127.21),  # 하남
    "11B20601": (37.26, 127.02),  # 수원
    "11B20602": (37.39, 126.96),  # 안양
    "11B20603": (37.15, 127.07),  # 오산
    "11B20604": (37.19, 126.83),  # 화성
    "11B20605": (37.45, 127.14),  # 성남
    "11B20606": (36.99, 127.11),  # 평택
    "11B20609": (37.34, 126.97),  # 의왕
    "11B20610": (37.36, 126.93),  # 군포
    "11B20611": (37.01, 127.27),  # 안성
    "11B20612": (37.24, 127.18),  # 용인
    "11B20701": (37.27, 127.44),  # 이천
    "11B20702": (37.43, 127.26),  # 경기 광주
    "11B20703": (37.30, 127.64),  # 여주

    # ── 충청북도 ─────────────────────────────────────────
    "11C10101": (36.98, 127.93),  # 충주
    "11C10102": (36.86, 127.44),  # 진천
    "11C10103": (36.94, 127.69),  # 음성
    "11C10201": (37.13, 128.19),  # 제천
    "11C10202": (36.98, 128.37),  # 단양
    "11C10301": (36.64, 127.49),  # 청주
    "11C10302": (36.49, 127.73),  # 보은
    "11C10303": (36.82, 127.79),  # 괴산
    "11C10304": (36.79, 127.58),  # 증평
    "11C10401": (36.22, 128.02),  # 추풍령
    "11C10402": (36.17, 127.78),  # 영동
    "11C10403": (36.30, 127.57),  # 옥천

    # ── 충청남도 ─────────────────────────────────────────
    "11C20101": (36.78, 126.45),  # 서산
    "11C20102": (36.74, 126.30),  # 태안
    "11C20103": (36.89, 126.63),  # 당진
    "11C20104": (36.60, 126.66),  # 홍성
    "11C20201": (36.33, 126.61),  # 보령
    "11C20202": (36.08, 126.69),  # 서천
    "11C20301": (36.81, 127.15),  # 천안
    "11C20302": (36.79, 127.00),  # 아산
    "11C20303": (36.68, 126.85),  # 예산
    "11C20401": (36.35, 127.38),  # 대전
    "11C20402": (36.44, 127.11),  # 공주
    "11C20403": (36.27, 127.25),  # 계룡
    "11C20404": (36.48, 127.29),  # 세종
    "11C20501": (36.27, 126.91),  # 부여
    "11C20502": (36.45, 126.80),  # 청양
    "11C20601": (36.11, 127.49),  # 금산
    "11C20602": (36.19, 127.10),  # 논산

    # ── 강원 영서 ────────────────────────────────────────
    "11D10101": (38.15, 127.31),  # 철원
    "11D10102": (38.11, 127.71),  # 화천
    "11D10201": (38.07, 128.17),  # 인제
    "11D10202": (38.10, 127.99),  # 양구
    "11D10301": (37.88, 127.73),  # 춘천
    "11D10302": (37.70, 127.89),  # 홍천
    "11D10401": (37.34, 127.92),  # 원주
    "11D10402": (37.49, 128.00),  # 횡성
    "11D10501": (37.18, 128.46),  # 영월
    "11D10502": (37.38, 128.66),  # 정선
    "11D10503": (37.37, 128.39),  # 평창

    # ── 강원 영동 ────────────────────────────────────────
    "11D20201": (37.68, 128.72),  # 대관령
    "11D20301": (37.16, 128.99),  # 태백
    "11D20401": (38.21, 128.59),  # 속초
    "11D20402": (38.38, 128.47),  # 고성
    "11D20403": (38.08, 128.63),  # 양양
    "11D20501": (37.75, 128.88),  # 강릉
    "11D20601": (37.52, 129.11),  # 동해
    "11D20602": (37.45, 129.17),  # 삼척

    # ── 울릉도 / 독도 ─────────────────────────────────────
    "11E00101": (37.49, 130.86),  # 울릉도
    "11E00102": (37.24, 131.86),  # 독도

    # ── 전북자치도 ───────────────────────────────────────
    "11F10201": (35.82, 127.15),  # 전주
    "11F10202": (35.95, 126.96),  # 익산
    "11F10203": (35.57, 126.85),  # 정읍
    "11F10204": (35.90, 127.13),  # 완주
    "11F10301": (35.65, 127.52),  # 장수
    "11F10302": (36.01, 127.66),  # 무주
    "11F10303": (35.79, 127.43),  # 진안
    "11F10401": (35.41, 127.39),  # 남원
    "11F10402": (35.61, 127.29),  # 임실
    "11F10403": (35.37, 127.14),  # 순창

    # ── 전라남도 ─────────────────────────────────────────
    "11F20301": (34.31, 126.76),  # 완도
    "11F20302": (34.57, 126.60),  # 해남
    "11F20303": (34.64, 126.77),  # 강진
    "11F20304": (34.69, 126.91),  # 장흥
    "11F20401": (34.76, 127.66),  # 여수
    "11F20402": (34.94, 127.70),  # 광양
    "11F20403": (34.60, 127.28),  # 고흥
    "11F20404": (34.77, 127.07),  # 보성
    "11F20405": (34.95, 127.49),  # 순천시
    "11F20501": (35.15, 126.85),  # 광주 (전남)
    "11F20502": (35.30, 126.78),  # 장성
    "11F20503": (35.02, 126.71),  # 나주
    "11F20504": (35.32, 126.99),  # 담양
    "11F20505": (35.06, 126.99),  # 화순
    "11F20601": (35.20, 127.46),  # 구례
    "11F20602": (35.28, 127.29),  # 곡성
    "11F20603": (34.95, 127.49),  # 순천
    "11F20701": (34.69, 125.44),  # 흑산도

    # ── 제주도 ───────────────────────────────────────────
    "11G00101": (33.38, 126.88),  # 성산
    "11G00201": (33.51, 126.52),  # 제주
    "11G00302": (33.36, 126.53),  # 성판악
    "11G00401": (33.25, 126.56),  # 서귀포
    "11G00501": (33.29, 126.16),  # 고산
    "11G00601": (32.12, 125.18),  # 이어도  ← 원거리 탐색 제외 대상
    "11G00800": (33.96, 126.29),  # 추자도
    "11G00901": (33.43, 126.53),  # 산천단
    "11G01001": (33.36, 126.67),  # 한남

    # ── 경상북도 ─────────────────────────────────────────
    "11H10101": (37.04, 129.40),  # 울진
    "11H10102": (36.53, 129.37),  # 영덕
    "11H10201": (36.02, 129.34),  # 포항
    "11H10202": (35.84, 129.22),  # 경주
    "11H10301": (36.59, 128.19),  # 문경
    "11H10302": (36.41, 128.16),  # 상주
    "11H10303": (36.65, 128.45),  # 예천
    "11H10401": (36.87, 128.60),  # 영주
    "11H10402": (36.89, 128.73),  # 봉화
    "11H10403": (36.67, 129.11),  # 영양
    "11H10501": (36.57, 128.73),  # 안동
    "11H10502": (36.35, 128.70),  # 의성
    "11H10503": (36.44, 129.06),  # 청송
    "11H10601": (36.12, 128.11),  # 김천
    "11H10602": (36.12, 128.35),  # 구미
    "11H10604": (35.73, 128.26),  # 고령
    "11H10605": (35.92, 128.28),  # 성주
    "11H10701": (35.87, 128.60),  # 대구
    "11H10702": (35.97, 128.94),  # 영천
    "11H10703": (35.82, 128.74),  # 경산
    "11H10704": (35.65, 128.73),  # 청도
    "11H10705": (35.99, 128.40),  # 칠곡
    "11H10707": (36.24, 128.57),  # 군위

    # ── 경상남도 ─────────────────────────────────────────
    "11H20101": (35.54, 129.31),  # 울산
    "11H20102": (35.34, 129.03),  # 양산
    "11H20201": (35.10, 129.03),  # 부산
    "11H20301": (35.23, 128.68),  # 창원
    "11H20304": (35.23, 128.89),  # 김해
    "11H20401": (34.85, 128.43),  # 통영
    "11H20402": (35.00, 128.06),  # 사천
    "11H20403": (34.88, 128.62),  # 거제
    "11H20404": (34.97, 128.36),  # 고성
    "11H20405": (34.84, 127.89),  # 남해
    "11H20501": (35.52, 127.73),  # 함양
    "11H20502": (35.69, 127.91),  # 거창
    "11H20503": (35.57, 128.17),  # 합천
    "11H20601": (35.50, 128.74),  # 밀양
    "11H20602": (35.32, 128.26),  # 의령
    "11H20603": (35.27, 128.41),  # 함안
    "11H20604": (35.55, 128.49),  # 창녕
    "11H20701": (35.18, 128.11),  # 진주
    "11H20703": (35.41, 127.87),  # 산청
    "11H20704": (35.07, 127.75),  # 하동

    # ── 전북 서해안 (21F) ────────────────────────────────
    "21F10501": (35.97, 126.74),  # 군산
    "21F10502": (35.80, 126.89),  # 김제
    "21F10601": (35.44, 126.70),  # 고창
    "21F10602": (35.73, 126.73),  # 부안

    # ── 전남 서해안 (21F) ────────────────────────────────
    "21F20101": (35.07, 126.52),  # 함평
    "21F20102": (35.28, 126.51),  # 영광
    "21F20201": (34.49, 126.26),  # 진도
    "21F20801": (34.81, 126.39),  # 목포
    "21F20802": (34.80, 126.70),  # 영암
    "21F20803": (34.83, 126.10),  # 신안
    "21F20804": (34.99, 126.46),  # 무안
}

# 원거리 탐색 제외 항목 (이어도·독도는 특수 도서로 일반 nearest 탐색 제외)
_EXCLUDE_FROM_NEAREST: frozenset[str] = frozenset({"11G00601", "11E00102"})

# 사용자 제공 land code 매핑 테이블
# temp_id prefix → land_id  (긴 prefix를 앞에 배치하여 우선 매칭)
_LAND_CODE_MAP: list[tuple[str, str]] = [
    ("11A",  "11A00101"),  # 백령도
    ("11B",  "11B00000"),  # 서울/인천/경기
    ("11C1", "11C10000"),  # 충청북도
    ("11C2", "11C20000"),  # 충청남도
    ("11D1", "11D10000"),  # 강원 영서
    ("11D2", "11D20000"),  # 강원 영동
    ("11E",  "11E00101"),  # 울릉도/독도
    ("11F1", "11F10000"),  # 전북자치도
    ("11F2", "11F20000"),  # 전라남도
    ("11G",  "11G00000"),  # 제주도
    ("11H1", "11H10000"),  # 경상북도
    ("11H2", "11H20000"),  # 경상남도
    ("21F1", "21F10000"),  # 전북 서해안
    ("21F2", "21F20000"),  # 전남 서해안
]


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 좌표 간 haversine 거리(km)를 반환한다."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _land_code(temp_id: str) -> str:
    """
    temp_id prefix로 land_id를 도출한다.
    prefix는 긴 것부터 매칭하여 ambiguity를 방지한다.
    """
    for prefix, land in sorted(_LAND_CODE_MAP, key=lambda x: len(x[0]), reverse=True):
        if temp_id.startswith(prefix):
            return land
    _LOGGER.warning("알 수 없는 temp_id prefix: %s — land code 도출 실패", temp_id)
    return "11B00000"


def _get_kma_reg_ids(lat: float, lon: float) -> tuple:
    """
    위도·경도로 가장 가까운 중기예보 기온 구역(temp_id)을 찾아
    (reg_id_temp, reg_id_land) 쌍을 반환한다.

    알고리즘: _TEMP_ID_COORDS 테이블의 모든 지점에 대해 haversine 거리를 계산,
    최근접 지점의 temp_id를 선택한다.
    이어도(11G00601)·독도(11E00102)는 일반 탐색에서 제외한다.

    Returns:
        (reg_id_temp, reg_id_land) — 정상
        (None, None)               — 유효 좌표 없음 (호출부에서 캐시 사용)
    """
    best_id: str | None = None
    best_dist = float("inf")

    for tid, (tlat, tlon) in _TEMP_ID_COORDS.items():
        if tid in _EXCLUDE_FROM_NEAREST:
            continue
        d = _haversine(lat, lon, tlat, tlon)
        if d < best_dist:
            best_dist = d
            best_id = tid

    if best_id is None:
        return None, None

    _LOGGER.debug(
        "중기예보 구역 탐색: (%.4f, %.4f) → %s (%.1fkm)",
        lat, lon, best_id, best_dist,
    )
    return best_id, _land_code(best_id)


def _is_valid_korean_coord(lat: float, lon: float) -> bool:
    """
    한반도 및 부속 도서 유효 범위 내 좌표인지 검사한다.
    마라도(33.1°N) ~ 북한 최북단(42.5°N) / 이어도(125.1°E) ~ 독도(131.9°E)
    NaN·Inf 등 비정상 값도 False 처리.
    """
    if math.isnan(lat) or math.isnan(lon):
        return False
    if math.isinf(lat) or math.isinf(lon):
        return False
    # 이어도(32.1°N, 125.2°E)까지 포함하는 넉넉한 범위
    return 32.0 <= lat <= 42.5 and 124.0 <= lon <= 132.5


class KMAWeatherUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(hours=1))
        self.entry = entry
        self.api = KMAWeatherAPI(
            session=async_get_clientsession(hass),
            api_key=entry.data.get(CONF_API_KEY),
            reg_id_temp=None,   # 하드코딩 제거 — 첫 업데이트 시 좌표로 결정
            reg_id_land=None,
            hass=hass,
        )
        self._last_lat: float | None = None
        self._last_lon: float | None = None
        self._last_reg_temp: str | None = None
        self._last_reg_land: str | None = None
        self._cached_data: dict | None = None
        self._update_lock = asyncio.Lock()

    async def _async_update_data(self) -> dict:
        async with self._update_lock:
            try:
                curr_lat, curr_lon = self._resolve_location()

                # 유효 좌표를 전혀 얻지 못한 경우 → 캐시 반환
                if curr_lat is None or curr_lon is None:
                    _LOGGER.warning(
                        "유효한 위치 정보를 얻지 못했습니다. 캐시 데이터를 반환합니다."
                    )
                    return self._cached_data or {"weather": {}, "air": {}}

                reg_temp, reg_land = _get_kma_reg_ids(curr_lat, curr_lon)

                # 구역 코드 도출 실패 → 캐시 구역 코드로 대체
                if reg_temp is None:
                    _LOGGER.warning(
                        "좌표 (%.4f, %.4f)의 중기예보 구역을 도출하지 못했습니다. "
                        "마지막 캐시 구역 코드를 사용합니다.",
                        curr_lat, curr_lon,
                    )
                    if self._last_reg_temp:
                        reg_temp = self._last_reg_temp
                        reg_land = self._last_reg_land
                    else:
                        _LOGGER.error("캐시 구역 코드도 없습니다. 빈 데이터를 반환합니다.")
                        return self._cached_data or {"weather": {}, "air": {}}

                # 유효 상태 캐시 갱신
                self._last_lat, self._last_lon = curr_lat, curr_lon
                self._last_reg_temp, self._last_reg_land = reg_temp, reg_land
                self.api.reg_id_temp = reg_temp
                self.api.reg_id_land = reg_land

                nx, ny = convert_grid(curr_lat, curr_lon)
                new_data = await self.api.fetch_data(curr_lat, curr_lon, nx, ny)

                if new_data is None:
                    return self._cached_data or {"weather": {}, "air": {}}

                weather = new_data.setdefault("weather", {})
                weather.update({
                    "last_updated":       datetime.now(timezone.utc),
                    "debug_nx":           nx,
                    "debug_ny":           ny,
                    "debug_lat":          round(curr_lat, 5),
                    "debug_lon":          round(curr_lon, 5),
                    "debug_reg_id_temp":  reg_temp,
                    "debug_reg_id_land":  reg_land,
                })
                self._cached_data = new_data
                return new_data

            except Exception as exc:
                _LOGGER.warning("업데이트 중 오류 발생: %s", exc)
                return self._cached_data or {"weather": {}, "air": {}}

    def _resolve_location(self) -> tuple:
        """
        위치 엔티티 → 마지막 캐시 좌표 → HA 시스템 기본 좌표 순으로
        유효한 좌표를 반환한다.

        유효 좌표를 얻지 못하면 (None, None) 반환.
        """
        entity_id = self.entry.data.get(CONF_LOCATION_ENTITY, "")
        state = self.hass.states.get(entity_id) if entity_id else None

        # 1순위: 위치 엔티티
        if state:
            lat_attr = state.attributes.get("latitude")
            lon_attr = state.attributes.get("longitude")

            if lat_attr is not None and lon_attr is not None:
                try:
                    lat = float(lat_attr)
                    lon = float(lon_attr)
                    if _is_valid_korean_coord(lat, lon):
                        return lat, lon
                    _LOGGER.warning(
                        "엔티티 '%s' 좌표 (%.4f, %.4f)가 유효 범위 밖입니다. "
                        "마지막 캐시 좌표를 사용합니다.",
                        entity_id, lat, lon,
                    )
                except (TypeError, ValueError) as exc:
                    _LOGGER.warning(
                        "엔티티 '%s' 좌표 변환 실패 (%s). 마지막 캐시 좌표를 사용합니다.",
                        entity_id, exc,
                    )

        # 2순위: 마지막 캐시 좌표
        if self._last_lat is not None and self._last_lon is not None:
            return self._last_lat, self._last_lon

        # 3순위: HA 시스템 기본 좌표 (최후 수단)
        try:
            lat = float(self.hass.config.latitude)
            lon = float(self.hass.config.longitude)
            if _is_valid_korean_coord(lat, lon):
                _LOGGER.info(
                    "위치 엔티티 미설정 — HA 기본 좌표 (%.4f, %.4f) 사용", lat, lon
                )
                return lat, lon
        except (TypeError, ValueError):
            pass

        return None, None
