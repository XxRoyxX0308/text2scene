"""Step 1: prompt -> clean object images (FLUX.2 klein 4B via ComfyUI, text only).

Two modes (ComfyUI must be running, see comfy_start.sh):

  iterative (preferred, one image per call, the reviewer rewrites the prompt between calls)
    python3 01_generate.py <project_dir> --next <object_id> [--prompt "new prompt"] [--review-prev "verdict on the last image"]
      - writes work/objects/<id>/cand_<n>.png, n = number of candidates so far
      - prompt: --prompt if given, else the prompt of the previous candidate, else scene.json's prompt
      - appends {cand, seed, prompt, seconds} to work/objects/<id>/prompts.json; --review-prev is stored on the
        previous record so the reason for every prompt change is kept

  batch (same prompt, different seeds)
    python3 01_generate.py <project_dir> [--redo] [object_id ...]

The object's "suffix" (default: generation.suffix from scene.json) is appended to every prompt, and its "aspect"
(width / height, default 1) sets the image shape. Call comfy_stop.sh when finished to free the GPU.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from comfy_t2i import MODEL, generate

SERVER = "http://127.0.0.1:8188"


def take(args, flag):
    if flag not in args:
        return None
    i = args.index(flag)
    value = args[i + 1]
    del args[i:i + 2]
    return value


args = sys.argv[1:]
PROJECT = args.pop(0)
next_id = take(args, "--next")
new_prompt = take(args, "--prompt")
review_prev = take(args, "--review-prev")
redo = "--redo" in args
only = {a for a in args if not a.startswith("--")}

scene = json.load(open(os.path.join(PROJECT, "scene.json"), encoding="utf-8"))
gen = scene["generation"]
objects = {o["id"]: o for o in scene["objects"]}
log_path = os.path.join(PROJECT, "work", "generation_log.jsonl")


def log(record):
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


if next_id:
    obj = objects[next_id]
    d = os.path.join(PROJECT, "work", "objects", next_id)
    hist_path = os.path.join(d, "prompts.json")
    history = json.load(open(hist_path, encoding="utf-8")) if os.path.exists(hist_path) else []
    if review_prev and history:
        history[-1]["review"] = review_prev
    n = len(history)
    while os.path.exists(os.path.join(d, f"cand_{n}.png")):   # candidates made by batch mode have no history record
        n += 1
    prompt = new_prompt or (history[-1]["prompt"] if history else obj["prompt"])
    seed = obj.get("seed_base", 1000) + n
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, f"cand_{n}.png")
    secs = generate(SERVER, prompt + " " + obj.get("suffix", gen["suffix"]), out, seed=seed,
                    megapixels=gen["megapixels"], aspect=obj.get("aspect", 1.0))
    history.append({"cand": n, "seed": seed, "model": MODEL, "seconds": round(secs, 1), "prompt": prompt,
                    "prompt_changed": bool(new_prompt)})
    json.dump(history, open(hist_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    log({"id": next_id, "cand": n, "seed": seed, "model": MODEL, "seconds": round(secs, 1), "time": time.strftime("%H:%M:%S")})
    print(f"{next_id} cand_{n} seed={seed} {secs:.1f}s -> {out}")
    sys.exit(0)

for obj in scene["objects"]:
    if only and obj["id"] not in only:
        continue
    d = os.path.join(PROJECT, "work", "objects", obj["id"])
    prompt = obj["prompt"] + " " + obj.get("suffix", gen["suffix"])
    os.makedirs(d, exist_ok=True)
    for i in range(gen["candidates"]):
        out = os.path.join(d, f"cand_{i}.png")
        if os.path.exists(out) and not redo:
            continue
        seed = obj.get("seed_base", 1000) + i
        secs = generate(SERVER, prompt, out, seed=seed, megapixels=gen["megapixels"], aspect=obj.get("aspect", 1.0))
        print(f"{obj['id']} cand_{i} seed={seed} {secs:.1f}s", flush=True)
        log({"id": obj["id"], "cand": i, "seed": seed, "model": MODEL, "seconds": round(secs, 1), "time": time.strftime("%H:%M:%S")})
