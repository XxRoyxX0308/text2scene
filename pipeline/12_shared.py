"""Shared textures every engine material uses -> export/shared/.

usage: python 12_shared.py <project_dir>

  T_Macro.png        linear RGB, three independent tiling noise layers at different scales. Surface materials
                     sample it in world space (one tile covers ~8 m) to vary brightness and roughness, so the
                     same tileable texture never repeats identically across a big wall or floor.
  T_DetailNormal_N   fine tiling grain normal (OpenGL), blended over the main normal up close.
  T_Default_*        neutral fallbacks (white base colour, flat normal, AO 1 / roughness 0.5 / metal 0).
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import textures as tx
from t2s import Project, save


def build(project):
    out = project.export("shared")
    macro = np.stack([tx.tileable_noise(1024, 256, seed=11), tx.tileable_noise(1024, 96, seed=12),
                      tx.tileable_noise(1024, 32, seed=13)], axis=2)
    tx.save(os.path.join(out, "T_Macro.png"), macro)
    grain = tx.tileable_noise(512, 3, seed=21, octaves=3)
    tx.save(os.path.join(out, "T_DetailNormal_N.png"), tx.normal_from_height(grain, strength=1.2))
    files = tx.write_maps(out, "Default", np.ones((4, 4, 3), np.float32), None, 1.0, 0.5, 0.0, clamp=False)
    save(os.path.join(out, "asset.json"), {"kind": "shared", "id": "shared", "macro": "T_Macro.png", "detail_n": "T_DetailNormal_N.png",
                                           "default": files})
    print(f"shared textures -> {out}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    build(Project(sys.argv[1]))
