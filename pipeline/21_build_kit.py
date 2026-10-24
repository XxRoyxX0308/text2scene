"""Modular kit pieces -> export/kit/SM_<set>_<piece>/ (runs inside Blender, headless).

usage: blender -b --factory-startup -P pipeline/21_build_kit.py -- <project_dir>

Builds exactly the pieces 30_layout.py listed in export/kit_needed.json, so run the layout step first. Every
piece snaps to the kit grid (module, wall thickness and door / window sizes come from assets.json "kit"):

  Wall_W<w>_H<h>          plain wall, centred on its grid line, baseboards on both faces
  WallDoor_..             wall with a door opening, casings and jamb lining (trim material)
  WallWindow_..           wall with a window: frame, mullion over 1.2 m, sill, glass pane (glass slot)
  Header_.._O<h>          the part of a wall above a wide opening
  Post_H<h>               square column covering corners, T-junctions and free wall ends
  Floor_W<w> / Ceiling_W<w>  slabs with chamfered edges, so tiles read as real joints

Conventions: pivot at the bottom centre of the grid line (walls) or the top / bottom centre (floor / ceiling),
wall length along X, face A towards -Y, face B towards +Y. UVs are metres (1 UV unit = 1 m); the engine material
tiles by 1 / tile_m. Material slots are named by role (wall_a, wall_b, trim, glass, post, floor, ceiling); the
level assigns the real material per instance, so one mesh serves every room of the set.
Hard edges are chamfered (kit.bevel, default 12 mm) and get weighted normals so they catch highlights.
"""
import math
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from t2s import Project, kit_piece_name, load, save

if not bpy.app.background:
    raise SystemExit("run this headless (blender -b -P ...)")

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PROJECT = argv[0]

SLOTS = {"wall": ["wall_a", "wall_b", "trim"], "door": ["wall_a", "wall_b", "trim"],
         "window": ["wall_a", "wall_b", "trim", "glass"], "header": ["wall_a", "wall_b", "trim"],
         "post": ["post"], "floor": ["floor"], "ceiling": ["ceiling"]}
ROLE = {"wall_a": "wall", "wall_b": "wall", "trim": "trim", "glass": "glass", "post": "trim", "floor": "floor",
        "ceiling": "ceiling"}


