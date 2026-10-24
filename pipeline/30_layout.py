"""layout.json -> export/level.json (engine-neutral level) + export/kit_needed.json + export/budget.json

usage: python 30_layout.py <project_dir>

Run it once before 21_build_kit.py (so the kit builder knows which pieces the rooms need) and again after all
assets are exported (so the budget report has real triangle counts and texture sizes). It never touches an engine.

What it does
  rooms   -> a global wall graph on the kit grid. Every grid edge between two different spaces gets exactly one
             wall piece (no doubled walls, no z-fighting); each face of a shared wall takes the material of the
             room it faces; exterior faces take the kit set's exterior material. Openings become door / window /
             open pieces, corners and free wall ends get posts, every cell gets a floor tile and a ceiling tile.
  props   -> world transforms; "against": "N"|"S"|"E"|"W" puts the prop's back on that wall facing into the room
  scatter -> deterministic Poisson placement of clutter (floor or along walls), clear of doors and placed props
  decals  -> world position plus projection axes on floors, ceilings or walls
  lights  -> world lights; "grid" spreads one definition over the ceiling, "fixture" places a prop at each light
  volumes -> one reflection probe / light probe box / post volume per room
  budget  -> triangles, instances, materials, texture memory, overlapping shadowed lights, decals, translucency

See AGENT.md section 6 for the layout.json format, docs/STANDARDS.md for the budget numbers.
"""
import math
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from t2s import Project, kit_piece_name, load, mesh_name, save

SIDES = {"S": (0, -1), "N": (0, 1), "W": (-1, 0), "E": (1, 0)}
BUDGET = {
    "tris_per_room": 1_500_000,      # LOD0, before Nanite / LOD; a warning, not a hard limit
    "instances_per_room": 1500,
    "shadowed_lights_overlap": 4,    # UE stationary lights: max 4 overlapping shadow casters
    "decals_per_room": 60,
    "translucent_per_room": 40,
    "texture_mb": 2048,              # whole level, compressed, with mips
    "materials": 120,
}


def rot(v, yaw):
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c)


class Room:
    def __init__(self, r, kit):
        self.id = r["id"]
        self.r = r
        self.set = r.get("kit") or next(iter(kit["sets"]))
        mats = dict(kit["sets"][self.set])
        mats.update({k: r[k] for k in ("floor", "wall", "ceiling", "trim", "exterior", "glass") if isinstance(r.get(k), str)})
        self.mats = mats
        self.origin = list(r.get("origin", [0, 0])) + [0.0] * (3 - len(r.get("origin", [0, 0])))
        self.yaw = r.get("yaw", 0) % 360
        if self.yaw % 90:
            raise ValueError(f"room {self.id}: yaw must be a multiple of 90")
        self.w, self.d, self.h = r["size"]
        self.outdoor = r.get("outdoor", False)
        self.has_ceiling = r.get("ceiling", not self.outdoor) is not False

    def world(self, p):
        x, y = rot(p[:2], self.yaw)
        z = p[2] if len(p) > 2 else 0.0
        return [self.origin[0] + x, self.origin[1] + y, self.origin[2] + z]

    def world_dir(self, v):
        x, y = rot(v[:2], self.yaw)
        return [x, y, v[2] if len(v) > 2 else 0.0]

    def rects(self):
        """Local rectangles [x0, y0, x1, y1]; an L-shaped room lists several under "rects"."""
        return self.r.get("rects") or [[0, 0, self.w, self.d]]


