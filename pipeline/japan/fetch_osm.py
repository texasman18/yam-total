#!/usr/bin/env python3
"""
YAM 일본 화장실 데이터 파이프라인 - Phase 0
OSM Overpass API에서 도시별 공중화장실(amenity=toilets) 데이터를 수집한다.

표준 라이브러리만 사용 (urllib.request, json, time). pip 설치 불필요.

사용법:
    python3 fetch_osm.py

동작:
    - 도시별로 Overpass QL 쿼리를 POST로 전송
    - way는 out center로 받아 center 좌표를 노드처럼 사용
    - 이미 pipeline/raw/{도시ID}.json이 있으면 스킵 (재실행 안전)
    - 요청 사이 5초 대기
    - 실패 시 30초 대기 후 1회 재시도, 그래도 실패하면 해당 도시는
      건너뛰고 로그에 남긴다 (전체 중단하지 않음)
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# 도시ID: (남, 서, 북, 동)
CITIES = {
    "osaka":    (34.57, 135.38, 34.78, 135.61),
    "tokyo":    (35.47, 138.94, 35.90, 139.92),
    "fukuoka":  (33.50, 130.25, 33.70, 130.48),
    "kyoto":    (34.87, 135.55, 35.32, 135.88),
    "sapporo":  (42.95, 141.20, 43.15, 141.50),
    "naha":     (26.15, 127.62, 26.35, 127.78),
    "nagoya":   (35.03, 136.79, 35.25, 137.06),
    "kobe":     (34.62, 135.08, 34.78, 135.32),
    "nara":     (34.63, 135.77, 34.72, 135.92),
    "yokohama": (35.30, 139.48, 35.56, 139.70),
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(SCRIPT_DIR, "raw")


def build_query(bbox):
    south, west, north, east = bbox
    return f"""[out:json][timeout:180];
(
  node["amenity"="toilets"]({south},{west},{north},{east});
  way["amenity"="toilets"]({south},{west},{north},{east});
);
out center tags;
"""


def fetch_city(city_id, bbox, attempt_label=""):
    query = build_query(bbox)
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
    with urllib.request.urlopen(req, timeout=200) as resp:
        body = resp.read()
        return json.loads(body.decode("utf-8"))


def main():
    os.makedirs(RAW_DIR, exist_ok=True)

    results = []
    total_cities = len(CITIES)
    city_ids = list(CITIES.keys())

    for idx, city_id in enumerate(city_ids):
        bbox = CITIES[city_id]
        out_path = os.path.join(RAW_DIR, f"{city_id}.json")

        if os.path.exists(out_path):
            try:
                with open(out_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                n = len(existing.get("elements", []))
                print(f"[SKIP] {city_id}: 기존 파일 존재 ({n}건) - {out_path}")
                results.append((city_id, "skip", n))
            except Exception as e:
                print(f"[WARN] {city_id}: 기존 파일 파싱 실패({e}), 재수집 시도")
                results.append(_do_fetch(city_id, bbox, out_path))
            continue

        results.append(_do_fetch(city_id, bbox, out_path))

        # 마지막 도시가 아니면 5초 대기
        if idx < total_cities - 1:
            time.sleep(5)

    print("\n=== 수집 결과 요약 ===")
    ok = 0
    fail = 0
    for city_id, status, count in results:
        print(f"  {city_id}: {status} ({count}건)")
        if status == "fail":
            fail += 1
        else:
            ok += 1
    print(f"\n성공/스킵: {ok}개 도시, 실패: {fail}개 도시")

    if fail > 0:
        print("실패한 도시가 있습니다. 위 로그를 확인하세요.", file=sys.stderr)


def _do_fetch(city_id, bbox, out_path):
    print(f"[FETCH] {city_id} 수집 시작...")
    try:
        result = fetch_city(city_id, bbox)
        elements = result.get("elements", [])
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        print(f"[OK] {city_id}: {len(elements)}건 저장 -> {out_path}")
        return (city_id, "ok", len(elements))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        print(f"[ERROR] {city_id}: 1차 시도 실패 ({e}). 30초 대기 후 재시도...")
        time.sleep(30)
        try:
            result = fetch_city(city_id, bbox)
            elements = result.get("elements", [])
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False)
            print(f"[OK] {city_id}: (재시도 성공) {len(elements)}건 저장 -> {out_path}")
            return (city_id, "ok", len(elements))
        except Exception as e2:
            print(f"[FAIL] {city_id}: 재시도도 실패 ({e2}). 이 도시는 건너뜁니다.")
            return (city_id, "fail", 0)
    except Exception as e:
        print(f"[FAIL] {city_id}: 예기치 않은 오류 ({e}). 이 도시는 건너뜁니다.")
        return (city_id, "fail", 0)


if __name__ == "__main__":
    main()
