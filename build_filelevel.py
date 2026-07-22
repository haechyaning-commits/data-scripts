#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""파일 단위 카탈로그(카탈로그_파일목록.csv) 생성 — 자체감사파일1~4 전체.
   입력: filelist.json(전체 파일, .part 조각은 원본으로 묶음),
         카탈로그_보고서목록_new.csv(보고서 메타).
   출력: 카탈로그_파일목록.csv (각 파일 1행, 새 폴더명 GitHub 링크).
"""
import re, csv, json, os
from collections import defaultdict
from urllib.parse import quote

ROOT = os.path.dirname(os.path.abspath(__file__))
FILELIST = os.path.join(ROOT, "filelist.json")
REP = os.path.join(ROOT, "카탈로그_보고서목록_new.csv")
OUT = os.path.join(ROOT, "카탈로그_파일목록.csv")
REPO = "https://github.com/haechyaning-commits/data/blob/main"

re_num = re.compile(r"\(\d+\)$")           # 끝의 (N)
re_field = re.compile(r"^(.*)_(\d{4})년\s*(.*)$")


def base_of(display):
    """표시 파일명 → 카탈로그 파일명패턴(확장자·끝 (N) 제거)."""
    b = display.rsplit(".", 1)[0] if "." in display else display
    return re_num.sub("", b)


def parse_name(display):
    """파일명에서 기관·연도·감사분야 추출(카탈로그 미매칭 시 폴백)."""
    b = base_of(display)
    m = re_field.match(b)
    if m:
        return m.group(1), m.group(2), (m.group(3).strip() or "기타")
    return b, "", ""


# 보고서 카탈로그: (폴더, 파일명패턴) -> [reports]
reps = defaultdict(list)
for r in csv.DictReader(open(REP, encoding="utf-8-sig")):
    reps[(r["폴더"], r["파일명패턴"])].append(r)


def merge(reports):
    subj = " / ".join(sorted({x["감사사항명"] for x in reports if x["감사사항명"]}))
    kinds = set()
    for x in reports:
        for k in x["처분종류"].split("; "):
            if k.strip():
                kinds.add(k.strip())
    model = "Y" if any(x["모범사례포함"] == "Y" for x in reports) else "N"
    r0 = reports[0]
    return r0["기관"], r0["연도"], r0["감사분야"], subj, "; ".join(sorted(kinds)), model, len(reports)


fl = json.load(open(FILELIST, encoding="utf-8"))
rows = []
matched = 0
for folder in fl["folders"]:
    for display, parts, is_split in fl["files"].get(folder, []):
        base = base_of(display)
        reports = reps.get((folder, base))
        if reports:
            inst, yr, fld, subj, kinds, model, ncnt = merge(reports)
            matched += 1
        else:
            inst, yr, fld = parse_name(display)
            subj, kinds, model, ncnt = "", "", "N", 0
        if is_split:  # 분할: 조각 링크들을 공백으로 이어 붙임
            url = " ".join(f"{REPO}/{quote(folder)}/{quote(p)}" for p in parts)
        else:
            url = f"{REPO}/{quote(folder)}/{quote(display)}"
        rows.append([folder, display, inst, yr, fld, subj, kinds, model, url, ncnt, "Y" if is_split else "N"])

rows.sort(key=lambda r: (r[0], r[1]))
with open(OUT, "w", encoding="utf-8", newline="") as f:
    f.write("﻿")
    w = csv.writer(f)
    w.writerow(["폴더", "파일명", "기관", "연도", "감사분야", "감사사항명", "처분종류", "모범사례포함", "GitHub링크", "감사중복수", "분할파일"])
    for r in rows:
        w.writerow(r)
print(f"완료: {OUT}  파일 {len(rows):,}행, 카탈로그 매칭 {matched:,}, 모범사례 {sum(1 for r in rows if r[7]=='Y'):,}  ({os.path.getsize(OUT)//1024//1024}MB)")
