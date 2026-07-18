#!/usr/bin/env python3
"""
YAM 일본 화장실 데이터 파이프라인 - Phase 0
raw/{도시ID}.json (OSM Overpass 결과)을 정제 -> 중복 제거 -> 최종 스키마로 변환한다.

표준 라이브러리만 사용.

출력: ../japan_toilets.json (minified JSON 배열, UTF-8, ensure_ascii=False)

레코드 스키마:
{"n":"이름","a":"주소","la":34.6659,"ln":135.5013,"t":"공중화장실","h":"24/7","p":"","c":"osaka","s":"osm","f":{"wc":1,"ba":1}}
"""

import json
import math
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(SCRIPT_DIR, "raw")
OUTPUT_PATH = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "japan_toilets.json"))

CITY_IDS = [
    "osaka", "tokyo", "fukuoka", "kyoto", "sapporo",
    "naha", "nagoya", "kobe", "nara", "yokohama",
]

DEDUP_RADIUS_M = 30.0
GRID_SIZE_DEG = 0.0005  # 대략 55m 격자

LAT_MIN, LAT_MAX = 24.0, 46.0
LON_MIN, LON_MAX = 122.0, 154.0


def build_address(tags):
    if "addr:full" in tags and tags["addr:full"].strip():
        return tags["addr:full"].strip()
    parts_order = [
        "addr:province", "addr:state", "addr:city", "addr:suburb",
        "addr:district", "addr:neighbourhood", "addr:quarter",
        "addr:street", "addr:block_number", "addr:housenumber",
    ]
    parts = []
    for key in parts_order:
        if key in tags and tags[key].strip():
            parts.append(tags[key].strip())
    if parts:
        return "".join(parts) if all(len(p) <= 6 for p in parts) else " ".join(parts)
    return ""


def build_name(tags):
    if "name:ko" in tags and tags["name:ko"].strip():
        return tags["name:ko"].strip()
    if "name" in tags and tags["name"].strip():
        return tags["name"].strip()
    return "公衆トイレ"


def build_features(tags):
    f = {}
    if tags.get("wheelchair") == "yes":
        f["wc"] = 1
    if tags.get("changing_table") == "yes":
        f["ba"] = 1
    if tags.get("toilets:ostomate") == "yes" or tags.get("ostomate") == "yes":
        f["os"] = 1
    return f


def is_excluded(tags):
    access = tags.get("access", "")
    return access in ("private", "customers")


def extract_records(raw_elements, city_id):
    records = []
    for el in raw_elements:
        tags = el.get("tags", {}) or {}
        if is_excluded(tags):
            continue

        if el.get("type") == "node":
            lat = el.get("lat")
            lon = el.get("lon")
        elif el.get("type") == "way":
            center = el.get("center")
            if not center:
                continue
            lat = center.get("lat")
            lon = center.get("lon")
        else:
            continue

        if lat is None or lon is None:
            continue

        lat = round(float(lat), 6)
        lon = round(float(lon), 6)

        rec = {
            "n": build_name(tags),
            "a": build_address(tags),
            "la": lat,
            "ln": lon,
            "t": "공중화장실",
            "h": tags.get("opening_hours", ""),
            "p": "",
            "c": city_id,
            "s": "osm",
        }
        feats = build_features(tags)
        if feats:
            rec["f"] = feats
        rec["_tag_count"] = len(tags)  # 중복제거 우선순위용 (출력 전 제거)
        records.append(rec)
    return records


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def grid_key(lat, lon):
    return (math.floor(lat / GRID_SIZE_DEG), math.floor(lon / GRID_SIZE_DEG))