class Builder:
    def __init__(self, slots):
        self.bm = bmesh.new()
        self.slots = slots
        self.collision = []

    def slot(self, name):
        return self.slots.index(name)

    def box(self, lo, hi, slot, bevel=0.0):
        """Axis-aligned box from lo to hi; bevel chamfers all its edges."""
        bm = bmesh.new()
        size = Vector(hi) - Vector(lo)
        centre = (Vector(hi) + Vector(lo)) / 2
        bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation(centre) @ Matrix.Diagonal((*size, 1)))
        if bevel > 0:
            bmesh.ops.bevel(bm, geom=list(bm.edges), offset=min(bevel, min(size) / 3), segments=1,
                            affect="EDGES", profile=0.5, clamp_overlap=True)
        self.merge(bm, self.slot(slot))

    def merge(self, bm, mat):
        for f in bm.faces:
            f.material_index = mat
        me = bpy.data.meshes.new("tmp")
        bm.to_mesh(me)
        bm.free()
        self.bm.from_mesh(me)
        bpy.data.meshes.remove(me)

    def slab(self, xs, zs, solid, t, reveal_slot):
        """Wall slab made of grid cells: front (face A, -Y) and back (face B, +Y) faces for solid cells, side
        faces where a solid cell meets an empty cell (opening reveals, trim slot) or the piece boundary."""
        bm = bmesh.new()
        ya, yb = -t / 2, t / 2
        nx, nz = len(xs) - 1, len(zs) - 1

        def quad(pts, slot):
            f = bm.faces.new([bm.verts.new(p) for p in pts])
            f.material_index = slot
            return f

        for i in range(nx):
            for k in range(nz):
                if not solid(i, k):
                    continue
                x0, x1, z0, z1 = xs[i], xs[i + 1], zs[k], zs[k + 1]
                quad([(x0, ya, z0), (x1, ya, z0), (x1, ya, z1), (x0, ya, z1)], self.slot("wall_a"))
                quad([(x1, yb, z0), (x0, yb, z0), (x0, yb, z1), (x1, yb, z1)], self.slot("wall_b"))
                inside = lambda a, b: 0 <= a < nx and 0 <= b < nz
                for (di, dk), pts in (((-1, 0), [(x0, yb, z0), (x0, ya, z0), (x0, ya, z1), (x0, yb, z1)]),
                                      ((1, 0), [(x1, ya, z0), (x1, yb, z0), (x1, yb, z1), (x1, ya, z1)]),
                                      ((0, -1), [(x0, ya, z0), (x0, yb, z0), (x1, yb, z0), (x1, ya, z0)]),
                                      ((0, 1), [(x0, yb, z1), (x0, ya, z1), (x1, ya, z1), (x1, yb, z1)])):
                    ni, nk = i + di, k + dk
                    if inside(ni, nk) and solid(ni, nk):
                        continue
                    quad(pts, reveal_slot if inside(ni, nk) else self.slot("wall_a"))
                self.collision.append(((x0, ya, z0), (x1, yb, z1)))
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
        mats = [f.material_index for f in bm.faces]
        me = bpy.data.meshes.new("tmp")
        bm.to_mesh(me)
        bm.free()
        for f, m in zip(me.polygons, mats):
            f.material_index = m
        self.bm.from_mesh(me)
        bpy.data.meshes.remove(me)

    def finish(self, name):
        bm = self.bm
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
        bmesh.ops.triangulate(bm, faces=bm.faces)
        uv = bm.loops.layers.uv.new("UVMap")
        for f in bm.faces:
            n = f.normal
            ax = max(range(3), key=lambda i: abs(n[i]))
            for loop in f.loops:
                p = loop.vert.co
                if ax == 2:
                    loop[uv].uv = (p.x, p.y if n.z > 0 else -p.y)
                elif ax == 1:
                    loop[uv].uv = (p.x if n.y < 0 else -p.x, p.z)
                else:
                    loop[uv].uv = (p.y if n.x > 0 else -p.y, p.z)
            f.smooth = True
        for e in bm.edges:
            e.smooth = not (e.is_manifold and e.calc_face_angle(0) > math.radians(50))
        me = bpy.data.meshes.new(name)
        bm.to_mesh(me)
        bm.free()
        obj = bpy.data.objects.new(name, me)
        bpy.context.collection.objects.link(obj)
        for s in self.slots:
            me.materials.append(bpy.data.materials.get(s) or bpy.data.materials.new(s))
        wn = obj.modifiers.new("wn", "WEIGHTED_NORMAL")
        wn.keep_sharp = True
        wn.weight = 100
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.modifier_apply(modifier=wn.name)
        obj.select_set(False)
        return obj


def casing(b, xs_open, z0, z1, t, width=0.07, proud=0.015, bevel=0.004, top=True, bottom=False):
    """Trim around an opening on both wall faces."""
    xa, xb = xs_open
    for y0, y1 in ((-t / 2 - proud, -t / 2), (t / 2, t / 2 + proud)):
        b.box((xa - width, y0, z0), (xa, y1, z1 + (width if top else 0)), "trim", bevel)
        b.box((xb, y0, z0), (xb + width, y1, z1 + (width if top else 0)), "trim", bevel)
        if top:
            b.box((xa, y0, z1), (xb, y1, z1 + width), "trim", bevel)
        if bottom:
            b.box((xa - width, y0, z0 - width), (xb + width, y1, z0), "trim", bevel)


def baseboards(b, runs, t, height, depth=0.012, bevel=0.003):
    if height <= 0:
        return
    for xa, xb in runs:
        b.box((xa, -t / 2 - depth, 0), (xb, -t / 2, height), "trim", bevel)
        b.box((xa, t / 2, 0), (xb, t / 2 + depth, height), "trim", bevel)


