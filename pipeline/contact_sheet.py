"""Tile every review shot of one engine into one labelled sheet -> export/shots/<engine>/contact_sheet.png

usage: python contact_sheet.py <project_dir> [unreal|unity] [columns]
"""
import glob
import os
import re
import sys

from PIL import Image, ImageDraw

PROJECT = sys.argv[1]
ENGINE = sys.argv[2] if len(sys.argv) > 2 else "unreal"
COLS = int(sys.argv[3]) if len(sys.argv) > 3 else 4
CELL_W = 640
d = os.path.join(PROJECT, "export", "shots", ENGINE)
# natural order: A2_v1 before A10_v1
paths = sorted((p for p in glob.glob(os.path.join(d, "*.png")) if not p.endswith("contact_sheet.png")),
               key=lambda p: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", os.path.basename(p))])
if not paths:
    sys.exit(f"no shots in {d}")
w, h = Image.open(paths[0]).size
cell_h = round(CELL_W * h / w)
rows = -(-len(paths) // COLS)
out = Image.new("RGB", (CELL_W * COLS, cell_h * rows), (0, 0, 0))
for i, p in enumerate(paths):
    cell = Image.open(p).convert("RGB").resize((CELL_W, cell_h), Image.LANCZOS)
    ImageDraw.Draw(cell).text((8, 6), os.path.basename(p)[:-4], fill=(255, 0, 255), font_size=28)
    out.paste(cell, (i % COLS * CELL_W, i // COLS * cell_h))
path = os.path.join(d, "contact_sheet.png")
out.save(path)
print(path, out.size, f"{len(paths)} views")
