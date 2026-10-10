"""text -> image with FLUX.2 klein 4B through a running ComfyUI (official distilled text-to-image graph).

usage: python comfy_t2i.py --prompt "..." --out out.png [--seed 1] [--steps 4] [--mp 1.0] [--aspect 1.0] [--free]
Standard library only, so it runs under any Python. ComfyUI must be listening on --server.
"""
import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

# klein 4B is Apache 2.0; the 9B weights are non-commercial and are deliberately not selectable here
MODEL = "4b"
UNET = "flux-2-klein-4b-fp8.safetensors"
CLIP = "qwen_3_4b.safetensors"
VAE = "flux2-vae.safetensors"


def build_graph(prompt, seed, steps, width, height):
    return {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "flux2", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "latent": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "sigmas": {"class_type": "Flux2Scheduler", "inputs": {"steps": steps, "width": width, "height": height}},
        "text": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": prompt}},
        "zero": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["text", 0]}},
        "guider": {"class_type": "CFGGuider", "inputs": {"model": ["unet", 0], "positive": ["text", 0], "negative": ["zero", 0], "cfg": 1.0}},
        "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "sampler": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "sample": {"class_type": "SamplerCustomAdvanced",
                   "inputs": {"noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["sampler", 0],
                              "sigmas": ["sigmas", 0], "latent_image": ["latent", 0]}},
        "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["decode", 0], "filename_prefix": "klein_t2i"}},
    }


def http_json(url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req) as r:
        body = r.read()
    return json.loads(body) if body else {}


def generate(server, prompt, out, seed=1, steps=4, megapixels=1.0, aspect=1.0, timeout=1800):
    """aspect is width / height; both sides are rounded to a multiple of 16."""
    t0 = time.time()
    height = int(round((megapixels * 1e6 / aspect) ** 0.5 / 16)) * 16
    width = int(round(height * aspect / 16)) * 16
    graph = build_graph(prompt, seed, steps, width, height)
    try:
        pid = http_json(f"{server}/prompt", {"prompt": graph, "client_id": uuid.uuid4().hex})["prompt_id"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ComfyUI rejected the workflow: {e.read().decode()[:2000]}")
    while True:
        hist = http_json(f"{server}/history/{pid}").get(pid)
        if hist and hist.get("status", {}).get("completed"):
            break
        if hist and hist.get("status", {}).get("status_str") == "error":
            raise RuntimeError(f"ComfyUI execution error: {json.dumps(hist['status'])[:2000]}")
        if time.time() - t0 > timeout:
            raise TimeoutError("ComfyUI did not finish in time")
        time.sleep(0.5)
    img = hist["outputs"]["save"]["images"][0]
    q = urllib.parse.urlencode({"filename": img["filename"], "subfolder": img["subfolder"], "type": img["type"]})
    with urllib.request.urlopen(f"{server}/view?{q}") as r, open(out, "wb") as f:
        f.write(r.read())
    return time.time() - t0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8188")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--steps", type=int, default=4)
    ap.add_argument("--mp", type=float, default=1.0, help="output size in megapixels")
    ap.add_argument("--aspect", type=float, default=1.0, help="width / height")
    ap.add_argument("--free", action="store_true", help="unload models from VRAM afterwards (do this before running TRELLIS)")
    a = ap.parse_args()
    secs = generate(a.server, a.prompt, a.out, a.seed, a.steps, a.mp, a.aspect)
    print(f"{secs:.1f}s -> {a.out}")
    if a.free:
        http_json(f"{a.server}/free", {"unload_models": True, "free_memory": True})