def build_piece(kit, kit_set, piece, spec):
    t = float(kit["wall_thickness"])
    bevel = float(kit.get("bevel", 0.012))
    bb = float(kit["sets"][kit_set].get("baseboard", 0.1))
    kind = spec["type"]
    b = Builder(SLOTS[kind])
    w, h = spec["w"], spec.get("h", 0)
    hw = w / 2
    if kind == "wall":
        b.slab([-hw, hw], [0, h], lambda i, k: True, t, b.slot("trim"))
        baseboards(b, [(-hw, hw)], t, bb)
    elif kind == "door":
        ow, oh = spec["ow"] / 2, spec["oh"]
        b.slab([-hw, -ow, ow, hw], [0, oh, h], lambda i, k: not (i == 1 and k == 0), t, b.slot("trim"))
        casing(b, (-ow, ow), 0, oh, t)
        baseboards(b, [(-hw, -ow - 0.07), (ow + 0.07, hw)], t, bb)
        b.collision = [c for c in b.collision]          # opening cell stays walkable (no collision)
    elif kind == "window":
        ow, oh, sill = spec["ow"] / 2, spec["oh"], spec["sill"]
        b.slab([-hw, -ow, ow, hw], [0, sill, sill + oh, h], lambda i, k: not (i == 1 and k == 1), t, b.slot("trim"))
        casing(b, (-ow, ow), sill, sill + oh, t, bottom=False)
        fw, fd = 0.05, t * 0.45                         # window frame inside the reveal
        z0, z1 = sill, sill + oh
        b.box((-ow, -fd / 2, z0), (ow, fd / 2, z0 + fw), "trim", 0.003)
        b.box((-ow, -fd / 2, z1 - fw), (ow, fd / 2, z1), "trim", 0.003)
        b.box((-ow, -fd / 2, z0), (-ow + fw, fd / 2, z1), "trim", 0.003)
        b.box((ow - fw, -fd / 2, z0), (ow, fd / 2, z1), "trim", 0.003)
        if 2 * ow > 1.2:
            b.box((-fw / 2, -fd / 2, z0), (fw / 2, fd / 2, z1), "trim", 0.003)
        b.box((-ow - 0.03, -t / 2 - 0.04, sill - 0.03), (ow + 0.03, t / 2 + 0.04, sill), "trim", 0.004)   # sill
        b.box((-ow + fw, -0.004, z0 + fw), (ow - fw, 0.004, z1 - fw), "glass")
        b.collision.append(((-ow, -t / 2, z0), (ow, t / 2, z1)))
        baseboards(b, [(-hw, hw)], t, bb)
    elif kind == "header":
        oh = spec["oh"]
        b.slab([-hw, hw], [0, oh, h], lambda i, k: k == 1, t, b.slot("trim"))
    elif kind == "post":
        s = spec["w"] / 2
        b.box((-s, -s, 0), (s, s, h), "post", bevel)
        b.collision.append(((-s, -s, 0), (s, s, h)))
    elif kind == "floor":
        b.box((-hw, -hw, -0.2), (hw, hw, 0), "floor", bevel * 0.5)
        b.collision.append(((-hw, -hw, -0.2), (hw, hw, 0)))
    elif kind == "ceiling":
        b.box((-hw, -hw, 0), (hw, hw, 0.15), "ceiling", bevel * 0.5)
        b.collision.append(((-hw, -hw, 0), (hw, hw, 0.15)))
    name = kit_piece_name(kit_set, piece)
    obj = b.finish(name)
    cols = []
    for n, (lo, hi) in enumerate(b.collision):
        bm = bmesh.new()
        size = Vector(hi) - Vector(lo)
        bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation((Vector(hi) + Vector(lo)) / 2) @
                              Matrix.Diagonal((*size, 1)))
        me = bpy.data.meshes.new(f"UCX_{name}_{n:02d}")
        bm.to_mesh(me)
        bm.free()
        col = bpy.data.objects.new(me.name, me)
        bpy.context.collection.objects.link(col)
        cols.append(col)
    return obj, cols


def export(objs, path):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.export_scene.fbx(filepath=path, use_selection=True, object_types={"MESH"}, apply_unit_scale=True,
                             apply_scale_options="FBX_SCALE_NONE", axis_forward="-Z", axis_up="Y",
                             bake_space_transform=True, mesh_smooth_type="FACE", use_tspace=True,
                             add_leaf_bones=False, path_mode="STRIP", embed_textures=False)