def dedup_city(records):
    """같은 도시 내 30m 이내 중복 제거. 격자 버킷으로 이웃 버킷만 비교 (O(n) 근사)."""
    buckets = {}
    for idx, rec in enumerate(records):
        key = grid_key(rec["la"], rec["ln"])
        buckets.setdefault(key, []).append(idx)

    removed = set()
    kept_in_bucket_area = {}  # 처리 순서 관리 불필요, 아래서 직접 비교

    # 각 레코드에 대해 자신의 버킷 + 8개 이웃 버킷의 "이미 확정된" 레코드와 비교
    # 안정적인 결과를 위해 정렬 순서(입력 순서) 기준으로 처리하고,
    # 중복 쌍이 발견되면 tag_count가 적은 쪽을 제거 대상으로 표시한다.
    n = len(records)
    active = list(range(n))  # 아직 제거되지 않은 인덱스

    # 인접 버킷 조회를 위해 버킷맵을 유지하되, 제거된 항목은 active에서 제외하고 최종 필터링
    for idx in range(n):
        if idx in removed:
            continue
        lat_a, lon_a = records[idx]["la"], records[idx]["ln"]
        gy, gx = grid_key(lat_a, lon_a)
        neighbor_candidates = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                neighbor_candidates.extend(buckets.get((gy + dy, gx + dx), []))
        for jdx in neighbor_candidates:
            if jdx <= idx or jdx in removed:
                continue
            lat_b, lon_b = records[jdx]["la"], records[jdx]["ln"]
            dist = haversine_m(lat_a, lon_a, lat_b, lon_b)
            if dist <= DEDUP_RADIUS_M:
                # 태그 많은 쪽을 남긴다
                if records[idx]["_tag_count"] >= records[jdx]["_tag_count"]:
                    removed.add(jdx)
                else:
                    removed.add(idx)
                    break  # idx 자체가 제거됐으므로 더 비교할 필요 없음

    return [rec for i, rec in enumerate(records) if i not in removed]


def strip_internal_fields(records):
    for rec in records:
        rec.pop("_tag_count", None)
    return records


def validate(all_records):
    errors = []
    for rec in all_records:
        if not (LAT_MIN <= rec["la"] <= LAT_MAX):
            errors.append(f"위도 범위 밖: {rec}")
        if not (LON_MIN <= rec["ln"] <= LON_MAX):
            errors.append(f"경도 범위 밖: {rec}")
    if errors:
        for e in errors[:20]:
            print(f"[검증 실패] {e}", file=sys.stderr)
        raise SystemExit(f"검증 실패: {len(errors)}건의 좌표 범위 오류. 중단합니다.")

    # 도시별 30m 이내 중복 재검사 (전수 검증, 격자 인접 비교로 근사 O(n))
    by_city = {}
    for rec in all_records:
        by_city.setdefault(rec["c"], []).append(rec)

    dup_count = 0
    for city_id, recs in by_city.items():
        buckets = {}
        for i, rec in enumerate(recs):
            buckets.setdefault(grid_key(rec["la"], rec["ln"]), []).append(i)
        for i, rec in enumerate(recs):
            gy, gx = grid_key(rec["la"], rec["ln"])
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    for j in buckets.get((gy + dy, gx + dx), []):
                        if j <= i:
                            continue
                        d = haversine_m(rec["la"], rec["ln"], recs[j]["la"], recs[j]["ln"])
                        if d <= DEDUP_RADIUS_M:
                            dup_count += 1
    if dup_count > 0:
        raise SystemExit(f"검증 실패: 중복 제거 후에도 30m 이내 중복 {dup_count}쌍 발견. 중단합니다.")

    print("[검증 통과] 좌표 범위 OK, 30m 이내 중복 0건")


def main():
    all_records = []
    per_city_stats = []

    for city_id in CITY_IDS:
        raw_path = os.path.join(RAW_DIR, f"{city_id}.json")
        if not os.path.exists(raw_path):
            print(f"[SKIP] {city_id}: raw 파일 없음 ({raw_path})")
            per_city_stats.append((city_id, 0, 0))
            continue

        with open(raw_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        elements = raw.get("elements", [])
        raw_count = len(elements)

        records = extract_records(elements, city_id)
        before_dedup = len(records)
        records = dedup_city(records)
        after_dedup = len(records)

        records = strip_internal_fields(records)
        all_records.extend(records)
        per_city_stats.append((city_id, raw_count, after_dedup))
        print(f"[{city_id}] 원본 {raw_count}건 -> 제외/필터 후 {before_dedup}건 -> 중복제거 후 {after_dedup}건")

    validate(all_records)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, separators=(",", ":"))

    size_bytes = os.path.getsize(OUTPUT_PATH)
    size_mb = size_bytes / (1024 * 1024)

    print("\n=== 최종 결과 ===")
    print(f"{'도시':<10}{'원본':>8}{'정제후':>8}")
    total_raw = 0
    total_final = 0
    for city_id, raw_count, final_count in per_city_stats:
        print(f"{city_id:<10}{raw_count:>8}{final_count:>8}")
        total_raw += raw_count
        total_final += final_count
    print(f"{'합계':<10}{total_raw:>8}{total_final:>8}")
    print(f"\n출력 파일: {OUTPUT_PATH}")
    print(f"파일 크기: {size_bytes:,} bytes ({size_mb:.2f} MB)")
    if size_bytes > 2 * 1024 * 1024:
        print("[주의] 파일 크기가 2MB를 초과합니다.")


if __name__ == "__main__":
    main()
