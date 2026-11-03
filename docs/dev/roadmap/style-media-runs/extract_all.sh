#!/usr/bin/env bash
# Harness checks, then every extraction, sequential on the GPU. Read-only on the DB.
# test6-test8 and flickr6 live in one throwaway app DB (imported and embedded by `main` on
# :8299). Set R to an output folder, DB to that throwaway DB, and SETS to the sets to run.
set -u
R="${R:?set R to an output folder}"
DB="${DB:?set DB to the throwaway DB}"
SETS="${SETS:-test6 test7 test8 flickr6}"
cd /b/Crucible-style-models
export HF_HUB_DISABLE_SYMLINKS_WARNING=1 PYTHONIOENCODING=utf-8
PY=/b/Crucible/venv/Scripts/python.exe

run() {  # name, script module, then its args
  local name=$1 mod=$2; shift 2
  local t0=$(date +%s)
  $PY -m $mod "$@" 2>&1 | tr '\r' '\n' | grep -vE '^\s+[0-9]+/[0-9]+ images \(' > "$R/$name.log"
  local rc=${PIPESTATUS[0]}
  echo "$name rc=$rc $(( $(date +%s) - t0 ))s | $(grep -E 'verify:|self-check|RoPE' "$R/$name.log" | tr '\n' ' ')"
}

for set in $SETS; do
  run ${set}_clip backend.scripts.dino_embed_offline --db $DB --dataset $set --model clip --verify --out "$R/${set}_clip.npz"
  run ${set}_v2   backend.scripts.dino_embed_offline --db $DB --dataset $set --model facebook/dinov2-base --verify --out "$R/${set}_v2.npz"
  run ${set}_kalo backend.scripts.style_research.style_embed_offline --db $DB --dataset $set --model kalo \
    --out "$R/${set}_kalo.npz" --feat-out "$R/${set}_kalofeat.npz"
  run ${set}_csd  backend.scripts.style_research.style_embed_offline --db $DB --dataset $set --model csd --out "$R/${set}_csd.npz"
  run ${set}_pe   backend.scripts.style_research.style_embed_offline --db $DB --dataset $set --model pe --out "$R/${set}_pe.npz"
  run ${set}_clipq backend.scripts.style_research.style_embed_offline --db $DB --dataset $set --model clipq --out "$R/${set}_clipq.npz"
done
echo ALL-DONE
