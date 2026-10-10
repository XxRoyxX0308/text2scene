"""Tile every work/renders/view_*.png into one labelled sheet -> work/renders/contact_sheet.png

usage (trellis2 env): python 04_contact_sheet.py <project_dir> [columns]
"""
import glob
import os
import re
import sys

from PIL import Image, ImageDraw

PROJECT = sys.argv[1]
COLS = int(sys.argv[2]) if len(sys.argv) > 2 else 4
CELL_W = 640
d = os.path.join(PROJECT, "work", "renders")
# natural order: view_A2 before view_A10, view_A2 before view_A2b
paths = sorted(glob.glob(os.path.join(d, "view_*.png")),
               key=lambda p: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", os.path.basename(p))])
if not paths:
    sys.exit(f"no view_*.png in {d}")
w, h = Image.open(paths[0]).size
cell_h = round(CELL_W * h / w)
rows = -(-len(paths) // COLS)
out = Image.new("RGB", (CELL_W * COLS, cell_h * rows), (0, 0, 0))
for i, p in enumerate(paths):
    cell = Image.open(p).convert("RGB").resize((CELL_W, cell_h), Image.LANCZOS)
    ImageDraw.Draw(cell).text((8, 6), os.path.basename(p)[5:-4], fill=(255, 0, 255), font_size=28)
    out.paste(cell, (i % COLS * CELL_W, i // COLS * cell_h))
path = os.path.join(d, "contact_sheet.png")
out.save(path)
print(path, out.size, f"{len(paths)} views")
