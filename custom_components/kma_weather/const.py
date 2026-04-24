"""Constants for the KMA Weather integration."""
import math

DOMAIN = "kma_weather"

CONF_API_KEY = "api_key"
CONF_LOCATION_ENTITY = "location_entity"
CONF_PREFIX = "prefix"
CONF_APPLY_DATE = "apply_date"
CONF_EXPIRE_DATE = "expire_date"

def convert_grid(lat, lon):
    """WGS84 좌표를 기상청 격자 좌표로 변환 (원본 로직 완벽 복구)."""
    RE = 6371.00877  # 지구 반경(km)
    GRID = 5.0       # 격자 간격(km)
    SLAT1 = 30.0     # 투영 위도1(degree)
    SLAT2 = 60.0     # 투영 위도2(degree)
    OLON = 126.0     # 기준점 경도(degree)
    OLAT = 38.0      # 기준점 위도(degree)
    XO = 43          # 기준점 X좌표(GRID)
    YO = 136         # 기준점 Y좌표(GRID)

    DEGRAD = math.pi / 180.0
    re = RE / GRID
    slat1 = SLAT1 * DEGRAD
    slat2 = SLAT2 * DEGRAD
    olon = OLON * DEGRAD
    olat = OLAT * DEGRAD

    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = math.pow(sf, sn) * math.cos(slat1) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re * sf / math.pow(ro, sn)

    ra = math.tan(math.pi * 0.25 + (lat) * DEGRAD * 0.5)
    ra = re * sf / math.pow(ra, sn)
    theta = lon * DEGRAD - olon
    if theta > math.pi: theta -= 2.0 * math.pi
    if theta < -math.pi: theta += 2.0 * math.pi
    theta *= sn

    x = math.floor(ra * math.sin(theta) + XO + 0.5)
    y = math.floor(ro - ra * math.cos(theta) + YO + 0.5)
    return int(x), int(y)


# ── 공통 지오 유틸리티 ────────────────────────────────────────────────────────

def safe_float(v) -> float | None:
    """
    값을 float으로 안전하게 변환한다.
    None, 빈 문자열, "-" 는 None을 반환한다.
    변환 실패 시 None을 반환한다.
    """
    try:
        if v is None or v == "" or v == "-":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 위경도 좌표 간 거리를 킬로미터(km) 단위로 반환한다 (Haversine 공식)."""
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return r * 2 * math.asin(math.sqrt(a))


# 한국 영역 경계 상수 (독도·이어도 포함)
# ── 서비스 액션 입력 주소 검증용 (엄격한 범위) ────────────────────────────
KOR_LAT_STRICT = (33.0, 38.7)
KOR_LON_STRICT = (124.0, 132.0)

# ── 기기 위치 좌표 유효성 검사용 (넓은 범위: 제주 남단·독도 포함) ──────────
KOR_LAT_LOOSE  = (32.0, 42.5)
KOR_LON_LOOSE  = (124.0, 132.5)


def is_korean_coord_strict(lat: float, lon: float) -> bool:
    """서비스 입력 주소 검증용 — 국내 행정구역 범위 내 좌표인지 검사한다."""
    return (KOR_LAT_STRICT[0] <= lat <= KOR_LAT_STRICT[1]
            and KOR_LON_STRICT[0] <= lon <= KOR_LON_STRICT[1])


def is_korean_coord_loose(lat: float, lon: float) -> bool:
    """기기 위치 유효성 검사용 — 한반도 인근 넓은 범위 내 좌표인지 검사한다."""
    if math.isnan(lat) or math.isnan(lon):
        return False
    return (KOR_LAT_LOOSE[0] <= lat <= KOR_LAT_LOOSE[1]
            and KOR_LON_LOOSE[0] <= lon <= KOR_LON_LOOSE[1])
