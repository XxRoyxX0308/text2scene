"""Unreal Engine 5 importer: export/ -> /Game/T2S (master materials, instances, meshes, level, review shots).

Needs the Python Editor Script Plugin and Editor Scripting Utilities (both ship with the engine).

  in the editor (Output Log, Python):
      py "<repo>/pipeline/engines/unreal/t2s_unreal.py" --export "<project>/export"
  unattended (opens the editor, runs, saves, exits; the commandlet mode has no editor world to spawn into, and
  -ExecutePythonScript exits before the screenshots, so use -ExecCmds):
      UnrealEditor.exe <Game.uproject> -unattended -nosplash
          -ExecCmds="py <repo>/pipeline/engines/unreal/t2s_unreal.py --export <project>/export --shots --exit-when-done"

  options: --assets-only | --level-only | --shots (render every level camera to export/shots/unreal/)
           --rebuild-materials (recreate the master materials) | --root /Game/T2S | --exit-when-done
  The run log is written to export/unreal_import.log.

Re-running is safe: assets whose source file has not changed are skipped (metadata tag t2s_src), and the level
actors tagged T2S are deleted and rebuilt, so anything you add by hand (untagged) survives.

Axes: level.json is metres, right-handed Z-up (+Y north). Unreal is centimetres, left-handed Z-up:
x_ue = 100 x, y_ue = -100 y, z_ue = 100 z, yaw_ue = -yaw. Meshes come through FBX with the same mapping.
"""
import argparse
import json
import math
import os
import sys
import time

import unreal

AT = unreal.AssetToolsHelpers.get_asset_tools()
EAL = unreal.EditorAssetLibrary
MEL = unreal.MaterialEditingLibrary


LOG_FILE = None


def log(msg):
    unreal.log(f"[t2s] {msg}")
    if LOG_FILE:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")


# ---------------------------------------------------------------- conversion
def loc(p):
    return unreal.Vector(p[0] * 100.0, -p[1] * 100.0, p[2] * 100.0)


def direction(d):
    return unreal.Vector(d[0], -d[1], d[2])


def rot_from_dir(d, up=None):
    x = direction(d)
    if up is None:
        return unreal.MathLibrary.make_rot_from_x(x)
    return unreal.MathLibrary.make_rot_from_xz(x, direction(up))


def yaw_rot(yaw):
    return unreal.Rotator(roll=0.0, pitch=0.0, yaw=-yaw)


def kelvin_tint(k):
    """Approximate black-body colour (for engines / properties without a temperature input)."""
    t = k / 100.0
    r = 255 if t <= 66 else 329.7 * (t - 60) ** -0.1332
    g = 99.47 * math.log(t) - 161.1 if t <= 66 else 288.1 * (t - 60) ** -0.0755
    b = 255 if t >= 66 else (0 if t <= 19 else 138.5 * math.log(t - 10) - 305.0)
    return unreal.LinearColor(*(max(0.0, min(255.0, c)) / 255.0 for c in (r, g, b)), 1.0)


# ---------------------------------------------------------------- import helpers
def asset_path(folder, name):
    return f"{folder}/{name}.{name}"


def up_to_date(path, src):
    if not EAL.does_asset_exist(path):
        return False
    a = unreal.load_asset(path)
    return a is not None and EAL.get_metadata_tag(a, "t2s_src") == f"{os.path.getmtime(src):.0f}"


def mark(asset, src):
    EAL.set_metadata_tag(asset, "t2s_src", f"{os.path.getmtime(src):.0f}")
    EAL.save_loaded_asset(asset)


def import_task(src, folder, name, options=None):
    task = unreal.AssetImportTask()
    task.filename = src
    task.destination_path = folder
    task.destination_name = name
    task.automated = True
    task.replace_existing = True
    task.save = False
    if options:
        task.options = options
    AT.import_asset_tasks([task])
    paths = list(task.imported_object_paths)
    if not paths:
        raise RuntimeError(f"import failed: {src}")
    return unreal.load_asset(paths[0])


def texture(src, folder, kind):
    """kind: color | normal | masks | data"""
    name = os.path.splitext(os.path.basename(src))[0]
    path = asset_path(folder, name)
    if up_to_date(path, src):
        return unreal.load_asset(path)
    tex = import_task(src, folder, name)
    if kind == "normal":
        tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP)
        tex.set_editor_property("flip_green_channel", True)      # files are OpenGL, Unreal is DirectX
        tex.set_editor_property("srgb", False)
    elif kind in ("masks", "data"):
        tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_MASKS)
        tex.set_editor_property("srgb", False)
    mark(tex, src)
    return tex


