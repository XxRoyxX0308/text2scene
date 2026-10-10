#!/bin/bash
# Runs 03_trellis_batch.py until every object has a model.glb, restarting a fresh process after each failure.
# usage: trellis_loop.sh <project_dir> [max_attempts]
PROJECT="$1"; MAX="${2:-8}"
source ~/miniconda3/etc/profile.d/conda.sh; conda activate trellis2; cd ~/TRELLIS.2
for attempt in $(seq 1 "$MAX"); do
  echo "[attempt $attempt]"
  python "$PROJECT/pipeline/03_trellis_batch.py" "$PROJECT" 2>&1 && { echo "[all done]"; exit 0; }
done
echo "[gave up after $MAX attempts]"; exit 1
