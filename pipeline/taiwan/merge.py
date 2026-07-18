#!/usr/bin/env python3
"""
YAM 대만 화장실 데이터 파이프라인 - 병합 (환경부 주력 + OSM 보조)
raw/moenv.json (환경부 全國公廁建檔資料 FAC_P_07) + raw/taiwan.json (OSM Overpass)을
정제 -> 건물 단위 그룹핑(환경부) -> 우선순위 병합(환경부 > OSM) -> 존 판정 -> 중간 스키마로 변환한다.

환경부 데이터 특성: 물리적 화장실 1곳이 남廁/여廁/친자廁/무장애廁·층별 개별 레코드
  -> name에서 성별·층 접미사를 제거한 기본명 + 주소로 그룹핑해 건물 단위 1레코드로 병합.

병합 규칙:
  - 우선순위 환경부(gov) > OSM. 30m 이내 중복 시 환경부 유지, OSM의 h(운영시간)만 보충.
  - OSM 단독 지점은 유지 (등산로 등 환경부 미등재 결측 보충).
  - grade -> g 코드: 特優級=1, 優等級=2, 그 외(普通級/改善級/不合格) 생략.
  - type2 -> t 매핑: 상업 계열(TYPE2_OPEN)만 "개방화장실", 그 외 전부 "공중화장실".

표준 라이브러리만 사용.
출력: merged_taiwan.json (중간 산출물, translate.py의 입력)
"""

import json
import math
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(SCRIPT_DIR, "raw")
RAW_OSM_PATH = os.path.join(RAW_DIR, "taiwan.json")
RAW_MOENV_PATH = os.path.join(RAW_DIR, "moenv.json")
OUTPUT_PATH = os.path.join(SCRIPT_DIR, "merged_taiwan.json")

DEDUP_RADIUS_M = 30.0
GRID_SIZE_DEG = 0.0005  # 대략 55m 격자

# 대만 좌표 검증 범위 (펑후·진먼 포함)
LAT_MIN, LAT_MAX = 21.5, 26.5
LON_MIN, LON_MAX = 117.5, 122.5

# 8개 존 테이블: id -> (남,서,북,동)
ZONES = {
    "taipei":    (24.95, 121.40, 25.22, 121.67),
    "jiufen":    (25.00, 121.65, 25.25, 121.95),
    "taoyuan":   (24.90, 121.15, 25.12, 121.40),
    "taichung":  (24.05, 120.45, 24.35, 120.75),
    "sunmoon":   (23.80, 120.85, 23.95, 121.00),
    "tainan":    (22.93, 120.13, 23.05, 120.25),
    "kaohsiung": (22.55, 120.25, 22.70, 120.40),
    "hualien":   (23.90, 121.45, 24.20, 121.65),
}

# 위생 등급 우선순위 (낮을수록 좋음)
GRADE_RANK = {"特優級": 1, "優等級": 2, "普通級": 3, "改善級": 4, "不合格": 5}

# type2 -> "개방화장실" 매핑 (상업 개방 계열). 목록 밖은 전부 "공중화장실".
# 2026-07-16 실측 분포 기준 매핑표 (taiwan_sources.md에 기록):
#   商業營業場所 12,522 -> 개방 / 休閒娛樂場所 248 -> 개방
#   民眾洽公場所 7,323·社福機構、集會場所 6,017·文化育樂活動場所 5,925·交通 4,084·
#   公園 3,511·觀光地區及風景區 3,263·宗教禮儀場所 1,838·其他 984·(없음) 3 -> 공중
TYPE2_OPEN = {
    "商業營業場所",   # 상업 영업장 (백화점·마트·음식점·주유소 등)
    "休閒娛樂場所",   # 레저·오락시설 (KTV·오락실 등)
}


def zone_of(lat, lon):
    for zid, (s, w, n, e) in ZONES.items():
        if s <= lat <= n and w <= lon <= e:
            return zid
    return ""


