#!/usr/bin/env python3
"""
YAM 대만 화장실 데이터 파이프라인 - Phase 0
OSM Overpass API에서 대만 전역 공중화장실(amenity=toilets) 데이터를 1회 쿼리로 수집한다.
(일본판과 달리 대만은 도시별 분할 없이 전국 단일 쿼리 — 약 5,700건 규모 예상)

표준 라이브러리만 사용 (urllib.request, json, time). pip 설치 불필요.

사용법:
    python3 fetch_osm.py

동작:
    - area["ISO3166-1"="TW"] 기반 전국 쿼리를 POST로 전송
    - way는 out center로 받아 center 좌표를 노드처럼 사용
    - 이미 pipeline/raw/taiwan.json이 있으면 스킵 (재실행 안전)
    - 실패 시 30초 대기 후 1회 재시도, 그래도 실패하면 bbox 폴백 쿼리 시도
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# 대만 전역 bbox 폴백 (남,서,북,동) — 펑후·진먼 포함
BBOX_FALLBACK = (21.8, 118.0, 25.4, 122.1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(SCRIPT_DIR, "raw")
OUT_PATH = os.path.join(RAW_DIR, "taiwan.json")

AREA_QUERY = """[out:json][timeout:300];
area["ISO3166-1"="TW"][admin_level=2]->.tw;
(
  node["amenity"="toilets"](area.tw);
  way["amenity"="toilets"](area.tw);
);
out center tags;
"""


def build_bbox_query(bbox):
    south, west, north, east = bbox
    return f"""[out:json][timeout:300];
(
  node["amenity"="toilets"]({south},{west},{north},{east});
  way["amenity"="toilets"]({south},{west},{north},{east});
);
out center tags;
"""


def run_query(query):
    data = query.encode("utf-8")
    req = urllib.request.Request(
        OVERPASS_URL,
        data=data,
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "User-Agent": "YAM-toilet-app-data-pipeline/1.0 (contact: internal use)",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=330) as resp:
        body = resp.read()
        return json.loads(body.decode("utf-8"))


def main():
    os.makedirs(RAW_DIR, exist_ok=True)

    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f)
            n = len(existing.get("elements", []))
            print(f"[SKIP] taiwan: 기존 파일 존재 ({n}건) - {OUT_PATH}")
            return
        except Exception as e:
            print(f"[WARN] 기존 파일 파싱 실패({e}), 재수집 시도")

    print("[FETCH] 대만 전역(area ISO3166-1=TW) 쿼리 시작...")
    try:
        result = run_query(AREA_QUERY)
        elements = result.get("elements", [])
        if not elements:
            raise ValueError("area 쿼리 결과 0건 — bbox 폴백으로 전환")
        with open(OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        print(f"[OK] taiwan (area query): {len(elements)}건 저장 -> {OUT_PATH}")
        return
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, ValueError) as e:
        print(f"[ERROR] area 쿼리 1차 시도 실패 ({e}). 30초 대기 후 재시도...")
        time.sleep(30)
        try:
            result = run_query(AREA_QUERY)
            elements = result.get("elements", [])
            if not elements:
                raise ValueError("area 쿼리 재시도도 0건")
            with open(OUT_PATH, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False)
            print(f"[OK] taiwan (area query, 재시도 성공): {len(elements)}건 저장 -> {OUT_PATH}")
            return
        except Exception as e2:
            print(f"[WARN] area 쿼리 재시도도 실패 ({e2}). bbox 폴백 쿼리 시도...")
            try:
                result = run_query(build_bbox_query(BBOX_FALLBACK))
                elements = result.get("elements", [])
                with open(OUT_PATH, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False)
                print(f"[OK] taiwan (bbox 폴백): {len(elements)}건 저장 -> {OUT_PATH}")
                return
            except Exception as e3:
                print(f"[FAIL] bbox 폴백도 실패 ({e3}). 수집 실패.", file=sys.stderr)
                sys.exit(1)
    except Exception as e:
        print(f"[FAIL] 예기치 않은 오류 ({e}).", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
