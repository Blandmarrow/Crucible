#!/usr/bin/env bash
# Plan step 5: crossed probes. pools for every rule candidate (rule b), synthetic as context.
set -u
R="${R:?set R to an output folder}"
P=B:/Crucible-test3-pools
cd /b/Crucible-dinov3
set -a; . <(grep '^HF_TOKEN=' /b/Crucible/.env | tr -d '\r'); set +a
export HF_HUB_DISABLE_SYMLINKS_WARNING=1 PYTHONIOENCODING=utf-8
PY=/b/Crucible/venv/Scripts/python.exe
DB=B:/Crucible/dataset_manager.db
B3=facebook/dinov3-vitb16-pretrain-lvd1689m
L3=facebook/dinov3-vitl16-pretrain-lvd1689m
POOLS=$(for a in bauer bilibin clarke dulac goble nielsen rackham; do printf -- "--pool %s=@%s/%s " $a $P $a; done)

probe() {  # name, model, size ("" = processor default), subcommand args...
  local name=$1 model=$2 size=$3; shift 3
  local t0=$(date +%s)
  $PY -m backend.scripts.style_crossed_probe --db $DB --model $model ${size:+--size $size} --batch 8 \
    "$@" 2>&1 | tr '\r' '\n' | grep -vE '^\s+[0-9]+/[0-9]+ images \(' > "$R/$name.log"
  echo "$name rc=${PIPESTATUS[0]} $(( $(date +%s) - t0 ))s"
}

for spec in "v2 facebook/dinov2-base " "v3b224 $B3 224" "v3b512 $B3 512" "v3l224 $L3 224" "v3l512 $L3 512"; do
  set -- $spec
  probe "pools_$1" "$2" "${3:-}" pools $POOLS \
    --out "$R/pools_$1.npz"
done
for spec in "v2 facebook/dinov2-base " "v3b224 $B3 224" "v3l224 $L3 224"; do
  set -- $spec
  probe "synth_$1" "$2" "${3:-}" synthetic --bases test3:30 --work "$R/synth_work" \
    --out "$R/synth_$1.npz"
done
echo ALL-DONE
