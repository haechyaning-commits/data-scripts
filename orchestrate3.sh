#!/bin/bash
# Orchestrates phase 2 of the 2016-01-01 ~ 2020-12-31 collection:
#   download batch -> commit -> push -> free local copies, looping until
#   manifest_2016_2020.json is exhausted.
#
# Layout note: the scripts + manifest + done log live in a SEPARATE repo
# (/home/user/data-scripts), while the downloaded files go into the sparse
# checkout of haechyaning-commits/data at /home/user/data. Only the output
# folder (자체감사파일3) is committed/pushed to the data repo.
#
# IMPORTANT (sparse-checkout safety): we `git add` ONLY the files this batch
# saved (listed in batch_files.txt), never `git add 자체감사파일3`. Adding the
# whole folder after prior batches' worktree copies were removed would stage
# those as deletions, dropping them from the tree. After a successful push we
# set skip-worktree on this batch's files and delete their worktree copies to
# free disk; because later batches never re-add them, they persist in the tree.
set -u

DATA=/home/user/data
SCRIPTS=/home/user/data-scripts
BRANCH=main
OUT=자체감사파일3
BATCH_BYTES=${BATCH_BYTES:-400000000}   # ~400MB per batch (stays under push size cap)
REMAINING_FILE="$SCRIPTS/remaining_2016_2020.txt"
BATCH_LIST="$SCRIPTS/batch_files.txt"

cd "$DATA" || { echo "cannot cd $DATA" >&2; exit 1; }
git config gc.auto 0 2>/dev/null || true
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

  # Stage ONLY this batch's saved files (repo-relative paths, one per line).
  if [ -s "$BATCH_LIST" ]; then
    git add --pathspec-from-file="$BATCH_LIST" -- 2>/dev/null
  fi
  n_staged=$(git diff --cached --numstat | wc -l)
  if [ "$n_staged" -gt 0 ]; then
    git commit -q -m "자체감사파일3: 2016~2020 자체감사결과 배치 추가 (${n_staged}개 파일, 남은 항목 ${remaining}건)

Co-Authored-By: Claude <noreply@anthropic.com>"

    pushed=0
    for delay in 0 2 4 8 16 30; do
      [ "$delay" -gt 0 ] && sleep "$delay"
      if git push -u origin "$BRANCH"; then pushed=1; break; fi
      echo "push failed; retrying in next backoff step" >&2
    done
    if [ "$pushed" -ne 1 ]; then
      echo "git push failed after retries; aborting (files remain committed locally)" >&2
      exit 3
    fi

    # Free disk: keep this batch's blobs only in .git, drop worktree copies.
    # skip-worktree + rm ONLY this batch's files (not the whole folder).
    git update-index -z --skip-worktree --stdin < <(tr '\n' '\0' < "$BATCH_LIST")
    while IFS= read -r f; do [ -n "$f" ] && [ -f "$f" ] && rm -f "$f"; done < "$BATCH_LIST"
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
