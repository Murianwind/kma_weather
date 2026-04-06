async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    # 이미지 목록과 동일한 16개 핵심 센서
    sensor_map = [
        ("현재날씨", "current_condition"),
        ("현재위치 날씨", "location_weather"),
        ("현재풍속", "WSD"),
        ("현재풍향", "VEC_KOR"),
        ("최고온도", "TMX_today"),
        ("최저온도", "TMN_today"),
        ("강수확률", "POP"),
        ("비시작시간오늘내일", "rain_start_time"),
        ("내일최고온도", "TMX_tomorrow"),
        ("내일최저온도", "TMN_tomorrow"),
        ("내일오전날씨", "weather_am_tomorrow"),
        ("내일오후날씨", "weather_pm_tomorrow"),
        ("미세먼지", "pm10Value"),
        ("미세먼지등급", "pm10GradeKOR"),
        ("초미세먼지", "pm25Value"),
        ("초미세먼지등급", "pm25GradeKOR"),
    ]
    
    entities = [KMACustomSensor(coordinator, entry, name, key) for name, key in sensor_map]
    async_add_entities(entities)

class KMACustomSensor(CoordinatorEntity, SensorEntity):
    # (이전 코드와 동일한 구조, data['weather'] 또는 data['air']에서 값을 가져옴)
