#!/usr/bin/env python3
"""
YAM 대만 화장실 데이터 파이프라인 - 환경부 데이터 수집
환경부(MOENV) 「全國公廁建檔資料」(FAC_P_07)를 offset 페이지네이션으로 전량 수집한다.

API 키: data.gov.tw가 공식 배포하는 다운로드 URL에 내장된 공개 키 사용
        (추후 사용자 개인 키로 교체 권장 — taiwan_sources.md 참조)

표준 라이브러리만 사용.

사용법:
    python3 fetch_moenv.py

동작:
    - limit=1000 페이지네이션 전량 수집 (약 50회 호출)
    - 요청 간 1초 대기, 실패 시 30초 후 1회 재시도
    - 이미 pipeline/raw/moenv.json이 있으면 스킵 (재실행 안전)
    - 수집 후 통계 출력: 총 건수, 좌표 결측/0/범위밖, grade 분포, type2 분포
"""

import json
import os
import subprocess
import sys
import time

API_KEY = "e75b1660-e564-4107-aad5-a8be1f905dd9"  # data.gov.tw 배포 URL 내장 공개 키
BASE_URL = ("https://data.moenv.gov.tw/api/v2/fac_p_07"
            "?api_key={key}&limit=1000&offset={offset}&format=JSON&sort=ImportDate%20desc")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(SCRIPT_DIR, "raw")
OUT_PATH = os.path.join(RAW_DIR, "moenv.json")

# 대만 좌표 검증 범위 (통계용 — 필터링은 merge.py에서)
LAT_MIN, LAT_MAX = 21.5, 26.5
LON_MIN, LON_MAX = 117.5, 122.5


def fetch_page(offset):
    # 주의: data.moenv.gov.tw 인증서가 Python 3.14 OpenSSL 검증
    # ("Missing Subject Key Identifier")에 걸려 urllib 대신 curl 사용
    url = BASE_URL.format(key=API_KEY, offset=offset)
    result = subprocess.run(
        ["curl", "-s", "--max-time", "60", "--fail",
         "-A", "YAM-toilet-app-data-pipeline/1.0 (contact: internal use)", url],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl 실패 (exit {result.returncode})")
    return json.loads(result.stdout.decode("utf-8"))


def fetch_page_with_retry(offset):
    try:
        return fetch_page(offset)
    except (RuntimeError, json.JSONDecodeError) as e:
        print(f"[ERROR] offset={offset} 1차 실패 ({e}). 30초 대기 후 재시도...")
        time.sleep(30)
        return fetch_page(offset)


def main():
    os.makedirs(RAW_DIR, exist_ok=True)

    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f)
            print(f"[SKIP] moenv: 기존 파일 존재 ({len(existing)}건) - {OUT_PATH}")
            print_stats(existing)
            return
        except Exception as e:
            print(f"[WARN] 기존 파일 파싱 실패({e}), 재수집 시도")

    all_records = []
    offset = 0
    while True:
        try:
            data = fetch_page_with_retry(offset)
        except Exception as e:
            print(f"[FAIL] offset={offset} 재시도도 실패 ({e}). 수집 중단.", file=sys.stderr)
            sys.exit(1)

        # 응답이 bare list거나 {"records":[...]} 두 형태 모두 대응
        records = data if isinstance(data, list) else data.get("records", [])
        if not records:
            print(f"[DONE] offset={offset}: 빈 응답 — 수집 종료")
            break
        all_records.extend(records)
        print(f"[OK] offset={offset}: {len(records)}건 (누적 {len(all_records)}건)")
        offset += 1000
        time.sleep(1)

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False)
    print(f"\n총 {len(all_records)}건 저장 -> {OUT_PATH}")
    print_stats(all_records)


def print_stats(records):
    total = len(records)
    missing = 0
    zero = 0
    out_of_range = 0
    grades = {}
    type2s = {}
    for r in records:
        la_raw = r.get("latitude", "")
        ln_raw = r.get("longitude", "")
        try:
            la = float(la_raw)
            ln = float(ln_raw)
        except (TypeError, ValueError):
            missing += 1
            la = ln = None
        if la is not None:
            if la == 0 or ln == 0:
                zero += 1
            elif not (LAT_MIN <= la <= LAT_MAX and LON_MIN <= ln <= LON_MAX):
                out_of_range += 1
        g = (r.get("grade") or "").strip() or "(없음)"
        grades[g] = grades.get(g, 0) + 1
        t2 = (r.get("type2") or "").strip() or "(없음)"
        type2s[t2] = type2s.get(t2, 0) + 1

    print("\n=== 수집 통계 ===")
    print(f"총 건수: {total}")
    print(f"좌표 결측(파싱불가): {missing} / 0좌표: {zero} / 대만범위 밖: {out_of_range}")
    print("\n--- grade 분포 ---")
    for g, c in sorted(grades.items(), key=lambda x: -x[1]):
        print(f"  {g}: {c}")
    print("\n--- type2 분포 ---")
    for t, c in sorted(type2s.items(), key=lambda x: -x[1]):
        print(f"  {t}: {c}")


if __name__ == "__main__":
    main()
