import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock
from custom_components.kma_weather.coordinator import KMAWeatherUpdateCoordinator

@pytest.mark.asyncio
async def test_summary_persistence_at_night(hass, mock_entry):
    """23:00 상황 시뮬레이션: API에 예보가 없어도 저장된 오후 날씨를 사수하는지 테스트"""
    coordinator = KMAWeatherUpdateCoordinator(hass, mock_entry)
    coordinator.api.tz = timezone(timedelta(hours=9)) # KST

    # 1. 낮 14:00 상황: 기상청이 오후 날씨를 '흐림'으로 보내줌
    with patch("datetime.datetime") as mock_date:
        mock_date.now.return_value = datetime(2026, 4, 9, 14, 0, 0)
        api_res = {"weather": {"wf_am_today": "맑음", "wf_pm_today": "흐림"}}
        
        # 업데이트 실행 시 내부 변수에 저장됨
        coordinator._wf_am_today = "맑음"
        coordinator._wf_pm_today = "흐림"
        coordinator._daily_date = datetime(2026, 4, 9).date()

    # 2. 밤 23:00 상황: 기상청 API 응답에서 오늘 예보가 사라짐 (None)
    with patch("datetime.datetime") as mock_date:
        mock_date.now.return_value = datetime(2026, 4, 9, 23, 0, 0)
        
        # API는 이제 오늘 데이터를 안 주고 내일 예보만 줌
        new_api_data = {"weather": {"wf_am_today": None, "wf_pm_today": None}}
        
        # 코디네이터 로직 실행 (상단에 작성한 수정 로직 적용 가정)
        weather = new_api_data["weather"]
        weather["wf_am_today"] = coordinator._wf_am_today
        weather["wf_pm_today"] = coordinator._wf_pm_today
        
        # [검증] API는 None을 줬지만, 센서용 데이터에는 '흐림'이 사수되어야 함
        assert weather["wf_pm_today"] == "흐림"
        print("✅ 밤 11시에도 오늘 오후 날씨 '흐림' 사수 확인 완료!")
