# 기상청 스마트 날씨 (KMA Weather Smart)

[![Pytest](https://github.com/Murianwind/kma_weather/actions/workflows/pytest.yml/badge.svg)](https://github.com/Murianwind/kma_weather/actions/workflows/pytest.yml)
[![HACS Validate](https://github.com/Murianwind/kma_weather/actions/workflows/hacs.yml/badge.svg)](https://github.com/Murianwind/kma_weather/actions/workflows/hacs.yml)
[![codecov](https://codecov.io/gh/Murianwind/kma_weather/branch/main/graph/badge.svg)](https://codecov.io/gh/Murianwind/kma_weather)

기상청(KMA) 및 에어코리아(Air Korea)의 공공 데이터를 활용하여 대한민국 로컬 날씨 정보를 제공합니다. 특히 **이동형 기기(Mobile Device)**의 실시간 위치를 추적하여 해당 지역의 읍/면/동 단위 주소와 날씨를 즉시 갱신하는 기능을 포함하고 있습니다.

## ✨ 주요 기능

- **정밀한 로컬 데이터**: 기상청 단기/중기 예보 및 에어코리아 미세먼지 데이터 통합
- **실시간 위치 추적**: `device_tracker`와 연동하여 이동 시 실시간으로 읍/면/동 단위 주소와 날씨를 갱신
- **스마트 예보**: 오늘/내일 최고/최저 기온, 강수 확률, 비 시작 시간, 10일간 일별/하루 2회(오전·오후) 상세 예보
- **꽃가루 농도 위험지수**: 전국 3,560개 읍면동 단위 정밀 조회 (참나무·소나무·잡초류)
- **천문 정보**: 일출·일몰·박명·월출·월몰·달 위상·달 조명율·천문 관측 조건 센서 제공
- **기상특보**: 현재 발효 중인 기상특보(주의보·경보) 표시
- **직관적인 엔티티 ID**: 설정 시 입력한 `Prefix`로 영문 직관적 ID 강제 생성 (예: `sensor.home_temperature`)
- **동적 센서 등록**: API 활용신청 후 HA 재로드 없이 다음 업데이트 시 자동으로 센서가 추가됨
- **수동 업데이트 버튼**: 즉시 데이터를 갱신할 수 있는 리프레시 버튼 제공

## 🚀 설치 방법

### 방법 1: HACS (권장)
1. **HACS > Integrations > 우측 상단 메뉴 > Custom repositories** 선택
2. 저장소 URL(`https://github.com/murianwind/kma_weather`)을 입력하고 Category를 **Integration**으로 선택하여 추가
3. 목록에서 **기상청 스마트 날씨 (KMA Weather Smart)**를 찾아 설치
4. Home Assistant **재시작**

### 방법 2: 수동 설치
1. 저장소의 `custom_components/kma_weather` 폴더 전체를 다운로드
2. HA 설정 폴더(config)의 `custom_components` 폴더에 붙여넣기
3. Home Assistant **재시작**

## ⚙️ 설정 가이드

### 1. API 키 신청

[공공데이터포털](https://www.data.go.kr/)에서 아래 6개 서비스의 활용신청을 하고 **일반 인증키(Encoding)**를 준비하세요. 모든 키는 하나의 인증키를 공유합니다.

| # | 서비스명 | 링크 | 생성되는 센서 |
|---|---|---|---|
| 1 | 단기예보 조회서비스 | [신청](https://www.data.go.kr/data/15084084/openapi.do) | 온도, 습도, 풍속, 풍향, 강수확률 등 16개 |
| 2 | 중기예보 조회서비스 | [신청](https://www.data.go.kr/data/15059468/openapi.do) | 날씨 엔티티 10일 예보 데이터에 통합 |
| 3 | 에어코리아 대기오염정보 | [신청](https://www.data.go.kr/data/15073861/openapi.do) | PM10·PM2.5 농도 및 등급 4개 |
| 4 | 에어코리아 측정소정보 | [신청](https://www.data.go.kr/data/15073877/openapi.do) | 내부 처리용 (별도 센서 없음) |
| 5 | 기상특보 조회서비스 | [신청](https://www.data.go.kr/data/15000415/openapi.do) | 기상특보 1개 |
| 6 | 꽃가루농도위험지수 조회서비스 | [신청](https://www.data.go.kr/data/15085289/openapi.do) | 꽃가루 농도 1개 |

> **중요**: API가 활용신청되지 않은 경우 해당 센서는 생성되지 않습니다. HA 알림(Persistent Notification)으로 미신청 API를 안내합니다. 신청 후 승인이 완료되면 HA 재로드 없이 다음 자동 업데이트(최대 1시간) 시 센서가 자동으로 추가됩니다.

### 2. 통합구성요소 추가

1. **설정 > 기기 및 서비스 > 통합구성요소 추가**에서 `기상청 스마트 날씨`를 검색
2. **인증키**: 공공데이터포털의 Encoding 키 입력
3. **위치 선택**: 고정 위치 `Zone` 또는 이동 추적 `device_tracker` 선택
4. **Prefix**: 센서 ID 앞에 붙을 영문 식별자 입력 (예: `home`, `car`, `murian`)
5. **API 만료일**: 공공데이터포털의 활용기간 종료일 입력 (잔여일수 센서에 표시)

## 📡 생성되는 센서

### 항상 생성 (API 신청 불필요)

| 센서 | 설명 |
|---|---|
| `sensor.PREFIX_location` | 현재 위치 (읍면동 주소). 속성: 격자좌표, 중기예보 구역코드, 에어코리아 측정소, 좌표, 꽃가루 조회 지역 |
| `sensor.PREFIX_last_updated` | 마지막 업데이트 시각 |
| `sensor.PREFIX_api_expire` | API 잔여일수 |
| `sensor.PREFIX_sunrise` | 다음 일출 시각 |
| `sensor.PREFIX_sunset` | 다음 일몰 시각 |
| `sensor.PREFIX_dawn` | 다음 새벽(시민박명 시작) |
| `sensor.PREFIX_dusk` | 다음 황혼(시민박명 종료) |
| `sensor.PREFIX_astro_dawn` | 다음 천문박명 종료 (천문 관측 시작 가능 시각) |
| `sensor.PREFIX_astro_dusk` | 다음 천문박명 시작 (천문 관측 종료 시각) |
| `sensor.PREFIX_moon_phase` | 달 위상 (삭/초승달/상현달 등) |
| `sensor.PREFIX_moon_illumination` | 달 조명율 (%) |
| `sensor.PREFIX_moonrise` | 다음 월출 시각 |
| `sensor.PREFIX_moonset` | 다음 월몰 시각 |
| `sensor.PREFIX_observation_condition` | 천문 관측 조건 (최우수/우수/보통/불량/관측불가) |

### 단기예보 API 승인 시

| 센서 | 설명 |
|---|---|
| `sensor.PREFIX_temperature` | 현재 기온 (°C) |
| `sensor.PREFIX_humidity` | 현재 습도 (%) |
| `sensor.PREFIX_wind_speed` | 현재 풍속 (m/s) |
| `sensor.PREFIX_wind_direction` | 현재 풍향 (북/북동/동 등) |
| `sensor.PREFIX_precipitation_prob` | 강수 확률 (%) |
| `sensor.PREFIX_apparent_temperature` | 체감 온도 (°C) |
| `sensor.PREFIX_rain_start` | 비 시작 예상 시각 |
| `sensor.PREFIX_condition` | 현재 날씨 상태 |
| `sensor.PREFIX_today_temp_max` | 오늘 최고 기온 (°C) |
| `sensor.PREFIX_today_temp_min` | 오늘 최저 기온 (°C) |
| `sensor.PREFIX_today_condition_am` | 오늘 오전 날씨 |
| `sensor.PREFIX_today_condition_pm` | 오늘 오후 날씨 |
| `sensor.PREFIX_tomorrow_temp_max` | 내일 최고 기온 (°C) |
| `sensor.PREFIX_tomorrow_temp_min` | 내일 최저 기온 (°C) |
| `sensor.PREFIX_tomorrow_condition_am` | 내일 오전 날씨 |
| `sensor.PREFIX_tomorrow_condition_pm` | 내일 오후 날씨 |

### 에어코리아 대기오염정보 API 승인 시

| 센서 | 설명 |
|---|---|
| `sensor.PREFIX_pm10` | 미세먼지 농도 (µg/m³) |
| `sensor.PREFIX_pm10_grade` | 미세먼지 등급 (좋음/보통/나쁨/매우나쁨) |
| `sensor.PREFIX_pm25` | 초미세먼지 농도 (µg/m³) |
| `sensor.PREFIX_pm25_grade` | 초미세먼지 등급 (좋음/보통/나쁨/매우나쁨) |

### 기상특보 API 승인 시

| 센서 | 설명 |
|---|---|
| `sensor.PREFIX_warning` | 현재 발효 중인 기상특보 (예: `호우주의보`, `특보없음`) |

### 꽃가루농도위험지수 API 승인 시

| 센서 | 설명 |
|---|---|
| `sensor.PREFIX_pollen` | 꽃가루 농도 종합 등급 (좋음/보통/나쁨/매우나쁨). 속성: 참나무·소나무·풀 개별 등급 |

> **꽃가루 센서 참고**: 비제공 시즌(참나무·소나무: 7-2월, 잡초류: 11-3월)에는 API 호출 없이 `좋음`을 반환합니다. 조회는 전국 3,560개 읍면동 단위로 이루어지며, 현재 위치에서 가장 가까운 읍면동의 데이터를 표시합니다.

## 🔭 천문 정보 서비스 (HA 액션)

**서비스 ID**: `kma_weather.get_astronomical_info`

원하는 위치와 날짜에 대한 천문 정보를 조회합니다. HA 자동화나 스크립트에서 호출할 수 있습니다.

### 입력 파라미터

| 파라미터 | 필수 | 설명 |
|---|---|---|
| `address` | 필수 | 한국 읍면동 주소 (예: `경기도 화성시 동탄면`) |
| `date` | 필수 | 조회 날짜 (오늘~오늘+4일 이내) |
| `time` | 선택 | 조회 시각, HH:MM 형식 (기본값: 현재 시각) |

### 반환값

| 필드 | 설명 |
|---|---|
| `address` | 입력한 주소 |
| `resolved_address` | Nominatim이 변환한 정규화 주소 |
| `date` | 조회 날짜 |
| `time` | 조회 시각 (HH:MM) |
| `latitude` / `longitude` | 변환된 좌표 |
| `sunrise` / `sunset` | 일출·일몰 시각 |
| `dawn` / `dusk` | 시민박명 시작·종료 시각 |
| `astro_dawn` / `astro_dusk` | 천문박명 종료·시작 시각 |
| `moonrise` / `moonset` | 월출·월몰 시각 |
| `moon_phase` | 달 위상 |
| `moon_illumination` | 달 조명율 (%) |
| `observation_condition` | 지정 시각 기준 천문 관측 조건 |
| `observation_reason` | 관측 불가 사유 (해당 시에만) |

### 오류 안내

잘못된 입력에 대해 아래 상황별로 구체적인 메시지를 반환합니다.

- 주소 공백 / 주소를 찾을 수 없음
- 과거 날짜 / 4일 이후 날짜 (조회 가능 범위 안내)
- 시각 형식 오류 (올바른 형식 예시 안내)
- 한국 영역 밖 좌표로 변환된 경우
- skyfield 라이브러리 미준비 / 천문 계산 실패

### API 만료 및 재신청

- API가 만료되거나 중지 신청하면, 다음 업데이트 시 관련 센서가 `unavailable` 상태로 전환되고 HA 알림으로 안내합니다.
- 사용자가 센서를 직접 삭제해도 재신청 후 다음 업데이트 시 자동으로 재생성됩니다.
- 새로 신청한 API의 센서는 HA 재로드 없이 승인 확인 즉시(다음 자동 업데이트) 자동 추가됩니다.

---

**Attribution**: 본 서비스는 기상청 및 에어코리아의 공공데이터를 활용합니다.
