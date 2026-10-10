"""Step 2: review + background removal.

  sheet : build a contact sheet (all candidates, numbered) per object for review
  matte : take the candidate named in work/selection.json, remove the background (RMBG-2.0) -> rgba.png + review.png

usage (trellis2 env): python 02_select_matte.py <project_dir> sheet|matte [object_id ...]
"""
import glob
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

PROJECT, MODE = sys.argv[1], sys.argv[2]
ONLY = set(sys.argv[3:])
sys.path.insert(0, os.path.expanduser("~/TRELLIS.2"))
scene = json.load(open(os.path.join(PROJECT, "scene.json"), encoding="utf-8"))
CELL = 512


def fit(im, size=CELL):
    im = im.convert("RGB")
    im.thumbnail((size, size), Image.LANCZOS)
    cell = Image.new("RGB", (size, size), (40, 40, 40))
    cell.paste(im, ((size - im.width) // 2, (size - im.height) // 2))
    return cell


def sheet(obj, d):
    cands = sorted(glob.glob(os.path.join(d, "cand_*.png")))
    if not cands:
        print(f"{obj['id']}: no candidates yet, skipped")
        return
    cells = [(os.path.basename(c)[5:-4], fit(Image.open(c))) for c in cands]
    out = Image.new("RGB", (CELL * len(cells), CELL), (0, 0, 0))
    for i, (label, cell) in enumerate(cells):
        ImageDraw.Draw(cell).text((8, 6), label, fill=(255, 0, 255), font_size=40)
        out.paste(cell, (i * CELL, 0))
    out.save(os.path.join(d, "sheet.png"))
    print(f"{obj['id']}: {len(cands)} candidates")


_rembg = None


def matte(obj, d, pick):
    global _rembg
    if _rembg is None:
        from trellis2.pipelines.rembg import BiRefNet
        _rembg = BiRefNet("briaai/RMBG-2.0")
        _rembg.cuda()
    rgb = Image.open(os.path.join(d, f"cand_{pick}.png")).convert("RGB")
    rgba = np.asarray(_rembg(rgb.copy())).copy()
    alpha = rgba[:, :, 3]
    alpha[alpha < 24] = 0
    Image.fromarray(rgba).save(os.path.join(d, "rgba.png"))
    a = alpha[:, :, None].astype(np.float32) / 255
    on_magenta = (rgba[:, :, :3] * a + np.array((255, 0, 255), np.float32) * (1 - a)).astype(np.uint8)
    both = np.concatenate([np.asarray(rgb), on_magenta], axis=1)
    im = Image.fromarray(both)
    im.thumbnail((1400, 700), Image.LANCZOS)
    im.save(os.path.join(d, "review.png"))
    print(f"{obj['id']}: picked cand_{pick}, coverage {float((alpha > 127).mean()):.2f}")


selection = {}
if MODE == "matte":
    selection = json.load(open(os.path.join(PROJECT, "work", "selection.json"), encoding="utf-8"))
for obj in scene["objects"]:
    if ONLY and obj["id"] not in ONLY:
        continue
    d = os.path.join(PROJECT, "work", "objects", obj["id"])
    if MODE == "sheet":
        sheet(obj, d)
    elif obj["id"] in selection:
        matte(obj, d, selection[obj["id"]]["pick"])
    else:
        print(f"{obj['id']}: no selection yet, skipped")