# ══════════════ 환경부(MOENV) 처리 ══════════════

# 이름 접미사 제거 패턴 (반복 적용)
_SEP = r'[\-–—_/·\s\(\)（）]*'
_RE_TYPE_SUFFIX = re.compile(
    _SEP + r'(男女|親子|無障礙|性別友善|混合|通用|殘障|男|女)(生)?[廁厠]所?'
    + _SEP + r'(多功能|\d+)?' + _SEP + r'$')
_RE_FLOOR_SUFFIX = re.compile(
    _SEP + r'(B\d+F?|\d+F|GF|[0-9一二三四五六七八九十]+樓|地下\d*樓?|地下室)'
    + _SEP + r'(公?[廁厠]所?)?' + _SEP + r'$')
# 성별·설비 한정어가 廁 없이 끝에 붙는 경우: 여러 글자 한정어는 구분자 없이도 제거,
# 한 글자(男/女)는 오제거 방지를 위해 구분자 또는 층 표기(F/樓/숫자) 뒤에서만 제거
_RE_DANGLING_QUAL = re.compile(r'[\-–—_/·\s]*(無障礙|親子|性別友善|殘障|男女)$')
_RE_DANGLING_GENDER = re.compile(r'([\-–—_/·\s]+|(?<=F)|(?<=樓)|(?<=\d))(男|女)$')
_RE_UNBALANCED_PAREN = re.compile(r'[\(（][^\)）]*$')
_RE_TRAIL_SEP = re.compile(r'[\-–—_/·\s]+$')


def strip_name_suffix(name):
    """성별·층·칸 접미사를 제거한 그룹핑용 기본명 추출.
    예: 太平區公所4F-男廁 -> 太平區公所 / 好巿多costco-新高雄店-4F男廁 -> 好巿多costco-新高雄店
        漢神洲際購物廣場(西南側 -> 漢神洲際購物廣場 (접미사 제거 후 미닫힘 괄호 정리)"""
    base = name.strip()
    while True:
        prev = base
        base = _RE_TYPE_SUFFIX.sub("", base)
        base = _RE_FLOOR_SUFFIX.sub("", base)
        base = _RE_DANGLING_QUAL.sub("", base)
        base = _RE_DANGLING_GENDER.sub("", base)  # 룩비하인드는 비소비라 F/樓/숫자는 보존됨
        base = _RE_UNBALANCED_PAREN.sub("", base)
        base = _RE_TRAIL_SEP.sub("", base)
        if base == prev:
            break
    return base.strip()


