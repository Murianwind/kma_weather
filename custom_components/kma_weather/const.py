"""Constants for the KMA Weather integration."""
from typing import Final

DOMAIN: Final = "kma_weather"

CONF_KMA_API_KEY: Final = "kma_api_key"
CONF_AIR_API_KEY: Final = "air_api_key"
CONF_LOCATION_TYPE: Final = "location_type"
CONF_ZONE_ID: Final = "zone_id"
CONF_MOBILE_DEVICE_ID: Final = "mobile_device_id"

LOCATION_TYPE_ZONE: Final = "zone"
LOCATION_TYPE_MOBILE: Final = "mobile"

# 업데이트 주기 관련 (02:15부터 3시간 간격)
UPDATE_START_HOUR: Final = 2
UPDATE_START_MIN: Final = 15
UPDATE_INTERVAL_HOURS: Final = 3

# 기상청 LCC 격자 변환 상수
RE = 6371.00877  # 지구 반경(km)
GRID = 5.0       # 격자 간격(km)
SLAT1 = 30.0     # 투영 위도1(degree)
SLAT2 = 60.0     # 투영 위도2(degree)
OLON = 126.0     # 기준점 경도(degree)
OLAT = 38.0      # 기준점 위도(degree)
XO = 43          # 기준점 X좌표(GRID)
YO = 136         # 기준점 Y좌표(GRID)

def convert_grid(lat, lon):
    """WGS84 위경도를 기상청 격자 좌표로 변환"""
    import math
    
    PI = math.pi
    DEGRAD = PI / 180.0
    
    re = RE / GRID
    slat1 = SLAT1 * DEGRAD
    slat2 = SLAT2 * DEGRAD
    olon = OLON * DEGRAD
    olat = OLAT * DEGRAD
    
    sn = math.tan(PI * 0.25 + slat2 * 0.5) / math.tan(PI * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(PI * 0.25 + slat1 * 0.5)
    sf = math.pow(sf, sn) * math.cos(slat1) / sn
    ro = math.tan(PI * 0.25 + olat * 0.5)
    ro = re * sf / math.pow(ro, sn)
    
    ra = math.tan(PI * 0.25 + lat * DEGRAD * 0.5)
    ra = re * sf / math.pow(ra, sn)
    theta = lon * DEGRAD - olon
    if theta > PI: theta -= 2.0 * PI
    if theta < -PI: theta += 2.0 * PI
    theta *= sn
    
    nx = math.floor(ra * math.sin(theta) + XO + 0.5)
    ny = math.floor(ro - ra * math.cos(theta) + YO + 0.5)
    
    return nx, ny
