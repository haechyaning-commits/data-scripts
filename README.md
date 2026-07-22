# data-scripts

[**haechyaning-commits/data**](https://github.com/haechyaning-commits/data) 저장소(공공감사포털
자체감사결과 원문 데이터)를 만들 때 쓴 **수집·가공 스크립트만** 모아둔 곳입니다.
실제 데이터(문서 원문·CSV·대시보드 HTML)는 여기 없고 `data` 저장소에 있습니다.

- **데이터 원 출처**: 공공감사포털([pap.go.kr](https://pap.go.kr)) — 각 공공기관이 공개한 자체감사결과 및 첨부문서
- **수집 방식**: 포털 REST API(`/api/fdadPlanRslt`, `/api/files/filelist`, `/api/files/download`)를 직접 호출해 원문 그대로 저장. 임의 생성·가공·요약 없음.
- **수집 범위**: 기관분류 = 공공기관(`palawInstClsfCd=30`), 기간 2016-01-01 ~ 2026-07 (자체감사파일1~4)

> **📘 전체 설명서 & 기술 아키텍처는 [`기술문서.md`](기술문서.md)** 를 참고하세요 — 데이터 흐름도, 수집 자동화, 대시보드/CSV 생성, 트러블슈팅 이력, 재현 가이드까지 정리돼 있습니다.
> 상세 실행/재개 가이드는 [`수집스크립트_사용법.md`](수집스크립트_사용법.md) 참고.

---

## 1. 실행 전제 — 데이터는 `data` 저장소로 커밋됩니다

수집 스크립트는 이 `data-scripts` 저장소가 아니라 **`haechyaning-commits/data` 저장소로
파일을 커밋·푸시**합니다. 스크립트 안에 원격 URL을 넣어두지 않았고, 아래 경로/원격 설정에
의존하므로 실행 전에 반드시 맞춰야 합니다.

- **작업 디렉터리(하드코딩)**: 다운로드·커밋·푸시 대상은 모두 `/home/user/data` 입니다.
  (`orchestrate2.sh`, `flush_chunks.sh`는 `cd /home/user/data`, `apply_gap.js`는 `REPO = '/home/user/data'`)
  → 이 경로에 `data` 저장소가 체크아웃되어 있어야 합니다.
- **원격(`origin`)**: 스크립트는 `git push -u origin "$BRANCH"`만 하고 `git remote add`는 하지
  않습니다. 따라서 `/home/user/data`의 `origin`이 반드시 `haechyaning-commits/data`를
  가리켜야 합니다.
- **브랜치(하드코딩)**: `orchestrate2.sh` / `flush_chunks.sh`의 `BRANCH`는 `main`으로
  설정돼 있습니다. **다른 브랜치로 밀려면 스크립트 상단의 `BRANCH` 값을 바꾸세요.**

준비 예시:

```bash
# data 저장소를 스크립트가 기대하는 경로에 두고 원격 확인
git clone https://github.com/haechyaning-commits/data /home/user/data
git -C /home/user/data remote -v          # origin 이 haechyaning-commits/data 인지 확인
# 필요하면 스크립트의 BRANCH 값을 원하는 브랜치로 수정
```

---

## 2. 필요한 환경변수 / 파라미터

### 환경변수

| 변수 | 쓰는 곳 | 설명 |
| --- | --- | --- |
| `HTTPS_PROXY` (또는 `https_proxy`) | 모든 Node 수집 스크립트(`lib.js`) | 포털 API 호출용 프록시. `lib.js`가 자동으로 읽어 CONNECT 터널을 만듭니다. 세션마다 포트가 바뀔 수 있어 **하드코딩하지 말고 환경변수로** 넘기세요. 미설정 시 코드의 기본 포트로 폴백하지만 세션과 다르면 실패합니다. |
| `BATCH_BYTES` | `orchestrate2.sh` | 배치당 다운로드 바이트 예산(기본 `400000000` = 400MB). 푸시 크기 상한(HTTP 413) 회피용. |
| `CHUNK_BYTES` | `flush_chunks.sh` | 분할 푸시 청크 크기(기본 400MB). |

### 포털 API 파라미터 (`/api/fdadPlanRslt`)

수집 스크립트가 목록 조회 시 넘기는 쿼리 파라미터입니다.

| 파라미터 | 값 | 의미 |
| --- | --- | --- |
| `searchYmdBgng` | `YYYYMMDD` | **수집 기간 시작일** |
| `searchYmdEnd` | `YYYYMMDD` | **수집 기간 종료일** |
| `palawInstClsfCd` | `30` | **기관분류 코드** — `30` = 공공기관 (전 스크립트 공통) |
| `instNm` | `''`(빈값) | 기관명 필터(미사용, 전체) |
| `size` / `page` / `index` | 정수 | 페이지 크기·페이지 번호 |

기간은 스크립트별로 다르게 지정됩니다:

- `full_scrape.js` — `20250707 ~ 20260707` 로 **하드코딩**(argv로 `OUT_DIR`만 받음).
- `incremental_scrape.js` — CLI 인자로 조절: `node incremental_scrape.js [OUT_DIR] [SINCE] [UNTIL] [--full]`
  (`SINCE` 기본 `20250707`, `UNTIL` 기본 오늘, `--full`이면 기간 전체 재점검).
- `collect_catalog.js` — 스크립트 상단 `RANGES` 배열로 기간을 정의(`20210101~20250706`, `20250707~20260707`).
- `build_manifest.js` — `20210101 ~ 20231230`, `build_manifest_gap.js` — `20231231 ~ 20250706`.

기간을 바꾸려면 위 값(하드코딩 스크립트는 상단 상수, `incremental_scrape.js`는 CLI 인자)을 수정하세요.

---

## 3. 스크립트 목록

### 수집 (포털 → 파일)

| 파일 | 설명 |
| --- | --- |
| `lib.js` | 프록시 CONNECT 터널 HTTPS 요청 헬퍼. 모든 Node 스크립트가 공유. `HTTPS_PROXY` 사용. |
| `full_scrape.js` | 자체감사결과 전량 수집(`checkpoint.json`으로 중단·재개). `node full_scrape.js [OUT_DIR]` |
| `incremental_scrape.js` | 새로 등록된 보고서만 append-only 최신화. `node incremental_scrape.js [OUT_DIR] [SINCE] [UNTIL] [--full]` |
| `collect_catalog.js` | 보고서 목록(메타데이터) 카탈로그 CSV 수집. |

### 대용량 배치 수집 (2021~2023 phase 2)

| 파일 | 설명 |
| --- | --- |
| `build_manifest.js` | 다운로드 대상 매니페스트(`manifest2.json`) 사전 생성 — 파일명·번호를 미리 확정. |
| `build_manifest_gap.js` | 간극기간(2023-12-31~2025-07-06) 신규 항목 매니페스트(`manifest3.json`)·리네임(`renames3.json`) 생성. |
| `apply_gap.js` | `renames3.json`/`manifest3.json`을 기존 커밋·매니페스트에 반영(git index 조작). |
| `download_batch.js` | 매니페스트에서 바이트 예산만큼 배치 다운로드(`done2.log`로 진행 추적). |
| `orchestrate2.sh` | 다운로드→커밋→푸시→로컬 정리 루프. **`/home/user/data`로 푸시.** |
| `flush_chunks.sh` | 이미 받아둔 파일을 413 상한 밑으로 분할 커밋·푸시. **`/home/user/data`로 푸시.** |

### 가공 / 리포트 (데이터 → CSV·HTML)

| 파일 | 설명 |
| --- | --- |
| `build_index.py` | 파일명·본문(pdf/hwp/hwpx)에서 마스터 인덱스 CSV 생성. |
| `clean_titles.py` | 마스터 인덱스의 '지적제목' 문자열 후처리 정리. |
| `build_dashboard.py` | 마스터 인덱스 임베드 자기완결형 대시보드 HTML. |
| `build_report.py` | 환경·에너지 부문 벤치마크 리포트 HTML. |
| `build_unified.py` | 마스터 인덱스 + 카탈로그 결합 통합 대시보드 HTML. |
| `build_filelevel.py` | 파일 단위 목록 산출(일부 경로가 과거 세션 스크래치패드에 하드코딩됨 — 재사용 시 경로 수정 필요). |
| `page_report.py` | `progress.log` 기반 수집 진행 상황 요약 출력(경로 하드코딩). |

---

## 4. 커밋되지 않는 런타임 산출물

아래 파일은 스크립트 실행 중 생성/갱신되는 산출물이라 이 저장소에는 커밋하지 않습니다
(`.gitignore` 처리). 필요하면 스크립트를 돌려 다시 만들어집니다.

- 진행 상태: `checkpoint.json`, `checkpoint.json.bak`
- 매니페스트: `manifest2.json`, `manifest3.json`, `renames3.json`, `remaining2.txt`, `catalog.csv`
- 로그: `progress.log`, `incremental.log`, `done2.log`, `nohup.out`, `oversized.log`, `unavailable*.log`
- 실제 데이터 폴더: `자체감사결과/`, `자체감사파일2/` (→ `data` 저장소로 감)
