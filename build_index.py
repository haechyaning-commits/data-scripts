#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
공공감사포털 자체감사결과 마스터 인덱스 생성기.

전 파일에 대해 파일명 기반 메타데이터(기관/연도/감사분야/순번)를 100% 채우고,
가능한 경우 문서 본문에서 '지적제목'과 '처분키워드'를 추출해 덧붙인다.

지원 추출:
  - pdf  : PyMuPDF(fitz), 앞 2페이지
  - hwpx : zip + section XML의 <hp:t>
  - hwp  : olefile + BodyText 섹션 zlib 해제 + PARA_TEXT 레코드 디코딩
  - txt  : 그대로
결과: 저장소 루트에 감사지적_마스터인덱스.csv (UTF-8 with BOM, 엑셀 호환)
"""
import os, re, io, csv, sys, json, zlib, zipfile, traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_ROOT = "/home/user/data"
# 대상 폴더는 인자로 지정(기본 자체감사파일3). 폴더별 findings CSV를 따로 생성해
# 대시보드가 여러 폴더의 지적을 합쳐 쓸 수 있게 한다.
FOLDER = next((a for a in sys.argv[1:] if not a.startswith("-")), "자체감사파일3")
DATA_DIR = os.path.join(DATA_ROOT, FOLDER)
OUT_CSV = os.path.join(SCRIPT_DIR, f"감사지적_마스터인덱스_{FOLDER}.csv")
PROGRESS = os.path.join(SCRIPT_DIR, f"index_progress_{FOLDER}.log")

# ----- 파일명 파싱 -----
STEM_RE = re.compile(r"^(?P<org>.+?)_(?P<year>\d{4})년\s*(?P<field>[^(]+?)(?:\((?P<seq>\d+)\))?$")

def parse_stem(stem):
    part = None
    m_part = re.search(r"_조각\((\d+)\)$", stem)
    if m_part:
        part = int(m_part.group(1))
        stem = stem[: m_part.start()]
    m = STEM_RE.match(stem)
    if not m:
        return {"org": "", "year": "", "field": "", "seq": "", "part": part, "parsed": False}
    return {
        "org": m.group("org").strip(),
        "year": m.group("year"),
        "field": m.group("field").strip(),
        "seq": m.group("seq") or "",
        "part": part,
        "parsed": True,
    }

# ----- 처분키워드 분류 -----
DISPO_KEYWORDS = ["징계", "경징계", "중징계", "문책", "주의", "경고", "시정", "개선",
                  "권고", "통보", "고발", "수사의뢰", "변상", "환수", "회수", "재심의",
                  "인사자료", "현지조치", "모범사례"]

def classify_dispo(text):
    if not text:
        return ""
    found = [k for k in DISPO_KEYWORDS if k in text]
    # 중복 제거·순서 유지
    seen, out = set(), []
    for k in found:
        if k not in seen:
            seen.add(k); out.append(k)
    return ";".join(out)

# ----- 지적제목 추출 -----
# 제목이 끝나는 경계 신호(표 헤더·섹션 라벨·감사서식). 공백 삽입에 관대하게 매칭해
# 제목 뒤에 딸려오는 표/처분 텍스트를 잘라낸다.
_STOP_WORDS = [
    r"소\s*관\s*부\s*서", r"조\s*치\s*부\s*서", r"관\s*련\s*부\s*서", r"소\s*관\s*팀",
    r"조\s*치\s*부\s*처", r"처\s*분\s*요\s*구", r"판\s*단\s*기\s*준", r"업\s*무\s*현\s*황",
    r"감\s*사\s*자", r"처\s*분\s*구\s*분", r"처\s*분\s*종\s*류", r"처\s*분\s*대\s*상\s*자",
    r"대\s*상\s*자\s*및\s*처\s*분", r"관\s*계\s*부\s*서", r"처\s*리\s*기\s*[한간]",
    r"일\s*련\s*번\s*호", r"수\s*령\s*자", r"시\s*행\s*년\s*도", r"조\s*치\s*할?\s*사\s*항",
    r"현\s*황\s*및\s*문\s*제\s*점", r"지\s*적\s*내\s*용", r"감\s*사\s*의\s*견",
    r"검\s*토\s*의\s*견", r"관\s*계\s*법\s*령", r"세\s*부\s*내\s*용", r"지\s*적\s*사\s*항",
    r"내\s*용", r"및\s*문\s*제\s*점",
    # 감사서식 표 헤더(띄어쓰기 관대)
    r"부\s*서\s*명", r"관\s*계\s*기\s*관", r"소\s*관\s*기\s*관", r"조\s*치\s*기\s*관",
    r"감\s*사\s*담\s*당", r"처\s*분\s*내\s*역", r"처\s*분\s*결\s*과", r"조\s*치\s*결\s*과",
    r"관\s*계\s*팀", r"소\s*속\s*및\s*성\s*명",
    # 불릿이 붙은 섹션 헤더(예: "○ 현황", "□ 문제점")
    r"[□○◯●▷▪‣]\s*(?:현\s*황|문\s*제\s*점|조\s*치\s*할?\s*사\s*항|지\s*적\s*사\s*항|검\s*토\s*의\s*견)",
]
# 번호가 붙은 섹션 헤더(예: "6. 조치", "3) 현황")
_NUM_SECTION = r"\d+\s*[.)]\s*(?:조치|현황|내용|검토|판단|처분|결론|의견|문제점)"
# 제목 뒤에 붙는 괄호 처분태그(예: "(권고)", "(“현지시정”)")
_DISPO_PAREN = (r"[(（]\s*[“\"']?\s*(?:현지시정|현지조치|기관경고|기관주의|주의|통보|"
                r"개선요구|개선|시정|권고|경고|징계|문책|재심의|변상|환수|회수|고발|신분|재정)"
                r"\s*[”\"']?\s*[)）]")
STOP = "|".join(_STOP_WORDS) + "|" + _NUM_SECTION + "|" + _DISPO_PAREN

TITLE_RE = re.compile(r"제\s*목\s*[:：]?\s*(.+?)\s*(?:" + STOP + r"|$)")
# 문자열 앞머리를 경계 신호 직전까지 잘라내는 용도(라벨 없는 줄에도 적용)
CUT_RE = re.compile(r"^(.+?)\s*(?:" + STOP + r")")
# 지적 어미 집합(오탐 방지를 위해 선별적으로 유지)
FINDING_SUFFIX = (r"미흡|부적정|소홀|과다|누락|위반|지연|부당|미이행|불합리|부실|미비|"
                  r"초과|오류|불철저|방만|불투명")
FINDING_RE = re.compile(r"([가-힣A-Za-z0-9()\-·「」『』\s]{4,45}?(?:" + FINDING_SUFFIX + r"))")

# 제목으로 부적절한 표지/행정/페이지 텍스트
_REJECT = [
    re.compile(r"^\s*[-–]?\s*\d+\s*[-–]?\s*$"),                       # 페이지 번호 "- 1 -"
    re.compile(r"^\d{1,4}\s*년?도?\s*(?:상반기|하반기|\d분기)?\s*"
               r"(?:정기|종합|특정|복무|성과|재무|일상|특별)?\s*감사"
               r"(?:\s*(?:사안별)?\s*감사?\s*결과)?\s*$"),            # 표지 "2016년도 정기감사 사안별 감사결과"
    re.compile(r"^(?:목\s*차|감사대상|감사결과보고서?|상임이사|이사장|사\s*장|"
               r"감사결과\s*처분요구서?|처분요구서|결과보고|감\s*사\s*결\s*과)\s*$"),
    re.compile(r"^\S*[팀실과부처]\s*[-–]\s*\d+"),                     # 문서번호 "감사팀-1229"
    re.compile(r"^\S{1,3}\s*[-–]\s*\d+\s*$"),                         # 짧은 코드 "가-12"
    re.compile(r"^감\s*사\s*(?:기간|대상|기관|반|일자|일정|목적|범위|"
               r"중점|연혁|근거|방법|기\s*간|담당)"),                 # 감사서식 행정줄 "감사기간 : ..."
    re.compile(r"(?:결과\s*보고서?|처분요구서|계획서)\s*$"),          # 표지 "특별감사 결과보고서"
    re.compile(r"상위\s*버전의?\s*배포용|한글\s*전용\s*뷰어"),        # 한글 배포용 문서 경고문
    re.compile(r"^[\W_]+$"),                                          # 기호만
]

# 앞뒤에서 벗겨낼 장식 기호(열림/닫힘 괄호 양쪽 모두 포함)
_EDGE = " .,:：;·∙・-–_〔〕【】「」『』（）()[]{}<>▷▪‣□○◯●■※\t　"

def _reject(t):
    return any(rx.match(t) for rx in _REJECT)

def _clean(t):
    """앞뒤 장식·잔여 기호·바이너리 잡토큰 제거."""
    if not t:
        return ""
    # 라틴+숫자 잡토큰(OCR/서식 아티팩트) 제거: 예) INSIDabcdef_:MS_0001
    t = re.sub(r"[A-Za-z]{4,}[A-Za-z0-9_:]*\d[A-Za-z0-9_:]*", " ", t)
    # 반복 기호 마스킹(♤♤♤, @@@, ☆☆☆, ###, ···) 축약
    t = re.sub(r"([@#♤☆★○◯●□■▷▪※·∙・\-–_=]){2,}", " ", t)
    # 앞쪽 열거·불릿 제거: 가. / 1) / □ / ○ / ▷ / 【 / 「 등
    t = re.sub(r"^\s*(?:[가-힣]\.\s*|\d+\s*[.)]\s*|[□○◯●▷▪※·∙‣∎【「『（(\[〔]\s*)+", "", t)
    # 앞뒤 장식 기호 제거
    t = t.strip(_EDGE)
    # 내부 다중 공백 정리
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t

def _good(t, lo=4, hi=60):
    return bool(t) and lo <= len(t) <= hi and not _reject(t)

def extract_title(text):
    if not text:
        return ""
    norm = re.sub(r"\s+", " ", text).strip()

    # 1) "제목:" 라벨 → 경계까지 캡처 후 정제
    m = TITLE_RE.search(norm)
    if m:
        t = _clean(m.group(1))
        if _good(t):
            return t
        # 여전히 길면 앞부분에서 지적 핵심구(어미) 추출
        if t:
            fm = FINDING_RE.search(t)
            if fm:
                ft = _clean(fm.group(1))
                if _good(ft):
                    return ft
            # 어미가 없으면 경계 앞 60자로 절단(단어 경계 우선)
            head = t[:60]
            cut = head.rsplit(" ", 1)[0] if len(t) > 60 and " " in head else head
            cut = _clean(cut)
            if _good(cut):
                return cut

    # 2) 본문에서 지적 어미로 끝나는 핵심구(서술형 보고서 대응)
    m2 = FINDING_RE.search(norm)
    if m2:
        t = _clean(m2.group(1))
        if _good(t):
            return t

    # 3) 표지/페이지/행정 줄을 걸러낸 첫 의미 있는 줄(경계 신호에서 잘라 표/처분태그 꼬리 제거)
    for line in text.splitlines():
        s = re.sub(r"\s+", " ", line).strip()
        cm = CUT_RE.match(s)
        if cm:
            s = cm.group(1)
        s = _clean(s)
        if _good(s, lo=6, hi=60) and "목 차" not in s:
            return s
    return ""

# ----- PDF -----
def extract_pdf(path):
    import fitz
    doc = fitz.open(path)
    try:
        parts = []
        for i in range(min(2, doc.page_count)):
            parts.append(doc.load_page(i).get_text("text"))
        return "\n".join(parts)
    finally:
        doc.close()

# ----- HWPX -----
def extract_hwpx(path):
    out = []
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if re.search(r"Contents/section\d+\.xml$", n)]
        names.sort()
        for n in names[:2]:
            xml = z.read(n).decode("utf-8", "ignore")
            # <hp:t ...>텍스트</hp:t>
            for t in re.findall(r"<hp:t[^>]*>(.*?)</hp:t>", xml, re.S):
                t = re.sub(r"<[^>]+>", "", t)
                t = (t.replace("&lt;", "<").replace("&gt;", ">")
                       .replace("&amp;", "&").replace("&quot;", '"'))
                out.append(t)
            out.append("\n")
            if sum(len(x) for x in out) > 6000:
                break
    return "".join(out)

# ----- HWP (OLE binary) -----
INLINE_EXT_CTRL = set([1,2,3,4,5,6,7,8,9,11,12,14,15,16,17,18,19,20,21,22,23])

def _decode_para(rec):
    res = []; i = 0; n = len(rec)
    while i + 2 <= n:
        wc = rec[i] | (rec[i+1] << 8)
        if wc in (0, 10, 13):
            if wc in (10, 13):
                res.append("\n")
            i += 2
        elif wc < 32:
            i += 8 if wc in INLINE_EXT_CTRL else 2
        elif 0xD800 <= wc <= 0xDFFF:
            i += 2  # 잘못된 서로게이트 코드포인트 제거
        else:
            res.append(chr(wc)); i += 2
    return "".join(res)

def _section_text(data):
    out = []; i = 0; n = len(data)
    while i + 4 <= n:
        header = int.from_bytes(data[i:i+4], "little"); i += 4
        tag = header & 0x3FF
        size = (header >> 20) & 0xFFF
        if size == 0xFFF:
            size = int.from_bytes(data[i:i+4], "little"); i += 4
        rec = data[i:i+size]; i += size
        if tag == 67:  # HWPTAG_PARA_TEXT
            out.append(_decode_para(rec))
        if sum(len(x) for x in out) > 6000:
            break
    return "\n".join(out)

def extract_hwp(path):
    import olefile
    ole = olefile.OleFileIO(path)
    try:
        compressed = True
        if ole.exists("FileHeader"):
            fh = ole.openstream("FileHeader").read()
            if len(fh) > 37:
                compressed = bool(fh[36] & 0x01)
        secs = []
        for entry in ole.listdir():
            if len(entry) == 2 and entry[0] == "BodyText" and entry[1].startswith("Section"):
                secs.append(entry)
        secs.sort(key=lambda e: int(re.sub(r"\D", "", e[1]) or 0))
        texts = []
        for entry in secs[:2]:
            raw = ole.openstream(entry).read()
            if compressed:
                try:
                    raw = zlib.decompress(raw, -15)
                except Exception:
                    pass
            texts.append(_section_text(raw))
            if sum(len(t) for t in texts) > 6000:
                break
        return "\n".join(texts)
    finally:
        ole.close()

def extract_text(path, ext):
    try:
        if ext == "pdf":
            return extract_pdf(path)
        if ext == "hwpx":
            return extract_hwpx(path)
        if ext in ("hwp", "HWP"):
            return extract_hwp(path)
        if ext == "txt":
            with open(path, encoding="utf-8", errors="ignore") as f:
                return f.read(6000)
    except Exception as e:
        return f"__ERR__:{type(e).__name__}"
    return ""


def main():
    import signal
    def _timeout(sig, frm): raise TimeoutError()
    signal.signal(signal.SIGALRM, _timeout)
    files = sorted(os.listdir(DATA_DIR))
    files = [f for f in files if not f.startswith(".")]
    total = len(files)
    rows = []
    err = 0
    with open(PROGRESS, "w") as pg:
        for idx, fname in enumerate(files, 1):
            path = os.path.join(DATA_DIR, fname)
            if not os.path.isfile(path):
                continue
            stem, dot, ext = fname.rpartition(".")
            if not dot:
                stem, ext = fname, ""
            meta = parse_stem(stem)
            size = os.path.getsize(path)
            title, dispo = "", ""
            is_part = meta["part"] is not None or ext == "part"
            if not is_part and ext.lower() in ("pdf", "hwpx", "hwp", "txt"):
                # 파일 1개가 파싱에서 멎어도 전체가 멈추지 않도록 30초 상한
                signal.alarm(30)
                try:
                    text = extract_text(path, ext.lower() if ext != "HWP" else "hwp")
                except TimeoutError:
                    text = "__ERR__:Timeout"
                finally:
                    signal.alarm(0)
                if text.startswith("__ERR__"):
                    err += 1
                else:
                    title = extract_title(text)
                    dispo = classify_dispo(text)
            rows.append({
                "기관명": meta["org"],
                "연도": meta["year"],
                "감사분야": meta["field"],
                "순번": meta["seq"],
                "지적제목": title,
                "처분키워드": dispo,
                "파일형식": ext,
                "파일크기(KB)": round(size / 1024, 1),
                "분할파일": "Y" if is_part else "",
                "파일명": fname,
            })
            if idx % 200 == 0 or idx == total:
                pg.write(f"{idx}/{total} 처리 (추출오류 {err})\n"); pg.flush()

    def clean(v):
        if isinstance(v, str):
            return v.encode("utf-8", "ignore").decode("utf-8")
        return v
    rows = [{k: clean(v) for k, v in r.items()} for r in rows]
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"완료: {len(rows)}행 -> {OUT_CSV} (추출오류 {err})")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--sample":
        # 샘플 검증: 각 형식 몇 개씩 추출 미리보기
        files = sorted(os.listdir(DATA_DIR))
        picks = {}
        for f in files:
            ext = f.rpartition(".")[2].lower()
            if ext in ("pdf", "hwpx", "hwp") and len(picks.get(ext, [])) < 2:
                picks.setdefault(ext, []).append(f)
        for ext, fs in picks.items():
            for f in fs:
                t = extract_text(os.path.join(DATA_DIR, f), ext)
                print(f"\n===== [{ext}] {f} =====")
                print(f"제목: {extract_title(t)!r}")
                print(f"처분: {classify_dispo(t)!r}")
                print("본문:", re.sub(r'\s+', ' ', t)[:200])
        sys.exit(0)
    main()