def fbx_options():
    ui = unreal.FbxImportUI()
    ui.set_editor_property("import_mesh", True)
    ui.set_editor_property("import_textures", False)
    ui.set_editor_property("import_materials", False)
    ui.set_editor_property("import_as_skeletal", False)
    ui.set_editor_property("import_animations", False)
    ui.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_STATIC_MESH)
    sm = ui.static_mesh_import_data
    sm.set_editor_property("combine_meshes", True)
    sm.set_editor_property("auto_generate_collision", False)
    sm.set_editor_property("generate_lightmap_u_vs", True)
    sm.set_editor_property("convert_scene", True)
    sm.set_editor_property("force_front_x_axis", False)
    sm.set_editor_property("import_uniform_scale", 1.0)
    sm.set_editor_property("normal_import_method", unreal.FBXNormalImportMethod.FBXNIM_IMPORT_NORMALS_AND_TANGENTS)
    return ui


# ---------------------------------------------------------------- master materials
class Graph:
    def __init__(self, mat):
        self.mat = mat
        self.y = 0

    def node(self, cls, x=-600, **props):
        e = MEL.create_material_expression(self.mat, cls, x, self.y)
        self.y += 160
        for k, v in props.items():
            e.set_editor_property(k, v)
        return e

    def scalar(self, name, value, x=-900):
        return self.node(unreal.MaterialExpressionScalarParameter, x, parameter_name=name, default_value=value)

    def vector(self, name, value, x=-900):
        return self.node(unreal.MaterialExpressionVectorParameter, x, parameter_name=name,
                         default_value=unreal.LinearColor(*value))

    def tex(self, name, sampler, default, uv=None, x=-900):
        e = self.node(unreal.MaterialExpressionTextureSampleParameter2D, x, parameter_name=name,
                      sampler_type=sampler, texture=default)
        if uv is not None:
            self.link(uv, "", e, "UVs")
        return e

    def op(self, cls, a, b, ao="", bo="", x=-400):
        e = self.node(cls, x)
        self.link(a, ao, e, "A")
        self.link(b, bo, e, "B")
        return e

    def mask(self, src, out, r=False, g=False, b=False, a=False, x=-500):
        e = self.node(unreal.MaterialExpressionComponentMask, x, r=r, g=g, b=b, a=a)
        self.link(src, out, e, "")
        return e

    @staticmethod
    def link(a, ao, b, bi):
        if not MEL.connect_material_expressions(a, ao, b, bi):
            raise RuntimeError(f"cannot connect {a.get_name()}.{ao} -> {b.get_name()}.{bi}")

    def out(self, e, o, prop):
        if not MEL.connect_material_property(e, o, prop):
            raise RuntimeError(f"cannot connect to {prop}")


