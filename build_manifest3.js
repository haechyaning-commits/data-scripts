// Phase 1 for the 2016-01-01 ~ 2020-12-31 collection (output: 자체감사파일3).
// Enumerates every report row and resolves each 조치사항 attachment's metadata
// (fileId/fileSn/fileName/fileSize) WITHOUT downloading bodies, so that final
// file names — including (1),(2)... numbering — can be assigned once, up front.
//
// Fresh state (does NOT reuse the 2021~2025 pass's checkpoint/manifest):
//   manifest_2016_2020_checkpoint.json  — resume state (page + name groups)
//   manifest_2016_2020.json             — final download manifest
// The base-name registry starts EMPTY, so (1),(2)... numbering is decided only
// within this 2016~2020 set (i.e. within 자체감사파일3/). 자체감사파일2/ is never
// consulted; years differ so base names can't collide anyway.
process.on('uncaughtException', (e) => { console.error('[ignored stray socket error]', e.message); });

const { proxyRequest } = require('./lib.js');
const fs = require('fs');
const path = require('path');

const YMD_BGNG = '20160101';
const YMD_END = '20201231';
const PAGE_SIZE = 100;
const META_CONCURRENCY = 40;
const CHECKPOINT_FILE = path.join(__dirname, 'manifest_2016_2020_checkpoint.json');
const MANIFEST_FILE = path.join(__dirname, 'manifest_2016_2020.json');
const LOG_FILE = path.join(__dirname, 'manifest_2016_2020.log');

function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  console.log(line);
  fs.appendFileSync(LOG_FILE, line + '\n');
}

function pLimit(concurrency) {
  let active = 0;
  const queue = [];
  const next = () => {
    if (active >= concurrency || queue.length === 0) return;
    active++;
    const { fn, resolve, reject } = queue.shift();
    fn().then(resolve, reject).finally(() => { active--; next(); });
  };
  return (fn) => new Promise((resolve, reject) => { queue.push({ fn, resolve, reject }); next(); });
}