def extract_moenv_groups(raw_records):
    """환경부 레코드를 건물 단위로 그룹핑해 병합 레코드 목록을 만든다."""
    groups = {}
    skipped_coord = 0
    for r in raw_records:
        try:
            la = round(float(r.get("latitude", "")), 5)  # 5자리 ≈ 1.1m (용량 절감 ③)
            ln = round(float(r.get("longitude", "")), 5)
        except (TypeError, ValueError):
            skipped_coord += 1
            continue
        if la == 0 or ln == 0 or not (LAT_MIN <= la <= LAT_MAX and LON_MIN <= ln <= LON_MAX):
            skipped_coord += 1
            continue

        name = (r.get("name") or "").strip()
        addr = (r.get("address") or "").strip()
        base = strip_name_suffix(name)
        # 그룹핑 키: 기본명+주소. 기본명이 비면 administration, 주소가 비면 좌표(약 11m 격자)
        key_name = base or (r.get("administration") or "").strip() or name
        key_addr = addr or f"@{round(la, 4)},{round(ln, 4)}"
        key = (key_name, key_addr)

        g = groups.setdefault(key, {
            "base": base, "first_name": name, "key_name": key_name, "addr": addr,
            "la": la, "ln": ln, "grade_rank": 99,
            "ba": False, "wc": False, "type2": "", "members": 0,
        })
        g["members"] += 1
        rank = GRADE_RANK.get((r.get("grade") or "").strip(), 99)
        if rank < g["grade_rank"]:
            g["grade_rank"] = rank
            g["la"], g["ln"] = la, ln  # 최고 등급 레코드의 좌표 채택
            g["type2"] = (r.get("type2") or "").strip()
        elif not g["type2"]:
            g["type2"] = (r.get("type2") or "").strip()
        try:
            if int(r.get("diaper") or 0) > 0:
                g["ba"] = True
        except (TypeError, ValueError):
            pass
        if "無障礙" in name or "無障礙" in (r.get("type") or ""):
            g["wc"] = True

    records = []
    for g in groups.values():
        # 병합 후 잔여 이름이 빈약하면(2자 미만) 그룹 키 이름, 그것도 빈약하면 원본 유지
        if len(g["base"]) >= 2:
            display = g["base"]
        elif len(g["key_name"]) >= 2:
            display = g["key_name"]
        else:
            display = g["first_name"]
        rec = {
            "n": display,
            "a": g["addr"],
            "la": g["la"],
            "ln": g["ln"],
            "t": "개방화장실" if g["type2"] in TYPE2_OPEN else "공중화장실",
            "h": "",
            "p": "",
            "c": zone_of(g["la"], g["ln"]),
            "s": "gov",
        }
        if g["grade_rank"] == 1:
            rec["g"] = 1
        elif g["grade_rank"] == 2:
            rec["g"] = 2
        feats = {}
        if g["wc"]:
            feats["wc"] = 1
        if g["ba"]:
            feats["ba"] = 1
        if feats:
            rec["f"] = feats
        # 중복제거 우선순위: gov는 항상 OSM(태그 수십 개 미만)보다 우선, gov끼리는 등급 좋은 쪽
        rec["_tag_count"] = 1000 - g["grade_rank"]
        records.append(rec)

    return records, skipped_coord


# ══════════════ OSM 처리 (기존과 동일) ══════════════

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
    return "公廁"


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


def extract_osm_records(raw_elements):
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

        lat = round(float(lat), 5)  # 5자리 ≈ 1.1m (용량 절감 ③)
        lon = round(float(lon), 5)
        if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
            continue

        rec = {
            "n": build_name(tags),
            "a": build_address(tags),
            "la": lat,
            "ln": lon,
            "t": "공중화장실",
            "h": tags.get("opening_hours", ""),
            "p": "",
            "c": zone_of(lat, lon),
            "s": "osm",
        }
        feats = build_features(tags)
        if feats:
            rec["f"] = feats
        rec["_tag_count"] = len(tags)  # OSM끼리 우선순위 (gov의 1000-α 보다 항상 낮음)
        records.append(rec)
    return records


# ══════════════ 공통: 격자·거리·중복제거 ══════════════

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


def dedup_all(records):
    """30m 이내 중복 제거. _tag_count 큰 쪽 유지 (gov > OSM 보장).
    gov가 OSM을 이기면 OSM의 h(운영시간)를 gov에 보충."""
    buckets = {}
    for idx, rec in enumerate(records):
        key = grid_key(rec["la"], rec["ln"])
        buckets.setdefault(key, []).append(idx)

    removed = set()
    h_supplemented = 0
    n = len(records)

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
                if records[idx]["_tag_count"] >= records[jdx]["_tag_count"]:
                    keep, drop = records[idx], records[jdx]
                    removed.add(jdx)
                else:
                    keep, drop = records[jdx], records[idx]
                    removed.add(idx)
                # gov가 OSM을 대체할 때 OSM 운영시간 보충
                if keep["s"] == "gov" and drop["s"] == "osm" and not keep["h"] and drop["h"]:
                    keep["h"] = drop["h"]
                    h_supplemented += 1
                if drop is records[idx]:
                    break

    kept = [rec for i, rec in enumerate(records) if i not in removed]
    return kept, h_supplemented


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

    buckets = {}
    for i, rec in enumerate(all_records):
        buckets.setdefault(grid_key(rec["la"], rec["ln"]), []).append(i)
    dup_count = 0
    for i, rec in enumerate(all_records):
        gy, gx = grid_key(rec["la"], rec["ln"])
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for j in buckets.get((gy + dy, gx + dx), []):
                    if j <= i:
                        continue
                    d = haversine_m(rec["la"], rec["ln"], all_records[j]["la"], all_records[j]["ln"])
                    if d <= DEDUP_RADIUS_M:
                        dup_count += 1
    if dup_count > 0:
        raise SystemExit(f"검증 실패: 중복 제거 후에도 30m 이내 중복 {dup_count}쌍 발견. 중단합니다.")

    print("[검증 통과] 좌표 범위 OK, 30m 이내 중복 0건")