class Importer:
    def __init__(self, export_dir, root="/Game/T2S", rebuild_materials=False):
        self.export = os.path.abspath(export_dir)
        self.root = root
        self.rebuild = rebuild_materials
        self.mi = {}           # material id / slot name -> MaterialInstanceConstant
        self.meshes = {}       # asset name -> StaticMesh
        self.assets = {}
        for dirpath, _, files in os.walk(self.export):
            if "asset.json" in files:
                with open(os.path.join(dirpath, "asset.json"), encoding="utf-8") as f:
                    a = json.load(f)
                a["_dir"] = dirpath
                self.assets[a.get("name", a["id"])] = a
        unreal.SystemLibrary.execute_console_command(None, "Interchange.FeatureFlags.Import.FBX 0")

    # ---- shared textures and masters
    def shared(self):
        a = self.assets["shared"]
        d, folder = a["_dir"], f"{self.root}/Shared"
        self.t_macro = texture(os.path.join(d, a["macro"]), folder, "data")
        self.t_detail = texture(os.path.join(d, a["detail_n"]), folder, "normal")
        self.t_bc = texture(os.path.join(d, a["default"]["bc"]), folder, "color")
        self.t_n = texture(os.path.join(d, a["default"]["n"]), folder, "normal")
        self.t_orm = texture(os.path.join(d, a["default"]["orm"]), folder, "masks")

    def master(self, name, build, setup=None):
        folder = f"{self.root}/Master"
        path = asset_path(folder, name)
        if EAL.does_asset_exist(path) and not self.rebuild:
            return unreal.load_asset(path)
        if EAL.does_asset_exist(path):
            EAL.delete_asset(path)
        mat = AT.create_asset(name, folder, unreal.Material, unreal.MaterialFactoryNew())
        if setup:
            setup(mat)
        build(Graph(mat))
        MEL.layout_material_expressions(mat)
        MEL.recompile_material(mat)
        EAL.save_loaded_asset(mat)
        log(f"master material {name}")
        return mat

    def masters(self):
        C, N, M = (unreal.MaterialSamplerType.SAMPLERTYPE_COLOR, unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL,
                   unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)
        P = unreal.MaterialProperty

        def prop(g):
            bc = g.tex("BaseColor", C, self.t_bc)
            n = g.tex("Normal", N, self.t_n)
            orm = g.tex("ORM", M, self.t_orm)
            tint = g.vector("Tint", (1, 1, 1, 1))
            col = g.op(unreal.MaterialExpressionMultiply, bc, tint, "RGB", "")
            rough = g.op(unreal.MaterialExpressionMultiply, orm, g.scalar("RoughnessScale", 1.0), "G", "")
            emis = g.op(unreal.MaterialExpressionMultiply, col, g.scalar("Emissive", 0.0))
            g.out(col, "", P.MP_BASE_COLOR)
            g.out(n, "RGB", P.MP_NORMAL)
            g.out(orm, "R", P.MP_AMBIENT_OCCLUSION)
            g.out(rough, "", P.MP_ROUGHNESS)
            g.out(orm, "B", P.MP_METALLIC)
            g.out(emis, "", P.MP_EMISSIVE_COLOR)

        def surface(g):
            uv = g.op(unreal.MaterialExpressionMultiply, g.node(unreal.MaterialExpressionTextureCoordinate, -1300),
                      g.scalar("Tiling", 0.5, -1300), x=-1100)
            # world-space macro variation: (x + 0.7 z, y + 0.7 z) so floors and walls in both directions vary
            wp = g.node(unreal.MaterialExpressionWorldPosition, -1500)
            xy = g.mask(wp, "", r=True, g=True, x=-1300)
            z = g.op(unreal.MaterialExpressionMultiply, g.mask(wp, "", b=True, x=-1300),
                     g.node(unreal.MaterialExpressionConstant, -1300, r=0.7), x=-1200)
            muv = g.op(unreal.MaterialExpressionDivide, g.op(unreal.MaterialExpressionAdd, xy, z, x=-1100),
                       g.op(unreal.MaterialExpressionMultiply, g.scalar("MacroSize", 8.0, -1300),
                            g.node(unreal.MaterialExpressionConstant, -1300, r=100.0), x=-1200), x=-1000)
            macro = g.tex("Macro", M, self.t_macro, muv)
            strength = g.scalar("MacroStrength", 0.12)
            lo = g.op(unreal.MaterialExpressionSubtract, g.node(unreal.MaterialExpressionConstant, r=1.0), strength)
            hi = g.op(unreal.MaterialExpressionAdd, g.node(unreal.MaterialExpressionConstant, r=1.0), strength)
            lerp = g.node(unreal.MaterialExpressionLinearInterpolate, -300)
            g.link(lo, "", lerp, "A")
            g.link(hi, "", lerp, "B")
            g.link(macro, "R", lerp, "Alpha")
            bc = g.tex("BaseColor", C, self.t_bc, uv)
            col = g.op(unreal.MaterialExpressionMultiply,
                       g.op(unreal.MaterialExpressionMultiply, bc, g.vector("Tint", (1, 1, 1, 1)), "RGB", ""), lerp, x=-200)
            orm = g.tex("ORM", M, self.t_orm, uv)
            rough = g.op(unreal.MaterialExpressionAdd,
                         g.op(unreal.MaterialExpressionMultiply, orm, g.scalar("RoughnessScale", 1.0), "G", ""),
                         g.op(unreal.MaterialExpressionMultiply,
                              g.op(unreal.MaterialExpressionSubtract, macro, g.node(unreal.MaterialExpressionConstant, r=0.5), "G", ""),
                              strength), x=-200)
            # detail normal, whiteout blend: normalize(n1.xy + s * n2.xy, n1.z)
            n1 = g.tex("Normal", N, self.t_n, uv)
            duv = g.op(unreal.MaterialExpressionMultiply, uv, g.scalar("DetailTiling", 4.0))
            n2 = g.tex("DetailNormal", N, self.t_detail, duv)
            xy2 = g.op(unreal.MaterialExpressionMultiply, g.mask(n2, "RGB", r=True, g=True), g.scalar("DetailStrength", 0.4))
            xy = g.op(unreal.MaterialExpressionAdd, g.mask(n1, "RGB", r=True, g=True), xy2)
            app = g.node(unreal.MaterialExpressionAppendVector, -250)
            g.link(xy, "", app, "A")
            g.link(n1, "B", app, "B")
            nrm = g.node(unreal.MaterialExpressionNormalize, -150)
            g.link(app, "", nrm, "")
            g.out(col, "", P.MP_BASE_COLOR)
            g.out(nrm, "", P.MP_NORMAL)
            g.out(orm, "R", P.MP_AMBIENT_OCCLUSION)
            g.out(rough, "", P.MP_ROUGHNESS)
            g.out(orm, "B", P.MP_METALLIC)

        def glass(g):
            g.out(g.vector("Tint", (0.9, 0.95, 1.0, 1)), "", P.MP_BASE_COLOR)
            g.out(g.scalar("Opacity", 0.15), "", P.MP_OPACITY)
            g.out(g.scalar("Roughness", 0.03), "", P.MP_ROUGHNESS)
            g.out(g.scalar("Specular", 0.5), "", P.MP_SPECULAR)

        def glass_setup(mat):
            mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
            mat.set_editor_property("two_sided", True)
            mat.set_editor_property("translucency_lighting_mode",
                                    unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING)

        def decal(g):
            bc = g.tex("BaseColor", C, self.t_bc)
            n = g.tex("Normal", N, self.t_n)
            g.out(bc, "RGB", P.MP_BASE_COLOR)
            g.out(g.op(unreal.MaterialExpressionMultiply, bc, g.scalar("Opacity", 1.0), "A", ""), "", P.MP_OPACITY)
            g.out(n, "RGB", P.MP_NORMAL)
            g.out(g.scalar("Roughness", 0.6), "", P.MP_ROUGHNESS)

        def decal_setup(mat):
            mat.set_editor_property("material_domain", unreal.MaterialDomain.MD_DEFERRED_DECAL)
            mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)

        self.m_prop = self.master("M_T2S_Prop", prop)
        self.m_surface = self.master("M_T2S_Surface", surface)
        self.m_glass = self.master("M_T2S_Glass", glass, glass_setup)
        self.m_decal = self.master("M_T2S_Decal", decal, decal_setup)

    def instance(self, name, folder, parent, textures=None, scalars=None, vectors=None):
        path = asset_path(folder, name)
        mi = unreal.load_asset(path) if EAL.does_asset_exist(path) else \
            AT.create_asset(name, folder, unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew())
        MEL.set_material_instance_parent(mi, parent)
        for k, v in (textures or {}).items():
            MEL.set_material_instance_texture_parameter_value(mi, k, v)
        for k, v in (scalars or {}).items():
            MEL.set_material_instance_scalar_parameter_value(mi, k, float(v))
        for k, v in (vectors or {}).items():
            MEL.set_material_instance_vector_parameter_value(mi, k, unreal.LinearColor(*(list(v) + [1.0])[:4]))
        EAL.save_loaded_asset(mi)
        return mi

    # ---- materials, decals, meshes
    def materials(self):
        for a in self.assets.values():
            if a["kind"] != "material":
                continue
            folder, d, t = f"{self.root}/Materials/{a['id']}", a["_dir"], a["textures"]
            prm = a.get("params", {})
            if a["shader"] == "glass":
                self.mi[a["id"]] = self.instance(f"MI_{a['id']}", folder, self.m_glass,
                                                 scalars={"Opacity": prm.get("opacity", 0.15),
                                                          "Roughness": prm.get("roughness", 0.03)},
                                                 vectors={"Tint": prm.get("color", [0.9, 0.95, 1.0])})
                continue
            texs = {"BaseColor": texture(os.path.join(d, t["bc"]), folder, "color"),
                    "Normal": texture(os.path.join(d, t["n"]), folder, "normal"),
                    "ORM": texture(os.path.join(d, t["orm"]), folder, "masks")}
            self.mi[a["id"]] = self.instance(f"MI_{a['id']}", folder, self.m_surface, texs,
                                             {"Tiling": 1.0 / a.get("tile_m", 2.0), "MacroStrength": prm.get("macro", 0.12),
                                              "DetailStrength": prm.get("detail", 0.4)})
            log(f"material {a['id']}")

    def decals(self):
        for a in self.assets.values():
            if a["kind"] != "decal":
                continue
            folder, d, t = f"{self.root}/Decals/{a['id']}", a["_dir"], a["textures"]
            texs = {"BaseColor": texture(os.path.join(d, t["bc"]), folder, "color"),
                    "Normal": texture(os.path.join(d, t["n"]), folder, "normal")}
            self.mi[a["id"]] = self.instance(f"MI_{a['id']}", folder, self.m_decal, texs,
                                             {"Roughness": a["params"]["roughness"], "Opacity": a["params"]["opacity"]})

    def slot_material(self, a, slot, folder):
        if "material" in slot:
            return self.mi.get(slot["material"])
        name = "MI_" + slot["name"][2:] if slot["name"].startswith("M_") else "MI_" + slot["name"]
        texs = {}
        for key, param, kind in (("bc", "BaseColor", "color"), ("n", "Normal", "normal"), ("orm", "ORM", "masks")):
            f = (slot.get("textures") or {}).get(key)
            if f:
                texs[param] = texture(os.path.join(a["_dir"], f), folder, kind)
        prm = slot.get("params", {})
        scalars = {"Emissive": prm.get("emissive", 0.0)}
        if "roughness" in prm:
            scalars["RoughnessScale"] = prm["roughness"] / 0.5           # default ORM roughness is 0.5
        vectors = {"Tint": prm["tint"]} if "tint" in prm else {}
        mi = self.instance(name, folder, self.m_prop, texs, scalars, vectors)
        self.mi[slot["name"]] = mi
        return mi

    def meshes_import(self, nanite_default=True):
        sms = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
        for name, a in sorted(self.assets.items()):
            if a["kind"] not in ("prop", "kit"):
                continue
            folder = f"{self.root}/{'Props' if a['kind'] == 'prop' else 'Kit'}/{name}"
            src = os.path.join(a["_dir"], a["lods"][0]["file"])
            if not os.path.exists(src):
                log(f"{name}: {src} missing, skipped")
                continue
            path = asset_path(folder, name)
            fresh = not up_to_date(path, src)
            mesh = import_task(src, folder, name, fbx_options()) if fresh else unreal.load_asset(path)
            nanite = nanite_default and a.get("nanite", True)
            if fresh:
                ns = mesh.get_editor_property("nanite_settings")
                ns.set_editor_property("enabled", nanite)
                mesh.set_editor_property("nanite_settings", ns)
                if not nanite:
                    for i, lod in enumerate(a["lods"][1:], 1):
                        sms.import_lod(mesh, i, os.path.join(a["_dir"], lod["file"]))
            for slot in a.get("slots", []):
                mi = self.slot_material(a, slot, folder)
                idx = mesh.get_material_index(slot["name"])
                if mi and idx >= 0:
                    mesh.set_material(idx, mi)
            mark(mesh, src)
            self.meshes[name] = mesh
            if fresh:
                log(f"mesh {name} (nanite={nanite}, lods={mesh.get_num_lods()})")

    def calibrate(self):
        """Import SM_T2S_Axes and check that +X / +Y / +Z (1 / 2 / 3 m) arrive where loc() expects them."""
        a = self.assets.get("SM_T2S_Axes")
        if a is None:
            return
        mesh = import_task(os.path.join(a["_dir"], a["lods"][0]["file"]), f"{self.root}/Kit/SM_T2S_Axes",
                           "SM_T2S_Axes", fbx_options())
        box = mesh.get_bounding_box()
        lo, hi = loc(a["bounds_min"]), loc(a["bounds_max"])
        want_lo = [min(getattr(lo, c), getattr(hi, c)) for c in "xyz"]
        want_hi = [max(getattr(lo, c), getattr(hi, c)) for c in "xyz"]
        got_lo = [getattr(box.min, c) for c in "xyz"]
        got_hi = [getattr(box.max, c) for c in "xyz"]
        ok = all(abs(g - w) < 2.0 for g, w in zip(got_lo + got_hi, want_lo + want_hi))
        msg = f"axis calibration {'OK' if ok else 'MISMATCH'}: bounds {got_lo} .. {got_hi}, expected {want_lo} .. {want_hi}"
        log(msg)
        if not ok:
            raise RuntimeError(msg + " (fix loc()/yaw_rot() or the FBX export axes before building levels)")

    def check_project(self):
        """Warn about project settings a Lumen level needs (project templates enable them by default)."""
        sl = unreal.SystemLibrary
        if not sl.get_console_variable_bool_value("r.GenerateMeshDistanceFields"):
            log("WARNING project setting Generate Mesh Distance Fields is off: Lumen has no GI data "
                "(ceilings and shadowed areas render black). Enable it in Project Settings > Rendering.")
        if sl.get_console_variable_int_value("r.DynamicGlobalIlluminationMethod") != 1:
            log("WARNING project GI method is not Lumen; the level's post process volume overrides it, "
                "but enable Lumen in Project Settings > Rendering for consistent results.")

    def import_all(self, nanite=True):
        self.check_project()
        self.calibrate()
        self.shared()
        self.masters()
        self.materials()
        self.decals()
        self.meshes_import(nanite)
        unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)

    # ---------------------------------------------------------------- level
    def level(self):
        with open(os.path.join(self.export, "level.json"), encoding="utf-8") as f:
            lv = json.load(f)
        les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        path = f"{self.root}/Maps/{lv['name']}"
        if EAL.does_asset_exist(path):
            les.load_level(path)
        else:
            les.new_level(path)
        for a in eas.get_all_level_actors():
            if "T2S" in [str(t) for t in a.tags]:
                eas.destroy_actor(a)
        if not self.meshes:
            for name, a in self.assets.items():
                if a["kind"] in ("prop", "kit"):
                    folder = f"{self.root}/{'Props' if a['kind'] == 'prop' else 'Kit'}/{name}"
                    if EAL.does_asset_exist(asset_path(folder, name)):
                        self.meshes[name] = unreal.load_asset(asset_path(folder, name))
            for a in self.assets.values():
                if a["kind"] in ("material", "decal"):
                    folder = f"{self.root}/{'Materials' if a['kind'] == 'material' else 'Decals'}/{a['id']}"
                    p = asset_path(folder, f"MI_{a['id']}")
                    if EAL.does_asset_exist(p):
                        self.mi[a["id"]] = unreal.load_asset(p)

        def tag(actor, name, folder):
            actor.set_actor_label(name)
            actor.set_folder_path(f"T2S/{folder}")
            actor.tags = ["T2S"]
            return actor

        missing = set()
        for inst in lv["instances"]:
            mesh = self.meshes.get(inst["asset"])
            if mesh is None:
                missing.add(inst["asset"])
                continue
            actor = eas.spawn_actor_from_object(mesh, loc(inst["pos"]), yaw_rot(inst["yaw"]))
            s = inst.get("scale", 1.0)
            actor.set_actor_scale3d(unreal.Vector(s, s, s))
            comp = actor.static_mesh_component
            comp.set_mobility(unreal.ComponentMobility.STATIC)
            for slot, mid in inst.get("materials", {}).items():
                idx = comp.get_material_index(slot)
                if idx >= 0 and mid in self.mi:
                    comp.set_material(idx, self.mi[mid])
            tag(actor, inst["name"], f"{inst['room']}/{inst['kind']}")
        if missing:
            log(f"not imported, skipped: {sorted(missing)}")

        mobility = {"static": unreal.ComponentMobility.STATIC, "stationary": unreal.ComponentMobility.STATIONARY,
                    "movable": unreal.ComponentMobility.MOVABLE}
        dynamic = lv.get("lighting", {}).get("gi", "dynamic") == "dynamic"
        for l in lv["lights"]:
            cls = {"point": unreal.PointLight, "spot": unreal.SpotLight, "rect": unreal.RectLight}[l["type"]]
            actor = eas.spawn_actor_from_class(cls, loc(l["pos"]), rot_from_dir(l["dir"]))
            comp = actor.get_component_by_class(unreal.LocalLightComponent)
            # with Lumen nothing is baked: static / stationary lights would stay dark until a lighting build
            comp.set_mobility(unreal.ComponentMobility.MOVABLE if dynamic else mobility[l["mobility"]])
            comp.set_editor_property("intensity_units", unreal.LightUnits.LUMENS)
            comp.set_intensity(l["lumens"])
            comp.set_attenuation_radius(l["radius"] * 100)
            comp.set_editor_property("use_temperature", True)
            comp.set_temperature(l["kelvin"])
            comp.set_cast_shadows(bool(l["shadows"]))
            comp.set_volumetric_scattering_intensity(l.get("volumetric", 1.0))
            if l.get("color"):
                comp.set_light_color(unreal.LinearColor(*l["color"], 1.0))
            if l["type"] == "spot":
                comp.set_outer_cone_angle(l["cone"] / 2)
                comp.set_inner_cone_angle(l["cone"] / 3)
            if l["type"] == "rect":
                comp.set_source_width(l["size"][0] * 100)
                comp.set_source_height(l["size"][1] * 100)
            tag(actor, l["name"], f"{l['room']}/Lights")

        self.environment(lv, eas, tag)

        for v in lv["volumes"]:
            if v["outdoor"]:
                continue
            c = [(v["min"][i] + v["max"][i]) / 2 for i in range(3)]
            half = [(v["max"][i] - v["min"][i]) / 2 for i in range(3)]
            actor = eas.spawn_actor_from_class(unreal.SphereReflectionCapture, loc(c), unreal.Rotator())
            actor.capture_component.set_editor_property("influence_radius", math.sqrt(sum(h * h for h in half)) * 100)
            tag(actor, f"{v['name']}_Reflection", f"{v['room']}/Volumes")

        for d in lv["decals"]:
            mi = self.mi.get(d["asset"])
            if mi is None:
                missing.add(d["asset"])
                continue
            n = d["normal"]
            actor = eas.spawn_actor_from_class(unreal.DecalActor, loc(d["pos"]),
                                               rot_from_dir([-n[0], -n[1], -n[2]], d["up"]))
            actor.set_decal_material(mi)
            actor.decal.set_editor_property("decal_size", unreal.Vector(d["depth"] * 50, d["size"][0] * 50, d["size"][1] * 50))
            actor.decal.set_editor_property("sort_order", d.get("sort", 0))
            tag(actor, d["name"], f"{d['room']}/Decals")

        cams = {}
        for c in lv["cameras"]:
            actor = eas.spawn_actor_from_class(unreal.CameraActor, loc(c["pos"]), rot_from_dir(c["dir"]))
            actor.camera_component.set_editor_property("field_of_view", c["fov"])
            cams[c["id"]] = tag(actor, f"Cam_{c['id']}", f"{c['room']}/Cameras")
        if lv.get("player_start"):
            ps = lv["player_start"]
            p = list(ps["pos"])
            p[2] += 0.92
            tag(eas.spawn_actor_from_class(unreal.PlayerStart, loc(p), yaw_rot(ps["yaw"])), "PlayerStart", "Gameplay")
        les.save_current_level()
        unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
        log(f"level {path}: {len(lv['instances'])} instances, {len(lv['lights'])} lights, {len(lv['decals'])} decals")
        return cams, eas, lv

    def environment(self, lv, eas, tag):
        L = lv.get("lighting", {})
        dynamic = L.get("gi", "dynamic") == "dynamic"
        sun = L.get("sun")
        if sun:
            actor = eas.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 2000), rot_from_dir(sun["dir"]))
            comp = actor.get_component_by_class(unreal.DirectionalLightComponent)
            comp.set_mobility(unreal.ComponentMobility.MOVABLE if dynamic else unreal.ComponentMobility.STATIONARY)
            comp.set_intensity(sun.get("lux", 75000))          # physical lux; auto exposure brings it into range
            comp.set_editor_property("use_temperature", True)
            comp.set_temperature(sun.get("kelvin", 5800))
            comp.set_cast_shadows(sun.get("shadows", True))
            comp.set_editor_property("atmosphere_sun_light", True)
            tag(actor, "Sun", "Environment")
        if L.get("sky", True):
            tag(eas.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(), unreal.Rotator()), "SkyAtmosphere", "Environment")
            sky = eas.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 2000), unreal.Rotator())
            comp = sky.get_component_by_class(unreal.SkyLightComponent)
            comp.set_mobility(unreal.ComponentMobility.MOVABLE if dynamic else unreal.ComponentMobility.STATIONARY)
            comp.set_editor_property("real_time_capture", dynamic)
            tag(sky, "SkyLight", "Environment")
        fog = L.get("fog")
        if fog:
            actor = eas.spawn_actor_from_class(unreal.ExponentialHeightFog, unreal.Vector(), unreal.Rotator())
            comp = actor.get_component_by_class(unreal.ExponentialHeightFogComponent)
            comp.set_fog_density(fog.get("density", 0.02))
            comp.set_fog_height_falloff(fog.get("height_falloff", 0.2))
            comp.set_volumetric_fog(fog.get("volumetric", True))
            if fog.get("color"):
                comp.set_fog_inscattering_color(unreal.LinearColor(*fog["color"], 1.0))
            tag(actor, "HeightFog", "Environment")
        post = L.get("post", {})
        ppv = eas.spawn_actor_from_class(unreal.PostProcessVolume, unreal.Vector(), unreal.Rotator())
        ppv.set_editor_property("unbound", True)
        s = ppv.get_editor_property("settings")

        def setp(name, value):
            s.set_editor_property(f"override_{name}", True)
            s.set_editor_property(name, value)

        setp("dynamic_global_illumination_method", unreal.DynamicGlobalIlluminationMethod.LUMEN if dynamic
             else unreal.DynamicGlobalIlluminationMethod.NONE)
        setp("reflection_method", unreal.ReflectionMethod.LUMEN if dynamic else unreal.ReflectionMethod.SCREEN_SPACE)
        if "exposure" in post:
            lo, hi = post["exposure"]
            setp("auto_exposure_min_brightness", float(lo))
            setp("auto_exposure_max_brightness", float(hi))
        if "bloom" in post:
            setp("bloom_intensity", float(post["bloom"]))
        if "vignette" in post:
            setp("vignette_intensity", float(post["vignette"]))
        if "grain" in post:
            setp("film_grain_intensity", float(post["grain"]))
        if "saturation" in post:
            setp("color_saturation", unreal.Vector4(1, 1, 1, float(post["saturation"])))
        if "contrast" in post:
            setp("color_contrast", unreal.Vector4(1, 1, 1, float(post["contrast"])))
        if "white_balance" in post:
            setp("white_temp", float(post["white_balance"]))
        if "ao" in post:
            setp("ambient_occlusion_intensity", float(post["ao"]))
        ppv.set_editor_property("settings", s)
        tag(ppv, "PostProcess", "Environment")
        if not dynamic and lv["volumes"]:
            lo = [min(v["min"][i] for v in lv["volumes"]) for i in range(3)]
            hi = [max(v["max"][i] for v in lv["volumes"]) for i in range(3)]
            c = [(lo[i] + hi[i]) / 2 for i in range(3)]
            imp = eas.spawn_actor_from_class(unreal.LightmassImportanceVolume, loc(c), unreal.Rotator())
            imp.set_actor_scale3d(unreal.Vector(*[(hi[i] - lo[i]) / 2 + 1 for i in range(3)]))
            tag(imp, "LightmassImportance", "Environment")

    def shots(self, cams, eas, lv, done):
        """Screenshot the level viewport from every level camera (needs the editor UI, not a commandlet).

        Shader compilation and texture streaming are flushed first; then, from a tick callback, the viewport
        flies to each camera, waits 4 s so auto exposure settles, and takes a high-res screenshot."""
        out = os.path.join(self.export, "shots", "unreal")
        os.makedirs(out, exist_ok=True)
        unreal.AutomationLibrary.finish_loading_before_screenshot()
        les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        les.editor_set_viewport_realtime(True)
        order = list(cams.items())
        state = {"i": -1, "next": 0.0, "t0": time.time()}

        key = les.get_active_viewport_config_key()

        def tick(_dt):
            now = time.time()
            if now < state["next"]:
                return
            try:
                step(now)
            except Exception:
                import traceback
                unreal.unregister_slate_post_tick_callback(state["handle"])
                log("shots FAILED\n" + traceback.format_exc())
                done()

        def step(now):
            if state["i"] >= 0 and not state.get("shot"):
                # the request is served on a later frame: keep the viewport still until it is written
                cid, _ = order[state["i"]]
                unreal.AutomationLibrary.take_high_res_screenshot(1600, 900, os.path.join(out, f"{cid}.png"))
                state["shot"] = True
                state["next"] = now + 1.5
                return
            state["shot"] = False
            state["i"] += 1
            if state["i"] >= len(order):
                unreal.unregister_slate_post_tick_callback(state["handle"])
                state["handle"] = unreal.register_slate_post_tick_callback(finish_tick)
                state["next"] = now
                return
            cam = order[state["i"]][1]
            les.set_level_viewport_camera_info(cam.get_actor_location(), cam.get_actor_rotation(), key)
            les.set_level_viewport_fov(cam.camera_component.field_of_view, key)
            state["next"] = now + 4

        def finish_tick(_dt):
            if time.time() < state["next"]:
                return
            unreal.unregister_slate_post_tick_callback(state["handle"])
            log(f"shots -> {out} ({len(order)} cameras, {time.time() - state['t0']:.0f} s)")
            done()

        state["handle"] = unreal.register_slate_post_tick_callback(tick)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", required=True)
    ap.add_argument("--root", default="/Game/T2S")
    ap.add_argument("--assets-only", action="store_true")
    ap.add_argument("--level-only", action="store_true")
    ap.add_argument("--shots", action="store_true")
    ap.add_argument("--no-nanite", action="store_true")
    ap.add_argument("--rebuild-materials", action="store_true")
    ap.add_argument("--exit-when-done", action="store_true")   # not "--quit": UE reads -quit anywhere on its command line
    a = ap.parse_args(argv)
    global LOG_FILE
    LOG_FILE = os.path.join(a.export, "unreal_import.log")
    open(LOG_FILE, "w").close()
    def finish():
        log("finished")
        if a.exit_when_done:
            unreal.SystemLibrary.quit_editor()

    try:
        imp = Importer(a.export, a.root, a.rebuild_materials)
        if not a.level_only:
            imp.import_all(nanite=not a.no_nanite)
        if a.assets_only:
            return finish()
        cams, eas, lv = imp.level()
        if a.shots:
            return imp.shots(cams, eas, lv, finish)    # finishes from a tick callback
        finish()
    except Exception:
        import traceback
        log("FAILED\n" + traceback.format_exc())
        if a.exit_when_done:
            unreal.SystemLibrary.quit_editor()
        raise


main(sys.argv[1:])
