# 🇰🇷 기상청 스마트 날씨 (KMA Weather Smart)

기상청(KMA) 및 에어코리아(Air Korea)의 공공 데이터를 활용하여 Home Assistant에서 가장 정확한 대한민국 로컬 날씨 정보를 제공합니다. 특히 **이동형 기기(Mobile Device)**의 실시간 위치를 추적하여 해당 지역의 동/읍/면 단위 주소와 날씨를 즉시 갱신하는 스마트한 기능을 포함하고 있습니다.

## ✨ 주요 기능

* **정밀한 로컬 데이터**: 기상청 단기/중기 예보 및 에어코리아 미세먼지 데이터 통합.
* **실시간 위치 추적 (Reverse Geocoding)**: `device_tracker`와 연동하여 이동 시 실시간으로 주소(예: 서울 자양동, 문경 농암면)를 센서에 표기.
* **스마트 예보**: 오늘/내일 최고/최저 기온, 강수 확률, 비 시작 시간(비안옴/시간표기) 제공.
* **직관적인 엔티티 ID**: 설정 시 입력한 `Prefix`를 기반으로 영문 직관적 ID 강제 생성 (예: `sensor.home_today_high_temperature`).
* **수동 업데이트 버튼**: 이동 기기 등록 시 즉시 데이터를 갱신할 수 있는 리프레시 버튼 제공.
* **고해상도 날씨 요약**: 10일간의 일별 예보 및 하루 2회(오전/오후) 상세 예보 카드 지원.

## 🚀 설치 방법

### 방법 1: HACS (권장)
1. **HACS > Integrations > 우측 상단 메뉴 > Custom repositories** 선택.
2. 본 저장소 URL(`https://github.com/murianwind/kma_weather`)을 입력하고 Category를 **Integration**으로 선택하여 추가합니다.
3. 목록에서 **기상청 스마트 날씨 (KMA Weather Smart)**를 찾아 설치합니다.
4. Home Assistant를 **재시작**합니다.

### 방법 2: 수동 설치
1. 본 저장소의 `custom_components/kma_weather` 폴더를 다운로드합니다.
2. Home Assistant 설정 폴더(config) 내의 `custom_components` 폴더에 붙여넣습니다.
3. Home Assistant를 **재시작**합니다.

## ⚙️ 설정 가이드

### 1. API 키 신청 (필수)
아래 3개 항목을 [공공데이터포털](https://www.data.go.kr/)에서 신청하고 **일반 인증키(Encoding)**를 준비하세요.
1. [기상청 단기예보](https://www.data.go.kr/data/15084084/openapi.do)
2. [기상청 중기예보](https://www.data.go.kr/data/15059468/openapi.do)
3. [에어코리아 대기오염](https://www.data.go.kr/data/15073861/openapi.do)

### 2. 통합구성요소 추가
1. **설정 > 기기 및 서비스 > 통합구성요소 추가**에서 `기상청 스마트 날씨`를 검색합니다.
2. **인증키**: 복사한 Encoding 키를 입력합니다.
3. **위치 선택**: 고정된 `Zone` 또는 이동 중인 `device_tracker`를 선택합니다.
4. **Prefix**: 센서 ID 앞에 붙을 영문(예: `home`, `car`)을 입력합니다. (비워둘 시 기본값 사용)

## 📊 제공 엔티티 (Prefix: home 기준 예시)

| 엔티티 ID | 이름 | 비고 |
| :--- | :--- | :--- |
| `weather.home_weather_summary` | 날씨 요약 | 10일치 예보 포함 |
| `sensor.home_current_location` | 현재 위치 | 행정구역 동/읍/면 표기 |
| `sensor.home_today_high_temperature` | 최고 온도 | 정수 표기 (15 °C) |
| `sensor.home_rain_start_time` | 비 시작 시간 | "비안옴" 또는 시작 시각 |
| `button.home_manual_update` | 수동 업데이트 | 이동 기기 전용 버튼 |

## 🛠️ 기기 정보
* **제조사**: Murianwind
* **모델**: integration
* **버전**: v1.0.0

---
**Attribution**: 본 서비스는 기상청 및 에어코리아의 공공데이터를 활용합니다.
