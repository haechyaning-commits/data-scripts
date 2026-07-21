#!/bin/bash
# Orchestrates phase 2 of the 2016-01-01 ~ 2020-12-31 collection:
#   download batch -> commit -> push -> free local copies, looping until
#   manifest_2016_2020.json is exhausted.
#
# Layout note: the scripts + manifest + done log live in a SEPARATE repo
# (/home/user/data-scripts), while the downloaded files go into the sparse
# checkout of haechyaning-commits/data at /home/user/data. So only the output
# folder (자체감사파일3) is committed/pushed to the data repo; the manifest and
# done log are runtime artifacts kept locally in data-scripts.
set -u

DATA=/home/user/data
SCRIPTS=/home/user/data-scripts
BRANCH=main
OUT=자체감사파일3
BATCH_BYTES=${BATCH_BYTES:-400000000}   # ~400MB per batch (stays under push size cap)
REMAINING_FILE="$SCRIPTS/remaining_2016_2020.txt"

cd "$DATA" || { echo "cannot cd $DATA" >&2; exit 1; }
# Keep new files in-scope of the sparse checkout so they materialize until we
# explicitly skip-worktree them after a successful push.
git sparse-checkout add "$OUT" 2>/dev/null || true

PREV_REMAINING=-1
STALL=0

while true; do
  node "$SCRIPTS/download_batch3.js" "$DATA/$OUT" "$BATCH_BYTES"
  rc=$?
  if [ $rc -ne 0 ] && [ $rc -ne 2 ]; then
    echo "download_batch3.js exited rc=$rc; aborting" >&2
    exit $rc
  fi
  remaining=$(cat "$REMAINING_FILE" 2>/dev/null || echo -1)

  # Commit ONLY the data folder to the data repo.
  git add "$OUT" 2>/dev/null
  n_staged=$(git diff --cached --numstat | wc -l)
  if [ "$n_staged" -gt 0 ]; then
    n_files=$(git diff --cached --numstat -- "$OUT" | wc -l)
    git commit -q -m "자체감사파일3: 2016~2020 자체감사결과 배치 추가 (${n_files}개 파일, 남은 항목 ${remaining}건)

Co-Authored-By: Claude <noreply@anthropic.com>"

    pushed=0
    for delay in 0 2 4 8 16; do
      [ "$delay" -gt 0 ] && sleep "$delay"
      if git push -u origin "$BRANCH"; then pushed=1; break; fi
      echo "push failed; retrying in next backoff step" >&2
    done
    if [ "$pushed" -ne 1 ]; then
      echo "git push failed after retries; aborting (files remain committed locally)" >&2
      exit 3
    fi

    # Free disk: keep this batch's blobs only in .git, drop worktree copies.
    git ls-files -z "$OUT" | xargs -0 -r -n 500 git update-index --skip-worktree
    git ls-files -z "$OUT" | while IFS= read -r -d '' f; do [ -f "$f" ] && rm -f "$f"; done
  fi

  if [ "$remaining" = "0" ]; then
    echo "ALL DONE"
    break
  fi
  if [ "$remaining" = "$PREV_REMAINING" ]; then
    STALL=$((STALL + 1))
    if [ "$STALL" -ge 3 ]; then
      echo "No progress across 3 consecutive batches (remaining=$remaining); aborting" >&2
      exit 4
    fi
  else
    STALL=0
  fi
  PREV_REMAINING=$remaining
done