function sanitize(name) {
  return name.replace(/[\\/:*?"<>|]/g, '_').replace(/\s+/g, ' ').trim().slice(0, 150);
}

// Retries transient failures (incl. HTTP 502) with exponential backoff 1s,2s,3s.
async function getJson(pathStr, retries = 4) {
  for (let i = 0; i < retries; i++) {
    try {
      const r = await proxyRequest('www.pap.go.kr', pathStr, { headers: { Accept: 'application/hal+json' } });
      if (r.status !== 200) throw new Error(`HTTP ${r.status}`);
      return JSON.parse(r.body.toString());
    } catch (e) {
      if (i === retries - 1) throw e;
      await new Promise((res) => setTimeout(res, 1000 * (i + 1))); // 1s, 2s, 3s
    }
  }
}

function loadCheckpoint() {
  if (fs.existsSync(CHECKPOINT_FILE)) return JSON.parse(fs.readFileSync(CHECKPOINT_FILE, 'utf8'));
  return { nextPage: 0, groups: {}, totalReports: 0 };
}

async function buildInstCdLookup() {
  const map = new Map();
  const r = await getJson('/api/instCd?size=3000&palaw1InstClsfCd=30');
  const list = (r._embedded && r._embedded.instCdListDtoes) || [];
  for (const item of list) map.set(item.instCd, item.instNm);
  log(`Loaded instCd lookup: ${map.size} institutions`);
  return map;
}

async function main() {
  const cp = loadCheckpoint();
  log(`Manifest pass (2016-2020) resuming from page ${cp.nextPage}. Reports so far: ${cp.totalReports}`);
  const instCdLookup = await buildInstCdLookup();
  // groups: base -> [{ key, fileId, fileSn, fileName, fileSize, regDt }]
  // key = fileName::fileSize (identical name+size under the same base is treated
  // as the same document and kept once).
  const groups = cp.groups;

  let page = cp.nextPage;
  while (true) {
    const params = new URLSearchParams({
      searchYmdBgng: YMD_BGNG, searchYmdEnd: YMD_END,
      instNm: '', palawInstClsfCd: '30',
      size: String(PAGE_SIZE), index: '0', page: String(page),
    });
    const search = await getJson('/api/fdadPlanRslt?' + params.toString());
    const totalElements = search.page.totalElements;
    const rows = (search._embedded && search._embedded.fdadPlanRsltListDtoes) || [];
    if (rows.length === 0) { log(`Page ${page} empty. Stopping (totalElements=${totalElements}).`); break; }

    // Resolve every row's attachment metadata in parallel (bounded globally),
    // then register into name groups strictly in row order so that (1),(2)...
    // numbering stays deterministic regardless of network completion order.
    const limit = pLimit(META_CONCURRENCY);
    const resolvedRows = await Promise.all(rows.map(async (row) => {
      const details = await Promise.all((row.subList || []).map((sub) => limit(async () => {
        if (!sub.rlsDocAtchFileUuid) return null;
        const fl = await getJson('/api/files/filelist/' + sub.rlsDocAtchFileUuid);
        const d = fl && fl._embedded && fl._embedded.commonFileDetailDtoes && fl._embedded.commonFileDetailDtoes[0];
        if (!d) return null;
        return { fileId: d.fileId, fileSn: d.fileSn, fileName: d.fileName, fileSize: d.fileSize };
      })));
      return { row, details };
    }));
    for (const { row, details } of resolvedRows) {
      const instNm = row.instCdNm || instCdLookup.get(row.instCd) || row.instCd || '기관명미상';
      const base = sanitize(`${instNm}_${row.adYr}년 ${row.adFldNm}`);
      // Within-row dedup: same attachment record (fileId::fileSn) or same
      // fileName+fileSize means the same document shared by multiple 조치사항.
      const rowFiles = [];
      const seen = new Set();
      for (const d of details) {
        if (!d) continue;
        const attKey = `${d.fileId}::${d.fileSn}`;
        const contentKey = `${d.fileName}::${d.fileSize}`;
        if (seen.has(attKey) || seen.has(contentKey)) continue;
        seen.add(attKey); seen.add(contentKey);
        rowFiles.push(d);
      }
      if (!groups[base]) groups[base] = [];
      const group = groups[base];
      let added = 0;
      for (const d of rowFiles) {
        const key = `${d.fileName}::${d.fileSize}`;
        if (group.some((g) => g.key === key)) continue; // duplicate content under same base
        group.push({ key, fileId: d.fileId, fileSn: d.fileSn, fileName: d.fileName, fileSize: d.fileSize, regDt: row.frstRegDt });
        added++;
      }
      cp.totalReports++;
      log(`Meta: ${base} | 조치사항 ${(row.subList || []).length} | distinct files +${added} (group=${group.length})`);
    }

    cp.nextPage = page + 1;
    cp.groups = groups;
    fs.writeFileSync(CHECKPOINT_FILE, JSON.stringify(cp));
    log(`Page ${page} done. Reports: ${cp.totalReports}/${totalElements}`);
    page++;
    if (page * PAGE_SIZE >= totalElements) { log(`All ${totalElements} rows enumerated.`); break; }
  }

  // Finalize names: single-entry groups get no number; multi-entry groups get (1),(2)...
  // in enumeration order (API returns newest-registered first, matching the convention).
  const manifest = [];
  for (const [base, group] of Object.entries(groups)) {
    group.forEach((g, i) => {
      const finalName = group.length > 1 ? `${base}(${i + 1})` : base;
      const ext = (g.fileName.match(/\.[a-zA-Z0-9]+$/) || ['.pdf'])[0];
      manifest.push({ name: finalName, ext, fileId: g.fileId, fileSn: g.fileSn, fileSize: g.fileSize });
    });
  }
  fs.writeFileSync(MANIFEST_FILE, JSON.stringify(manifest, null, 1));
  const totalBytes = manifest.reduce((a, m) => a + (m.fileSize || 0), 0);
  log(`MANIFEST DONE: ${manifest.length} files, ~${(totalBytes / 1e9).toFixed(2)} GB expected`);
}

main().catch((e) => { log('FATAL: ' + e.stack); process.exit(1); });
