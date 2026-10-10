"""Step 3: RGBA cut-outs -> textured GLB with TRELLIS.2 (pipeline loaded once, finished objects are skipped).

usage (inside the trellis2 env): python 03_trellis_batch.py <project_dir> [object_id ...]
Exits with code 3 on the first failure; rerun it (finished objects are skipped), e.g. via trellis_loop.sh.

trellis.pipeline_type defaults to "512"; "1024_cascade" gives finer geometry but can run out of VRAM on a 12 GB card
(solid blocks and foliage did).
The GLB is decimated to trellis.faces (default 30000) before the texture is baked, so the scene stays light.
"""
import os
os.environ['OPENCV_IO_ENABLE_OPENEXR'] = '1'
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
import gc
import json
import sys
import time
import traceback

sys.path.insert(0, os.path.expanduser("~/TRELLIS.2"))  # this script lives outside the repo

import torch
from PIL import Image
from trellis2.pipelines import Trellis2ImageTo3DPipeline
import o_voxel

PROJECT = sys.argv[1]
ONLY = set(sys.argv[2:])
FACES = 30000

scene = json.load(open(os.path.join(PROJECT, "scene.json"), encoding="utf-8"))
todo = []
for obj in scene["objects"]:
    d = os.path.join(PROJECT, "work", "objects", obj["id"])
    if obj.get("status") == "rejected" or obj.get("mode") == "image_slab" or (ONLY and obj["id"] not in ONLY):
        continue  # image_slab objects (text signs) are built in Blender from their 2D image, not by TRELLIS
    if os.path.exists(os.path.join(d, "model.glb")) and not ONLY:
        print(f"[skip] {obj['id']} already has model.glb", flush=True)
        continue
    todo.append((obj, d))
if not todo:
    sys.exit(0)

pipeline = Trellis2ImageTo3DPipeline.from_pretrained("microsoft/TRELLIS.2-4B")
pipeline.cuda()

for obj, d in todo:
    t0 = time.time()
    try:
        image = Image.open(os.path.join(d, "rgba.png"))
        cfg = obj.get("trellis", {})
        mesh = pipeline.run(
            image,
            seed=cfg.get("seed", 42),
            pipeline_type=cfg.get("pipeline_type", "512"),
            max_num_tokens=cfg.get("max_num_tokens", 49152),
            sparse_structure_sampler_params={'steps': 25},
            shape_slat_sampler_params={'steps': 25},
            tex_slat_sampler_params={'steps': 25},
        )[0]
        mesh.simplify(16777216)  # nvdiffrast limit
        gc.collect()
        torch.cuda.empty_cache()
        glb = o_voxel.postprocess.to_glb(
            vertices=mesh.vertices,
            faces=mesh.faces,
            attr_volume=mesh.attrs,
            coords=mesh.coords,
            attr_layout=mesh.layout,
            voxel_size=mesh.voxel_size,
            aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
            decimation_target=cfg.get("faces", FACES),
            texture_size=cfg.get("texture", 2048),
            remesh=True,
            remesh_band=1,
            remesh_project=0,
            verbose=False,
        )
        # PNG textures: Blender builds without WebP glTF support would otherwise import untextured
        glb.export(os.path.join(d, "model.glb"))
        print(f"[done] {obj['id']} in {time.time() - t0:.0f}s", flush=True)
    except Exception:
        print(f"[FAIL] {obj['id']}", flush=True)
        traceback.print_exc()
        # CUDA errors usually leave the context unusable: quit so the caller restarts a fresh process
        sys.stdout.flush()
        os._exit(3)
    finally:
        mesh = glb = None
        gc.collect()
        torch.cuda.empty_cache()