def preview(objs, path):
    """All pieces of a set side by side, slots in flat colours (wall_a warm, wall_b cool, trim dark)."""
    scene = bpy.context.scene
    for engine in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    colours = {"wall_a": (0.8, 0.7, 0.6), "wall_b": (0.55, 0.65, 0.8), "trim": (0.15, 0.15, 0.16),
               "glass": (0.6, 0.85, 1.0), "post": (0.3, 0.3, 0.32), "floor": (0.5, 0.5, 0.45),
               "ceiling": (0.85, 0.85, 0.85)}
    for name, c in colours.items():
        m = bpy.data.materials.get(name)
        if m:
            m.use_nodes = True
            bsdf = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
            bsdf.inputs["Base Color"].default_value = (*c, 1)
            bsdf.inputs["Roughness"].default_value = 0.6
    x = 0.0
    for o in scene.objects:
        o.hide_render = o.name.startswith("UCX_")
    for o in objs:
        o.location.x = x + o.dimensions.x / 2
        x += o.dimensions.x + 0.6
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    next(n for n in world.node_tree.nodes if n.type == "BACKGROUND").inputs["Color"].default_value = (0.3, 0.33, 0.37, 1)
    scene.world = world
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 3.5
    sun.rotation_euler = (math.radians(55), 0, math.radians(-35))
    scene.collection.objects.link(sun)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    hmax = max(o.dimensions.z for o in objs)
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = max(x, hmax * 2.2)
    cam.location = (x / 2, -40, hmax * 0.5 + 18)
    cam.rotation_euler = (math.radians(66), 0, 0)
    scene.render.resolution_x, scene.render.resolution_y = 1600, 700
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    for o in objs:
        o.location.x = 0


def axes(project):
    """SM_T2S_Axes: arms along +X (1 m), +Y (2 m), +Z (3 m). The engine importers compare its imported bounds
    with the expected ones, so a wrong axis or unit conversion is caught before a whole level is built."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b = Builder(["axes"])
    t = 0.05
    b.box((-t, -t, -t), (1, t, t), "axes")
    b.box((-t, -t, -t), (t, 2, t), "axes")
    b.box((-t, -t, -t), (t, t, 3), "axes")
    obj = b.finish("SM_T2S_Axes")
    out = project.export("kit", obj.name)
    os.makedirs(out, exist_ok=True)
    export([obj], os.path.join(out, f"{obj.name}.fbx"))
    save(os.path.join(out, "asset.json"), {
        "kind": "calibration", "id": obj.name, "name": obj.name, "bounds_min": [-t, -t, -t], "bounds_max": [1, 2, 3],
        "lods": [{"file": f"{obj.name}.fbx", "tris": len(obj.data.polygons), "screen_size": 1.0}], "slots": []})


def main():
    project = Project(PROJECT)
    axes(project)
    needed = load(project.export("kit_needed.json"))
    kit = {**needed["kit"], "sets": needed["sets"]}
    for kit_set, pieces in needed["pieces"].items():
        bpy.ops.wm.read_factory_settings(use_empty=True)
        built = []
        for piece, spec in sorted(pieces.items()):
            obj, cols = build_piece(kit, kit_set, piece, spec)
            out = project.export("kit", obj.name)
            os.makedirs(out, exist_ok=True)
            export([obj] + cols, os.path.join(out, f"{obj.name}.fbx"))
            dims = obj.dimensions
            mats = kit["sets"][kit_set]
            save(os.path.join(out, "asset.json"), {
                "kind": "kit", "id": obj.name, "name": obj.name, "set": kit_set, "type": spec["type"], "spec": spec,
                "bounds_m": [round(dims.x, 3), round(dims.y, 3), round(dims.z, 3)],
                "lods": [{"file": f"{obj.name}.fbx", "tris": sum(len(p.vertices) - 2 for p in obj.data.polygons),
                          "screen_size": 1.0}],
                "collision": [c.name for c in cols],
                "slots": [{"name": s, "material": mats.get(ROLE[s], mats.get("wall"))} for s in SLOTS[spec["type"]]],
                "nanite": spec["type"] != "window"})
            built.append(obj)
            print(f"[done] {obj.name}: {len(obj.data.polygons)} tris, {len(cols)} collision boxes", flush=True)
        preview(built, project.export("kit", f"preview_{kit_set}.png"))


main()
