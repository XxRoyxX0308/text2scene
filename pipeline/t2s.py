"""Shared paths, file IO and naming for every pipeline step (standard library only).

Project layout
  assets.json            what to make: props, decals, tileable materials, modular kit sets, target engine
  layout.json            how to assemble it: rooms, openings, placements, scatter, lights, post, cameras
  work/                  intermediates (FLUX candidates, mattes, TRELLIS GLBs, downloaded scans)
  export/                the engine-neutral package every engine importer reads
    shared/              macro variation, detail normal, default textures
    materials/<id>/      tileable surface materials
    decals/<id>/         decal textures
    props/SM_<id>/       baked props (FBX per LOD + textures)
    kit/SM_<set>_<piece>/ modular kit pieces
    level.json           compiled level (world-space instances, lights, decals, volumes, cameras)
    budget.json          performance report

World convention (assets, layout, level.json): metres, right-handed, +X east, +Y north, +Z up, ground z = 0,
yaw in degrees counter-clockwise seen from above, an object's front faces -Y at yaw 0 (same as Blender).
The engine importers convert to their own axes; nothing upstream needs to know which engine is used.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Texel density targets in pixels per metre (see docs/STANDARDS.md)
TEXEL_DENSITY = {"hero": 1024, "prop": 512, "small": 512, "kit": 512}
# LOD reduction ratios (relative to LOD0) and the screen size each LOD switches at
LOD_RATIOS = {"hero": [1, 0.5, 0.25, 0.1], "prop": [1, 0.5, 0.2], "small": [1, 0.35]}
LOD_SCREEN = [1.0, 0.4, 0.15, 0.05]


def load(path, default=None):
    if not os.path.exists(path):
        if default is not None:
            return default
        raise FileNotFoundError(path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


class Project:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.assets = load(os.path.join(self.root, "assets.json"))
        layout_path = os.path.join(self.root, "layout.json")
        self.layout = load(layout_path) if os.path.exists(layout_path) else None

    def p(self, *parts):
        return os.path.join(self.root, *parts)

    @property
    def target(self):
        return self.assets.get("target", {"engine": "unreal"})

    # work folders
    def work_prop(self, pid):
        return self.p("work", "props", pid)

    def work_decal(self, did):
        return self.p("work", "decals", did)

    # export folders
    def export(self, *parts):
        return self.p("export", *parts)

    def props(self):
        return self.assets.get("props", [])

    def decals(self):
        return self.assets.get("decals", [])

    def generated(self):
        """Everything FLUX makes an image for: props (incl. image slabs) and decals, tagged with their work dir."""
        for o in self.props():
            yield o, self.work_prop(o["id"])
        for o in self.decals():
            yield o, self.work_decal(o["id"])

    def find(self, oid):
        for o, d in self.generated():
            if o["id"] == oid:
                return o, d
        raise KeyError(f"{oid} is neither a prop nor a decal in assets.json")


def mesh_name(asset_id):
    return asset_id if asset_id.startswith("SM_") else f"SM_{asset_id}"


def kit_piece_name(kit_set, piece):
    return f"SM_{kit_set}_{piece}"
