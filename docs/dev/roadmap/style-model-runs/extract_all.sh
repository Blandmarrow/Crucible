#!/usr/bin/env bash
# Harness checks, then every extraction, sequential on the GPU. Read-only on both DBs.
# test4 lives in a throwaway app DB (imported and embedded by `main` on :8299); test3 in the
# dev DB. Set R to an output folder and T4DB to the throwaway DB first.
set -u
R="${R:?set R to an output folder}"
T4DB="${T4DB:?set T4DB to the throwaway DB holding test4}"
cd /b/Crucible-style-models
export HF_HUB_DISABLE_SYMLINKS_WARNING=1 PYTHONIOENCODING=utf-8
PY=/b/Crucible/venv/Scripts/python.exe
DEV=B:/Crucible/dataset_manager.db

run() {  # name, script module, then its args
  local name=$1 mod=$2; shift 2
  local t0=$(date +%s)
  $PY -m $mod "$@" 2>&1 | tr '\r' '\n' | grep -vE '^\s+[0-9]+/[0-9]+ images \(' > "$R/$name.log"
  local rc=${PIPESTATUS[0]}
  echo "$name rc=$rc $(( $(date +%s) - t0 ))s · $(grep -E 'verify:|self-check|RoPE' "$R/$name.log" | tr '\n' ' ')"
}

for set in test4 test3; do
  db=$DEV; [ $set = test4 ] && db=$T4DB
  run ${set}_clip backend.scripts.dino_embed_offline --db $db --dataset $set --model clip --verify --out "$R/${set}_clip.npz"
  run ${set}_v2   backend.scripts.dino_embed_offline --db $db --dataset $set --model facebook/dinov2-base --verify --out "$R/${set}_v2.npz"
  run ${set}_kalo backend.scripts.style_research.style_embed_offline --db $db --dataset $set --model kalo \
    --out "$R/${set}_kalo.npz" --feat-out "$R/${set}_kalofeat.npz"
  run ${set}_csd  backend.scripts.style_research.style_embed_offline --db $db --dataset $set --model csd --out "$R/${set}_csd.npz"
  run ${set}_pe   backend.scripts.style_research.style_embed_offline --db $db --dataset $set --model pe --out "$R/${set}_pe.npz"
  # Added after the review: the app's CLIP checkpoint built with the QuickGELU it expects.
  run ${set}_clipq backend.scripts.style_research.style_embed_offline --db $db --dataset $set --model clipq --out "$R/${set}_clipq.npz"
done
echo ALL-DONE
