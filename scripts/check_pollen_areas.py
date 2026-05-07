#!/usr/bin/env python3
"""
꽃가루 API rc=99 지역 전수 조사 스크립트
pollen_area_map.json의 모든 지역에 대해 소나무 꽃가루 API를 호출하여
rc=99인 지역을 pollen_rc99_areas.json으로 저장합니다.

사용법:
  python check_pollen_areas.py --api-key YOUR_API_KEY [--delay 1.0] [--start 0]

옵션:
  --api-key   공공데이터포털 API 키 (필수)
  --delay     API 호출 간격(초), 기본 1.0
  --start     이어서 시작할 인덱스 (중단 후 재시작 시 사용)
"""

import argparse
import json
import time
import urllib.request
import urllib.parse
import os
from datetime import datetime

PINE_URL = "https://apis.data.go.kr/1360000/HealthWthrIdxServiceV3/getPinePollenRiskIdxV3"
MAP_FILE = "pollen_area_map.json"
OUT_FILE = "pollen_rc99_areas.json"
PROGRESS_FILE = "pollen_check_progress.json"


def fetch_result_code(api_key: str, area_no: str) -> str:
    """소나무 꽃가루 API 호출 후 resultCode 반환."""
    params = urllib.parse.urlencode({
        "serviceKey": api_key,
        "returnType": "json",
        "numOfRows": "1",
        "pageNo": "1",
        "areaNo": area_no,
    })
    url = f"{PINE_URL}?{params}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        return data["response"]["header"]["resultCode"]
    except Exception as e:
        return f"ERR:{e}"


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"checked": 0, "rc99": [], "errors": []}


def save_progress(progress):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--start", type=int, default=0)
    args = parser.parse_args()

    with open(MAP_FILE, encoding="utf-8") as f:
        areas = json.load(f)

    total = len(areas)
    progress = load_progress()

    # --start 옵션이 0보다 크면 progress 초기화
    if args.start > 0:
        progress["checked"] = args.start

    start_idx = progress["checked"]
    rc99 = progress["rc99"]
    errors = progress["errors"]

    print(f"총 {total}개 지역, {start_idx}번부터 시작")
    print(f"예상 소요 시간: {(total - start_idx) * args.delay / 60:.0f}분")
    print(f"시작: {datetime.now().strftime('%H:%M:%S')}\n")

    for i, area in enumerate(areas[start_idx:], start=start_idx):
        code = area["c"]
        name = area["n"]
        rc = fetch_result_code(args.api_key, code)

        if rc == "99":
            rc99.append({"c": code, "n": name, "la": area["la"], "lo": area["lo"]})
            marker = "✗"
        elif rc == "00":
            marker = "✓"
        else:
            errors.append({"c": code, "n": name, "rc": rc})
            marker = f"?"

        progress["checked"] = i + 1
        progress["rc99"] = rc99
        progress["errors"] = errors

        if (i + 1) % 10 == 0 or rc != "00":
            elapsed = (i + 1 - start_idx)
            remaining = total - (i + 1)
            print(f"[{i+1:4d}/{total}] {marker} {code} {name[:20]:<20} "
                  f"rc={rc}  rc99={len(rc99)}개  남은시간≈{remaining * args.delay / 60:.0f}분")
            save_progress(progress)

        time.sleep(args.delay)

    # 최종 저장
    save_progress(progress)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(rc99, f, ensure_ascii=False, indent=2)

    print(f"\n완료: {datetime.now().strftime('%H:%M:%S')}")
    print(f"전체 {total}개 중 rc=99: {len(rc99)}개 ({len(rc99)/total*100:.1f}%)")
    print(f"오류: {len(errors)}개")
    print(f"결과 저장: {OUT_FILE}")


if __name__ == "__main__":
    main()
