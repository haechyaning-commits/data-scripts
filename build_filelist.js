// Builds filelist.json for the unified dashboard from git ls-tree of all four
// folders (자체감사파일1~4). Collapses split parts — X_조각(1).ext.part,
// X_조각(2).ext.part — into a single logical file "X.ext" whose download links
// point to the parts. Non-data files (README.md) are skipped.
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const REPO = '/home/user/data';
const FOLDERS = ['자체감사파일1', '자체감사파일2', '자체감사파일3', '자체감사파일4'];
const OUT = path.join(__dirname, 'filelist.json');

const PART_RE = /^(.*)_조각\((\d+)\)\.(.+)\.part$/; // base, idx, ext

const out = { folders: FOLDERS, files: {} };
let totalLogical = 0, totalRaw = 0, totalSplit = 0;
for (const folder of FOLDERS) {
  // core.quotePath=false → raw UTF-8 names (default octal-escapes non-ASCII).
  const raw = execSync(`git -c core.quotePath=false ls-tree -r origin/main --name-only "${folder}"`, { cwd: REPO, maxBuffer: 1 << 30 })
    .toString('utf8').split('\n').filter(Boolean)
    .map((p) => p.slice(folder.length + 1)) // strip "folder/"
    .filter((n) => n && n !== 'README.md' && !n.endsWith('.md'));
  totalRaw += raw.length;

  // Group split parts by their logical original name.
  const splitGroups = new Map(); // "base.ext" -> [partFilename,...]
  const plain = [];
  for (const n of raw) {
    const m = n.match(PART_RE);
    if (m) {
      const logical = `${m[1]}.${m[3]}`;
      if (!splitGroups.has(logical)) splitGroups.set(logical, []);
      splitGroups.get(logical).push(n);
    } else {
      plain.push(n);
    }
  }
  // Each entry: [displayName, [downloadFiles...], isSplit]
  const files = plain.map((n) => [n, [n], 0]);
  for (const [logical, parts] of splitGroups) {
    parts.sort();
    files.push([logical, parts, 1]);
    totalSplit++;
  }
  files.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  out.files[folder] = files;
  totalLogical += files.length;
  console.error(`${folder}: ${raw.length} raw -> ${files.length} logical (${splitGroups.size} split)`);
}
fs.writeFileSync(OUT, JSON.stringify(out));
console.error(`WROTE ${OUT}: ${totalLogical} logical files (${totalRaw} raw, ${totalSplit} split groups)`);