class Compiler:
    def __init__(self, project):
        self.p = project
        self.kit = {"module": 2.0, "wall_thickness": 0.2, "door": {"width": 1.2, "height": 2.2},
                    "window": {"width": 1.4, "height": 1.4, "sill": 0.9}, **project.assets.get("kit", {})}
        if "sets" not in self.kit:
            raise ValueError("assets.json kit.sets is empty: define at least one kit set")
        self.M = float(self.kit["module"])
        self.t = float(self.kit["wall_thickness"])
        self.layout = project.layout
        self.rooms = {r["id"]: Room(r, self.kit) for r in self.layout["rooms"]}
        self.instances, self.lights, self.decals, self.cameras, self.volumes = [], [], [], [], []
        self.kit_needed = defaultdict(dict)
        self.warnings = []
        self.counter = defaultdict(int)
        self.assets = self.load_assets()

    def warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)
            print("WARNING", msg)

    def load_assets(self):
        out = {}
        for root, _, files in os.walk(self.p.export()):
            if "asset.json" in files:
                a = load(os.path.join(root, "asset.json"))
                out[a.get("name", a["id"])] = a
        return out

    def name(self, room, kind):
        self.counter[(room, kind)] += 1
        return f"{room}_{kind}_{self.counter[(room, kind)]:03d}"

    def need(self, kit_set, piece, spec):
        name = kit_piece_name(kit_set, piece)
        self.kit_needed[kit_set][piece] = spec
        return name

    def add(self, room, kind, asset, pos, yaw=0.0, scale=1.0, materials=None, tags=()):
        inst = {"name": self.name(room, kind), "asset": asset, "room": room, "kind": kind,
                "pos": [round(v, 4) for v in pos], "yaw": round(yaw % 360, 3), "scale": scale}
        if materials:
            inst["materials"] = materials
        if tags:
            inst["tags"] = list(tags)
        self.instances.append(inst)
        return inst

    # ------------------------------------------------------------------ architecture
    def cells(self):
        """{(level z, ix, iy): room id} for every grid cell, warning on overlaps and off-grid sizes."""
        own = {}
        M = self.M
        for room in self.rooms.values():
            for x0, y0, x1, y1 in room.rects():
                for v in (x0, y0, x1, y1, room.origin[0], room.origin[1]):
                    if abs(v / M - round(v / M)) > 1e-6:
                        self.warn(f"room {room.id}: {v} is not on the {M} m kit grid (snapped)")
                for i in range(round(x0 / M), round(x1 / M)):
                    for j in range(round(y0 / M), round(y1 / M)):
                        wx, wy, _ = room.world([(i + 0.5) * M, (j + 0.5) * M])
                        key = (round(room.origin[2], 3), math.floor(wx / M), math.floor(wy / M))
                        if key in own:
                            self.warn(f"rooms {own[key]} and {room.id} overlap at cell {key}")
                        own[key] = room.id
        return own

    def openings(self, own):
        """{edge key: opening dict} from every room's openings, mapped to world grid edges."""
        M = self.M
        out = {}
        for room in self.rooms.values():
            z = round(room.origin[2], 3)
            for op in room.r.get("openings", []):
                side = op["wall"]
                kind = op.get("kind", "door")
                width = op.get("width", M) if kind == "open" else M
                segs = max(1, round(width / M))
                for k in range(segs):
                    a = op["at"] - width / 2 + M * (k + 0.5)
                    local = {"S": (a, 0), "N": (a, room.d), "W": (0, a), "E": (room.w, a)}[side]
                    wx, wy, _ = room.world(local)
                    gx, gy = round(wx / M), round(wy / M)
                    # the edge through this point, perpendicular to the wall's outward direction
                    nx, ny = rot(SIDES[side], room.yaw)
                    if abs(nx) > 0.5:
                        key = (z, "y", gx, math.floor(wy / M))
                    else:
                        key = (z, "x", math.floor(wx / M), gy)
                    if key in out and out[key]["kind"] != kind:
                        self.warn(f"room {room.id}: opening {side}@{op['at']} conflicts with {out[key]['room']}")
                        continue
                    out[key] = {**op, "kind": kind, "room": room.id}
        return out

    def architecture(self):
        M, t = self.M, self.t
        own = self.cells()
        ops = self.openings(own)
        edges = {}
        for (z, ix, iy), rid in own.items():
            for key in ((z, "x", ix, iy), (z, "x", ix, iy + 1), (z, "y", ix, iy), (z, "y", ix + 1, iy)):
                edges[key] = None
        verts = defaultdict(list)
        for key in edges:
            z, d, ix, iy = key
            a, b = ((z, ix, iy - 1), (z, ix, iy)) if d == "x" else ((z, ix, iy), (z, ix - 1, iy))
            ra, rb = own.get(a), own.get(b)        # a = face A side (south / east), b = face B side
            if ra == rb:
                continue
            rooms = [self.rooms[r] for r in (ra, rb) if r]
            indoor = [r for r in rooms if not r.outdoor]
            if not indoor and key not in ops:
                continue                           # outdoor to outdoor / void: no wall
            main = indoor[0] if indoor else rooms[0]
            h = max(r.h for r in indoor) if indoor else main.h

            def face(rid):
                r = self.rooms.get(rid) if rid else None
                return r.mats["wall"] if r and not r.outdoor else main.mats.get("exterior", main.mats["wall"])

            mats = {"wall_a": face(ra), "wall_b": face(rb), "trim": main.mats.get("trim", main.mats["wall"])}
            centre = ((ix + 0.5) * M, iy * M, z) if d == "x" else (ix * M, (iy + 0.5) * M, z)
            yaw = 0 if d == "x" else 90
            op = ops.get(key)
            hk = f"H{round(h * 100)}"
            wk = f"W{round(M * 100)}"
            if op is None:
                piece = self.need(main.set, f"Wall_{wk}_{hk}", {"type": "wall", "w": M, "h": h})
            elif op["kind"] == "door":
                dw, dh = self.kit["door"]["width"], self.kit["door"]["height"]
                piece = self.need(main.set, f"WallDoor_{wk}_{hk}", {"type": "door", "w": M, "h": h, "ow": dw, "oh": dh})
            elif op["kind"] == "window":
                win = self.kit["window"]
                piece = self.need(main.set, f"WallWindow_{wk}_{hk}",
                                  {"type": "window", "w": M, "h": h, "ow": win["width"], "oh": win["height"], "sill": win["sill"]})
                mats["glass"] = main.mats.get("glass", "glass_clear")
            else:   # open: nothing below the opening height, a header above it
                oh = op.get("height", h)
                if oh >= h - 1e-3:
                    continue
                piece = self.need(main.set, f"Header_{wk}_{hk}_O{round(oh * 100)}", {"type": "header", "w": M, "h": h, "oh": oh})
            self.add(main.id, "Wall", piece, centre, yaw, materials=mats)
            if op is None or op["kind"] != "open":
                for v in (((ix, iy), (ix + 1, iy)) if d == "x" else ((ix, iy), (ix, iy + 1))):
                    verts[(z,) + v].append((d, h, main))
        for (z, vx, vy), wall in verts.items():
            dirs = {d for d, _, _ in wall}
            if len(dirs) == 2 or len(wall) == 1:
                h = max(hh for _, hh, _ in wall)
                main = wall[0][2]
                piece = self.need(main.set, f"Post_H{round(h * 100)}", {"type": "post", "w": t + 0.04, "h": h})
                self.add(main.id, "Post", piece, (vx * M, vy * M, z), 0,
                         materials={"post": main.mats.get("trim", main.mats["wall"])})
        for (z, ix, iy), rid in own.items():
            room = self.rooms[rid]
            centre = ((ix + 0.5) * M, (iy + 0.5) * M, z)
            piece = self.need(room.set, f"Floor_W{round(M * 100)}", {"type": "floor", "w": M})
            self.add(rid, "Floor", piece, centre, 0, materials={"floor": room.mats["floor"]})
            if room.has_ceiling:
                piece = self.need(room.set, f"Ceiling_W{round(M * 100)}", {"type": "ceiling", "w": M})
                self.add(rid, "Ceiling", piece, (centre[0], centre[1], z + room.h), 0,
                         materials={"ceiling": room.mats["ceiling"]})
        self.own = own
        self.ops = ops

    # ------------------------------------------------------------------ props, scatter, decals
    def bounds(self, asset):
        a = self.assets.get(asset)
        if a is None:
            return [0.6, 0.6, 1.0]
        return a.get("bounds_m", [0.6, 0.6, 1.0])

    def prop_transform(self, room, pl):
        asset = mesh_name(pl["asset"])
        bx, by, bz = self.bounds(asset)
        wall_mounted = self.assets.get(asset, {}).get("mount") == "wall"
        if "against" in pl:
            side = pl["against"]
            at = pl.get("at", (room.w if side in "SN" else room.d) / 2)
            gap = self.t / 2 + (0.003 if wall_mounted else by / 2 + pl.get("gap", 0.03))
            local, yaw = {"S": ((at, gap), 180), "N": ((at, room.d - gap), 0),
                          "W": ((gap, at), 90), "E": ((room.w - gap, at), 270)}[side]
            z = pl.get("z", 1.2 if wall_mounted else 0.0)      # wall-mounted: height of the bottom edge
            yaw = yaw + pl.get("yaw", 0)
            pos = room.world((local[0], local[1], z))
        else:
            p = pl["pos"]
            pos = room.world((p[0], p[1], p[2] if len(p) > 2 else 0.0))
            yaw = pl.get("yaw", 0)
        return asset, pos, (yaw + room.yaw) % 360

    def props(self):
        self.footprints = defaultdict(list)
        for pl in self.layout.get("props", []):
            room = self.rooms[pl["room"]]
            asset, pos, yaw = self.prop_transform(room, pl)
            if asset not in self.assets:
                self.warn(f"prop {asset} has no export yet (placed with guessed bounds)")
            self.add(room.id, "Prop", asset, pos, yaw, pl.get("scale", 1.0))
            bx, by, _ = self.bounds(asset)
            self.footprints[room.id].append((pos[0], pos[1], max(bx, by) / 2 * pl.get("scale", 1.0)))

    def door_zones(self, room):
        zones = []
        M = self.M
        for (z, d, ix, iy), op in self.ops.items():
            c = ((ix + 0.5) * M, iy * M) if d == "x" else (ix * M, (iy + 0.5) * M)
            zones.append((c[0], c[1], max(op.get("width", M), M) / 2 + 1.0))
        return zones

    def scatter(self):
        for sc in self.layout.get("scatter", []):
            room = self.rooms[sc["room"]]
            rng = random.Random(sc.get("seed", 1))
            assets = [mesh_name(a) for a in sc["assets"]]
            radius = {a: max(self.bounds(a)[:2]) / 2 for a in assets}
            blocked = self.footprints[room.id] + self.door_zones(room)
            placed, tries = [], 0
            lo_s, hi_s = sc.get("scale", [0.9, 1.1])
            while len(placed) < sc.get("count", 10) and tries < sc.get("count", 10) * 200:
                tries += 1
                a = rng.choice(assets)
                r = radius[a]
                inset = self.t / 2 + r + 0.02
                if sc.get("zone", "floor") == "edges":
                    side = rng.choice("SNWE")
                    band = inset + rng.uniform(0, sc.get("band", 0.3))
                    u = rng.uniform(inset, (room.w if side in "SN" else room.d) - inset)
                    local, yaw = {"S": ((u, band), 180), "N": ((u, room.d - band), 0),
                                  "W": ((band, u), 90), "E": ((room.w - band, u), 270)}[side]
                    yaw += rng.uniform(-1, 1) * sc.get("yaw_jitter", 15)
                else:
                    local = (rng.uniform(inset, room.w - inset), rng.uniform(inset, room.d - inset))
                    yaw = rng.uniform(-1, 1) * sc.get("yaw_jitter", 180)
                if not self.inside(room, local):
                    continue
                wx, wy, wz = room.world((local[0], local[1], 0))
                if any(math.hypot(wx - x, wy - y) < r + rr for x, y, rr in blocked + placed):
                    continue
                s = rng.uniform(lo_s, hi_s)
                placed.append((wx, wy, r * s))
                self.add(room.id, "Scatter", a, (wx, wy, wz), (yaw + room.yaw) % 360, round(s, 3))
            if len(placed) < sc.get("count", 10):
                self.warn(f"scatter in {room.id}: placed {len(placed)} of {sc.get('count', 10)} (room too full)")
            self.footprints[room.id].extend(placed)

    def inside(self, room, local):
        return any(x0 <= local[0] <= x1 and y0 <= local[1] <= y1 for x0, y0, x1, y1 in room.rects())

    def decal_list(self):
        for dc in self.layout.get("decals", []):
            room = self.rooms[dc["room"]]
            a = self.assets.get(dc["asset"]) or {}
            size = dc.get("size", a.get("size_m", [1, 1]))
            on = dc.get("on", "floor")
            u, v = dc["pos"][:2]
            if on == "floor":
                pos, normal, up = room.world((u, v, 0.0)), [0, 0, 1], room.world_dir(rot((0, 1), dc.get("yaw", 0)))
            elif on == "ceiling":
                pos, normal, up = room.world((u, v, room.h)), [0, 0, -1], room.world_dir(rot((0, 1), dc.get("yaw", 0)))
            else:
                side = on.split(":")[1]
                inset = self.t / 2
                local = {"S": (u, inset), "N": (u, room.d - inset), "W": (inset, u), "E": (room.w - inset, u)}[side]
                n = (-SIDES[side][0], -SIDES[side][1])
                pos, normal = room.world((local[0], local[1], v)), room.world_dir((n[0], n[1], 0))
                # up = world Z rotated by yaw around the wall normal
                ang = math.radians(dc.get("yaw", 0))
                side_vec = (normal[1], -normal[0])
                up = [side_vec[0] * math.sin(ang), side_vec[1] * math.sin(ang), math.cos(ang)]
            self.decals.append({"name": self.name(room.id, "Decal"), "asset": dc["asset"], "room": room.id,
                                "pos": [round(c, 4) for c in pos], "normal": [round(c, 4) for c in normal],
                                "up": [round(c, 4) for c in up], "size": size, "depth": dc.get("depth", 0.3),
                                "sort": dc.get("sort", 0)})

    # ------------------------------------------------------------------ lights, volumes, cameras
    def light_list(self):
        for lt in self.layout.get("lights", []):
            room = self.rooms[lt["room"]]
            points = []
            if "grid" in lt:
                g = lt["grid"]
                sx, sy = g.get("spacing", [3, 3])
                m = g.get("margin", min(sx, sy) / 2)
                nx = max(1, round((room.w - 2 * m) / sx) + 1)
                ny = max(1, round((room.d - 2 * m) / sy) + 1)
                for i in range(nx):
                    for j in range(ny):
                        x = room.w / 2 if nx == 1 else m + i * (room.w - 2 * m) / (nx - 1)
                        y = room.d / 2 if ny == 1 else m + j * (room.d - 2 * m) / (ny - 1)
                        local = (x, y, g.get("z", room.h - 0.05))
                        if self.inside(room, local):
                            points.append(local)
            else:
                points.append(tuple(lt["pos"]))
            for local in points:
                pos = room.world(local)
                d = room.world_dir(lt.get("dir", [0, 0, -1]))
                rec = {"name": self.name(room.id, "Light"), "room": room.id, "type": lt.get("type", "point"),
                       "pos": [round(c, 4) for c in pos], "dir": d, "kelvin": lt.get("kelvin", 4500),
                       "lumens": lt.get("lumens", 1500), "radius": lt.get("radius", 8.0),
                       "shadows": lt.get("shadows", False), "mobility": lt.get("mobility", "stationary"),
                       "size": lt.get("size", [0.3, 0.3]), "cone": lt.get("cone", 60),
                       "color": lt.get("color"), "volumetric": lt.get("volumetric", 1.0)}
                self.lights.append(rec)
                if lt.get("fixture"):
                    self.add(room.id, "Fixture", mesh_name(lt["fixture"]), (pos[0], pos[1], room.origin[2] + room.h
                             if lt.get("fixture_on_ceiling", True) else pos[2]), room.yaw)

    def volume_list(self):
        for room in self.rooms.values():
            for k, (x0, y0, x1, y1) in enumerate(room.rects()):
                a, b = room.world((x0, y0, 0)), room.world((x1, y1, room.h))
                lo = [min(a[i], b[i]) for i in range(3)]
                hi = [max(a[i], b[i]) for i in range(3)]
                self.volumes.append({"name": f"{room.id}_Volume_{k}", "room": room.id, "min": lo, "max": hi,
                                     "outdoor": room.outdoor})

    def camera_list(self):
        given = {c["room"] for c in self.layout.get("cameras", [])}
        cams = list(self.layout.get("cameras", []))
        if self.layout.get("auto_cameras", True):
            for room in self.rooms.values():
                if room.id not in given:
                    cams.append({"id": f"{room.id}_v1", "room": room.id, "pos": [room.w / 2, min(0.8, room.d / 4), 1.6],
                                 "look": [room.w / 2, room.d, 1.6]})
        for c in cams:
            room = self.rooms[c["room"]]
            pos = room.world(c["pos"])
            if "look" in c:
                tgt = room.world(c["look"])
                d = [tgt[i] - pos[i] for i in range(3)]
            else:
                yaw, pitch = math.radians(c.get("yaw", 0)), math.radians(c.get("pitch", 0))
                d = room.world_dir((-math.sin(yaw) * math.cos(pitch), math.cos(yaw) * math.cos(pitch), math.sin(pitch)))
            n = math.sqrt(sum(v * v for v in d)) or 1
            self.cameras.append({"id": c["id"], "room": room.id, "pos": [round(v, 4) for v in pos],
                                 "dir": [round(v / n, 5) for v in d], "fov": c.get("fov", 90)})
        ps = self.layout.get("player_start")
        if ps:
            room = self.rooms[ps["room"]]
            self.player_start = {"pos": room.world(ps["pos"]), "yaw": (ps.get("yaw", 0) + room.yaw) % 360}
        elif self.cameras:
            c = self.cameras[0]
            self.player_start = {"pos": [c["pos"][0], c["pos"][1], c["pos"][2] - 1.6 + 0.1],
                                 "yaw": math.degrees(math.atan2(-c["dir"][0], c["dir"][1])) % 360}

    # ------------------------------------------------------------------ budget
    def budget(self):
        from PIL import Image
        limits = {**BUDGET, **self.p.assets.get("budgets", {})}
        report = {"rooms": {}, "totals": {}, "warnings": []}
        per_room = defaultdict(lambda: {"tris": 0, "instances": 0, "decals": 0, "translucent": 0, "lights": 0,
                                        "shadowed": 0})
        uses = defaultdict(int)
        for inst in self.instances:
            uses[inst["asset"]] += 1
            a = self.assets.get(inst["asset"])
            r = per_room[inst["room"]]
            r["instances"] += 1
            r["tris"] += a["lods"][0]["tris"] if a and a.get("lods") else 0
            if "glass" in inst.get("materials", {}):
                r["translucent"] += 1
        for d in self.decals:
            per_room[d["room"]]["decals"] += 1
        for l in self.lights:
            per_room[l["room"]]["lights"] += 1
            per_room[l["room"]]["shadowed"] += bool(l["shadows"]) and l["mobility"] != "static"
        missing = sorted(a for a in uses if a not in self.assets)
        tex_bytes, materials = 0, set()
        for a in self.assets.values():
            folder = None
            for root, _, files in os.walk(self.p.export()):
                if "asset.json" in files and os.path.basename(root) in (a.get("name"), a["id"]):
                    folder = root
                    break
            if a["kind"] == "prop" and a["name"] not in uses:
                continue
            tex = list((a.get("textures") or {}).values())
            for sl in a.get("slots", []):
                tex += list((sl.get("textures") or {}).values())
                materials.add(sl.get("name"))
            if a["kind"] in ("material", "decal"):
                materials.add(a["id"])
            for f in tex:
                if folder and os.path.exists(os.path.join(folder, f)) and "_MASK" not in f:
                    w, h = Image.open(os.path.join(folder, f)).size
                    bpp = 1.0 if f.endswith(("_N.png", "_BC.png")) and a["kind"] == "decal" else (1.0 if f.endswith("_N.png") else 0.5)
                    tex_bytes += w * h * bpp * 4 / 3
        for rid, r in per_room.items():
            for key, lim in (("tris", "tris_per_room"), ("instances", "instances_per_room"), ("decals", "decals_per_room"),
                             ("translucent", "translucent_per_room"), ("shadowed", "shadowed_lights_overlap")):
                if r[key] > limits[lim]:
                    report["warnings"].append(f"{rid}: {key} {r[key]} > {limits[lim]}")
            report["rooms"][rid] = r
        report["totals"] = {"instances": len(self.instances), "unique_meshes": len(uses), "decals": len(self.decals),
                            "lights": len(self.lights), "materials": len(materials),
                            "texture_mb_estimate": round(tex_bytes / 2 ** 20, 1),
                            "tris_lod0": sum(r["tris"] for r in per_room.values()), "missing_assets": missing}
        if tex_bytes / 2 ** 20 > limits["texture_mb"]:
            report["warnings"].append(f"texture memory {tex_bytes / 2 ** 20:.0f} MB > {limits['texture_mb']} MB")
        if len(materials) > limits["materials"]:
            report["warnings"].append(f"{len(materials)} materials > {limits['materials']}: share materials")
        report["warnings"] += self.warnings
        return report

    def run(self):
        self.architecture()
        self.props()
        self.scatter()
        self.decal_list()
        self.light_list()
        self.volume_list()
        self.camera_list()
        lighting = {"gi": "dynamic", "sky": True, **self.layout.get("lighting", {})}
        level = {"name": self.layout.get("name", "T2S_Level"), "units": "metres, +X east, +Y north, +Z up, yaw CCW",
                 "kit": {"module": self.M, "wall_thickness": self.t}, "instances": self.instances,
                 "lights": self.lights, "decals": self.decals, "volumes": self.volumes, "cameras": self.cameras,
                 "player_start": getattr(self, "player_start", None), "lighting": lighting}
        save(self.p.export("level.json"), level)
        save(self.p.export("kit_needed.json"), {"kit": {k: v for k, v in self.kit.items() if k != "sets"},
                                                "sets": self.kit["sets"], "pieces": self.kit_needed})
        report = self.budget()
        save(self.p.export("budget.json"), report)
        t = report["totals"]
        print(f"level {level['name']}: {t['instances']} instances of {t['unique_meshes']} meshes, {len(self.decals)} decals, "
              f"{len(self.lights)} lights, {sum(len(v) for v in self.kit_needed.values())} kit pieces needed")
        print(f"budget: {t['tris_lod0']:,} LOD0 tris, ~{t['texture_mb_estimate']} MB textures, {t['materials']} materials, "
              f"{len(t['missing_assets'])} assets not exported yet")
        for w in report["warnings"]:
            if w not in self.warnings:
                print("BUDGET", w)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    Compiler(Project(sys.argv[1])).run()
