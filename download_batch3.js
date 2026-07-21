// Phase 2 for the 2016-01-01 ~ 2020-12-31 collection (output: 자체감사파일3).
// Downloads the next slice of manifest_2016_2020.json into the output dir,
// stopping once the batch byte budget is reached so the orchestrator can
// commit/push/free-disk between batches. Progress is tracked in
// done_2016_2020.log (one manifest name per line).
//
// Notes vs the 2021~2023 downloader:
//   - reads manifest_2016_2020.json / done_2016_2020.log (fresh, isolated state)
//   - transient failures (incl. HTTP 502) retried with 1s,2s,3s backoff
//   - download concurrency 20; files >90MB split into _조각(n).part (README rule)
process.on('uncaughtException', (e) => { console.error('[ignored stray socket error]', e.message); });

const { proxyRequest } = require('./lib.js');
const fs = require('fs');
const path = require('path');

const OUT_DIR = process.argv[2] || '/home/user/data/자체감사파일3';
const BATCH_BYTES = Number(process.argv[3] || 400000000); // ~400MB per batch
const MANIFEST_FILE = path.join(__dirname, 'manifest_2016_2020.json');
const DONE_FILE = path.join(__dirname, 'done_2016_2020.log');
const REMAINING_FILE = path.join(__dirname, 'remaining_2016_2020.txt');
const LOG_FILE = path.join(__dirname, 'progress_2016_2020.log');
const UNAVAIL_FILE = path.join(__dirname, 'unavailable_2016_2020.log');
const DOWNLOAD_CONCURRENCY = 20;
const MAX_CHUNK_BYTES = 90 * 1024 * 1024;

function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  console.log(line);
  fs.appendFileSync(LOG_FILE, line + '\n');
}

function saveMaybeSplit(finalName, ext, buf) {
  if (buf.length <= MAX_CHUNK_BYTES) {
    fs.writeFileSync(path.join(OUT_DIR, `${finalName}${ext}`), buf);
    return `${finalName}${ext}`;
  }
  const parts = Math.ceil(buf.length / MAX_CHUNK_BYTES);
  const names = [];
  for (let i = 0; i < parts; i++) {
    const chunk = buf.subarray(i * MAX_CHUNK_BYTES, (i + 1) * MAX_CHUNK_BYTES);
    const name = `${finalName}_조각(${i + 1})${ext}.part`;
    fs.writeFileSync(path.join(OUT_DIR, name), chunk);
    names.push(name);
  }
  return names.join(', ');
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

// Downloads one file. HTTP 204 = permanently unavailable (record + skip).
// Transient errors (incl. HTTP 502) retried with exponential backoff 1s,2s,3s.
async function postDownload(fileId, fileSn, retries = 4) {
  const body = JSON.stringify({ fileId, fileSn });
  for (let i = 0; i < retries; i++) {
    try {
      const r = await proxyRequest('www.pap.go.kr', '/api/files/download', {
        method: 'POST',
        headers: { Accept: 'application/hal+json', 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
        body,
        timeoutMs: 180000,
      });
      if (r.status === 204) return null; // withheld/removed — never retry
      if (r.status !== 200) throw new Error(`HTTP ${r.status}`);
      return r.body;
    } catch (e) {
      if (i === retries - 1) throw e;
      await new Promise((res) => setTimeout(res, 1000 * (i + 1))); // 1s, 2s, 3s
    }
  }
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const manifest = JSON.parse(fs.readFileSync(MANIFEST_FILE, 'utf8'));
  const done = new Set(fs.existsSync(DONE_FILE) ? fs.readFileSync(DONE_FILE, 'utf8').split('\n').filter(Boolean) : []);
  const todo = manifest.filter((m) => !done.has(m.name));
  log(`Batch start: ${todo.length} files remaining of ${manifest.length}, budget ${(BATCH_BYTES / 1e9).toFixed(2)} GB`);

  // Select this batch by expected size, then download with bounded concurrency.
  const batch = [];
  let planned = 0;
  for (const m of todo) {
    batch.push(m);
    planned += m.fileSize || 5 * 1024 * 1024;
    if (planned >= BATCH_BYTES) break;
  }

  const limit = pLimit(DOWNLOAD_CONCURRENCY);
  let failures = 0;
  let unavailable = 0;
  await Promise.all(batch.map((m) => limit(async () => {
    try {
      const buf = await postDownload(m.fileId, m.fileSn);
      if (buf === null) {
        unavailable++;
        fs.appendFileSync(DONE_FILE, m.name + '\n');
        fs.appendFileSync(UNAVAIL_FILE, `${m.name}${m.ext}\t${m.fileId}\t${m.fileSn}\n`);
        log(`UNAVAILABLE (HTTP 204, skipped): ${m.name}${m.ext}`);
        return;
      }
      const savedAs = saveMaybeSplit(m.name, m.ext, buf);
      fs.appendFileSync(DONE_FILE, m.name + '\n');
      log(`SAVED: ${savedAs} (${buf.length}B)`);
    } catch (e) {
      failures++;
      log(`FAIL: ${m.name} :: ${e.message}`);
    }
  })));

  const remaining = todo.length - batch.length + failures;
  fs.writeFileSync(REMAINING_FILE, String(remaining));
  log(`Batch done: ${batch.length - failures} saved, ${unavailable} unavailable, ${failures} failed, ${remaining} remaining.`);
  // Failures stay out of done_2016_2020.log and are retried next batch.
  if (failures > 0 && batch.length === failures) process.exit(2); // no progress at all
}

main().catch((e) => { log('FATAL: ' + e.stack); process.exit(1); });
