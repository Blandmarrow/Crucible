#!/usr/bin/env bash
# Plan step 3: trust check, then every extraction, sequential on the GPU. Read-only on the DB.
set -u
R="${R:?set R to an output folder}"
cd /b/Crucible-dinov3
set -a; . <(grep '^HF_TOKEN=' /b/Crucible/.env | tr -d '\r'); set +a
export HF_HUB_DISABLE_SYMLINKS_WARNING=1 PYTHONIOENCODING=utf-8
PY=/b/Crucible/venv/Scripts/python.exe
DB=B:/Crucible/dataset_manager.db
B3=facebook/dinov3-vitb16-pretrain-lvd1689m
L3=facebook/dinov3-vitl16-pretrain-lvd1689m

run() {  # name, then the dino_embed_offline args
  local name=$1; shift
  local t0=$(date +%s)
  $PY -m backend.scripts.dino_embed_offline --db $DB --dataset test2 "$@" --out "$R/$name.npz" \
    2>&1 | tr '\r' '\n' > "$R/$name.log"
  local rc=${PIPESTATUS[0]}
  echo "$name rc=$rc $(( $(date +%s) - t0 ))s · $(grep -E 'verify:|on cuda|on cpu' "$R/$name.log" | tr '\n' ' ') $(grep -E 'images \(' "$R/$name.log" | tail -1)"
}

run clip    --model clip --verify
run v2      --model facebook/dinov2-base --verify
run v3b224  --model $B3 --size 224
run v3b512  --model $B3 --size 512 --batch 4
run v3l224  --model $L3 --size 224
run v3l512  --model $L3 --size 512 --batch 4
echo ALL-DONE
