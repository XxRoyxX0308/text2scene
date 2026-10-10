#!/bin/bash
# Start ComfyUI (if not already running) and wait until its API answers. Cold start from the HDD takes a few minutes.
# usage (inside WSL): bash comfy_start.sh
source ~/miniconda3/etc/profile.d/conda.sh
conda activate comfyui
if ! curl -s -m 2 http://127.0.0.1:8188/system_stats > /dev/null; then
  cd ~/ComfyUI
  nohup python main.py --listen 127.0.0.1 --port 8188 > ~/comfyui.log 2>&1 &
fi
for i in $(seq 1 200); do
  curl -s -m 2 http://127.0.0.1:8188/system_stats > /dev/null && { echo "COMFY_READY"; exit 0; }
  sleep 3
done
echo "COMFY_FAILED (see ~/comfyui.log)"; tail -5 ~/comfyui.log; exit 1