def main():
    # ── 1. 환경부 ──
    gov_records = []
    gov_raw_count = 0
    if os.path.exists(RAW_MOENV_PATH):
        with open(RAW_MOENV_PATH, "r", encoding="utf-8") as f:
            moenv_raw = json.load(f)
        gov_raw_count = len(moenv_raw)
        gov_records, gov_skipped = extract_moenv_groups(moenv_raw)
        print(f"[환경부] 원본 {gov_raw_count}건 -> 건물 단위 그룹핑 후 {len(gov_records)}건 "
              f"(좌표 결측/0/범위밖 스킵 {gov_skipped}건)")
    else:
        print(f"[WARN] 환경부 raw 파일 없음: {RAW_MOENV_PATH} — OSM 단독으로 진행")

    # ── 2. OSM ──
    osm_records = []
    if os.path.exists(RAW_OSM_PATH):
        with open(RAW_OSM_PATH, "r", encoding="utf-8") as f:
            osm_raw = json.load(f)
        elements = osm_raw.get("elements", [])
        osm_records = extract_osm_records(elements)
        print(f"[OSM] 원본 {len(elements)}건 -> 제외/좌표검증 후 {len(osm_records)}건")
    else:
        print(f"[WARN] OSM raw 파일 없음: {RAW_OSM_PATH}")

    if not gov_records and not osm_records:
        print("[FAIL] 병합할 데이터가 없습니다.", file=sys.stderr)
        sys.exit(1)

    # ── 3. 병합 + 30m 중복제거 (gov 우선) ──
    all_records = gov_records + osm_records
    before_dedup = len(all_records)
    all_records, h_supp = dedup_all(all_records)
    after_dedup = len(all_records)
    all_records = strip_internal_fields(all_records)

    validate(all_records)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, separators=(",", ":"))

    # ── 4. 통계 ──
    zone_counts = {}
    src_counts = {}
    grade_counts = {}
    for rec in all_records:
        zone_counts[rec["c"] or "(존 밖)"] = zone_counts.get(rec["c"] or "(존 밖)", 0) + 1
        src_counts[rec["s"]] = src_counts.get(rec["s"], 0) + 1
        if "g" in rec:
            grade_counts[rec["g"]] = grade_counts.get(rec["g"], 0) + 1

    size_bytes = os.path.getsize(OUTPUT_PATH)
    size_mb = size_bytes / (1024 * 1024)

    print("\n=== 병합 결과 ===")
    print(f"환경부 그룹핑 {len(gov_records)}건 + OSM {len(osm_records)}건 = {before_dedup}건 "
          f"-> 30m 중복제거 후 {after_dedup}건 (gov에 OSM 운영시간 보충 {h_supp}건)")
    print(f"소스별: {src_counts}")
    print(f"g 분포 (1=特優級, 2=優等級): {grade_counts}")
    print("\n=== 존별 건수 ===")
    for zid in list(ZONES.keys()) + ["(존 밖)"]:
        print(f"  {zid:<12}{zone_counts.get(zid, 0):>6}")
    print(f"\n중간 출력 파일: {OUTPUT_PATH}")
    print(f"파일 크기: {size_bytes:,} bytes ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
