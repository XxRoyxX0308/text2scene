"""Tileable surface materials -> export/materials/<id>/ (canonical BC / N / ORM / MASK + asset.json).

  python 10_materials.py search <keyword> [more]          search Poly Haven and ambientCG (both CC0)
  python 10_materials.py size <source> [1k|2k|4k]         files and total size of one source, WITHOUT downloading
  python 10_materials.py build <project_dir> [id ...]     download / generate every material in assets.json

assets.json "materials" entries:
  {"id": "concrete_raw", "source": "polyhaven:concrete_wall_008", "res": "2k", "tile_m": 2.0}
  {"id": "terrazzo",     "source": "ambientcg:Terrazzo009",       "res": "2k", "tile_m": 1.5}
  {"id": "paint_white",  "source": "flat", "color": [0.80, 0.80, 0.78], "roughness": 0.45, "metallic": 0}
  {"id": "glass_clear",  "source": "glass", "color": [0.92, 0.96, 1.0], "opacity": 0.12, "roughness": 0.03}
optional on any surface: "tint" [r,g,b] multiplier, "roughness_scale", "macro" (0-0.4, world-space brightness
variation the engine adds against tiling), "detail" (0-1 detail normal strength), "normal_strength".

Scanned sources are never used raw: the albedo is clamped into the plausible range (textures.clamp_albedo) and
"flat" surfaces still get a low-contrast roughness and colour variation, because a perfectly uniform surface is
what makes a scene look like plastic. Downloads go to work/materials/<id>/; tell the user what and how big
before downloading anything above 2k. Standard library + numpy + Pillow.
"""
import io
import json
import os
import sys
import urllib.parse
import urllib.request
import zipfile
import zlib

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import textures as tx
from t2s import Project, save

UA = {"User-Agent": "text2scene-pipeline"}
PH_API = "https://api.polyhaven.com"
ACG_API = "https://ambientcg.com/api/v2/full_json"
# canonical map key -> Poly Haven file key / ambientCG file suffix
PH_MAPS = {"color": "Diffuse", "normal": "nor_gl", "rough": "Rough", "ao": "AO", "metal": "Metal", "height": "Displacement"}
ACG_MAPS = {"color": "_Color", "normal": "_NormalGL", "rough": "_Roughness", "ao": "_AmbientOcclusion",
            "metal": "_Metalness", "height": "_Displacement"}


def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA)) as r:
        return r.read()


def ph_files(asset, res):
    info = json.loads(fetch(f"{PH_API}/files/{asset}"))
    out = {}
    for key, ph in PH_MAPS.items():
        entry = info.get(ph, {}).get(res, {})
        f = entry.get("png") if key == "height" else None
        f = f or entry.get("jpg") or entry.get("png")
        if f:
            out[key] = f
    return out


def acg_zip(asset, res):
    q = urllib.parse.urlencode({"id": asset, "include": "downloadData"})
    found = json.loads(fetch(f"{ACG_API}?{q}"))["foundAssets"]
    if not found:
        raise KeyError(f"ambientCG has no asset {asset}")
    downloads = found[0]["downloadFolders"]["default"]["downloadFiletypeCategories"]["zip"]["downloads"]
    want = f"{res.upper()}-JPG"
    for d in downloads:
        if d["attribute"] == want:
            return d
    raise KeyError(f"{asset}: no {want} download (have {[d['attribute'] for d in downloads]})")


def search(words):
    words = [w.lower() for w in words]
    assets = json.loads(fetch(f"{PH_API}/assets?t=textures"))
    hits = []
    for aid, a in assets.items():
        hay = " ".join([aid, a.get("name", "")] + a.get("categories", []) + a.get("tags", [])).lower()
        if all(w in hay for w in words):
            hits.append((a.get("download_count", 0), aid, a.get("name", "")))
    print("Poly Haven:")
    for _, aid, name in sorted(hits, reverse=True)[:15]:
        print(f"  polyhaven:{aid:34s} {name}")
    q = urllib.parse.urlencode({"type": "Material", "q": " ".join(words), "limit": 15, "sort": "Popular"})
    print("ambientCG:")
    for a in json.loads(fetch(f"{ACG_API}?{q}")).get("foundAssets", []):
        print(f"  ambientcg:{a['assetId']:34s} {a.get('displayName', '')}")


def download(source, res, dest):
    """Return {map key: local path} for a scanned source, downloading only what is missing."""
    lib, asset = source.split(":", 1)
    os.makedirs(dest, exist_ok=True)
    paths = {}
    if lib == "polyhaven":
        for key, f in ph_files(asset, res).items():
            path = os.path.join(dest, f"{key}{os.path.splitext(f['url'])[1]}")
            if not os.path.exists(path) or os.path.getsize(path) != f["size"]:
                open(path, "wb").write(fetch(f["url"]))
            paths[key] = path
        url = f"https://polyhaven.com/a/{asset}"
    elif lib == "ambientcg":
        marker = os.path.join(dest, f"{asset}_{res}.done")
        if not os.path.exists(marker):
            z = zipfile.ZipFile(io.BytesIO(fetch(acg_zip(asset, res)["downloadLink"])))
            for name in z.namelist():
                for key, suffix in ACG_MAPS.items():
                    if os.path.splitext(name)[0].endswith(suffix):
                        open(os.path.join(dest, key + os.path.splitext(name)[1]), "wb").write(z.read(name))
            open(marker, "w").close()
        for key in ACG_MAPS:
            for ext in (".jpg", ".png"):
                if os.path.exists(os.path.join(dest, key + ext)):
                    paths[key] = os.path.join(dest, key + ext)
        url = f"https://ambientcg.com/view?id={asset}"
    else:
        raise ValueError(f"unknown source library {lib}")
    open(os.path.join(dest, "SOURCE.txt"), "w").write(f"{url}\nLicense: CC0\n")
    return paths


def build_scanned(m, work, out):
    res = m.get("res", "2k")
    maps = download(m["source"], res, work)
    if "color" not in maps:
        raise RuntimeError(f"{m['id']}: source has no colour map")
    bc = tx.load(maps["color"]) * np.array(m.get("tint", [1, 1, 1]), np.float32)
    normal = tx.load(maps["normal"]) if "normal" in maps else None
    if normal is not None and m.get("normal_strength", 1.0) != 1.0:
        xy = (normal[..., :2] - 0.5) * m["normal_strength"]
        z = np.sqrt(np.clip(1 - (xy ** 2).sum(-1, keepdims=True), 0, 1))
        normal = np.concatenate([xy + 0.5, z * 0.5 + 0.5], axis=-1)
    rough = tx.load(maps["rough"], 1) * m.get("roughness_scale", 1.0) if "rough" in maps else 0.7
    ao = tx.load(maps["ao"], 1) if "ao" in maps else 1.0
    metal = tx.load(maps["metal"], 1) if "metal" in maps else float(m.get("metallic", 0.0))
    return tx.write_maps(out, m["id"], bc, normal, ao, rough, metal), bc.shape[1]


def build_flat(m, out, size=512):
    """Painted / coated surface: a base colour and roughness with subtle tileable variation."""
    var = float(m.get("noise", 0.06))
    n1 = tx.tileable_noise(size, size / 3, seed=zlib.crc32(m["id"].encode()))
    n2 = tx.tileable_noise(size, size / 24, seed=zlib.crc32(m["id"].encode()) + 1)
    color = np.array(m.get("color", [0.7, 0.7, 0.7]), np.float32)
    bc = color[None, None, :] * (1 + (n1[..., None] - 0.5) * var * 2)
    rough = np.clip(m.get("roughness", 0.5) + (n2 - 0.5) * var * 2 + (n1 - 0.5) * var * 3, 0.03, 1)
    normal = tx.normal_from_height(n2, strength=float(m.get("normal_strength", 0.6)))
    return tx.write_maps(out, m["id"], bc, normal, 1.0, rough, float(m.get("metallic", 0.0))), size


def build(project, only):
    for m in project.assets.get("materials", []):
        if only and m["id"] not in only:
            continue
        out = project.export("materials", m["id"])
        work = project.p("work", "materials", m["id"])
        shader = "glass" if m["source"] == "glass" else "surface"
        if m["source"] in ("flat", "glass"):
            files, px = build_flat(m, out, 256 if shader == "glass" else 512)
        else:
            files, px = build_scanned(m, work, out)
        tile = float(m.get("tile_m", 2.0))
        info = {"kind": "material", "id": m["id"], "shader": shader, "textures": files, "tile_m": tile,
                "texel_density": round(px / tile), "source": m["source"],
                "params": {k: m[k] for k in ("macro", "detail", "opacity", "roughness", "color") if k in m}}
        info["params"].setdefault("macro", 0.12 if shader == "surface" else 0.0)
        info["params"].setdefault("detail", 0.4 if shader == "surface" else 0.0)
        save(os.path.join(out, "asset.json"), info)
        warn = "  (below 512 px/m: will look soft up close)" if info["texel_density"] < 512 and ":" in m["source"] else ""
        module = float(project.assets.get("kit", {}).get("module", 2.0))
        if shader == "surface" and abs(module / tile - round(module / tile)) > 1e-6:
            warn += f"  (tile_m {tile} does not divide the {module} m kit module: visible seams every module)"
        print(f"{m['id']}: {m['source']} -> {out}  {info['texel_density']} px/m{warn}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "search":
        search(sys.argv[2:])
    elif cmd == "size":
        src, res = sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else "1k")
        lib, asset = src.split(":", 1)
        if lib == "polyhaven":
            fs = ph_files(asset, res)
            for k, f in fs.items():
                print(f"{k:8s} {f['size'] / 1e6:6.2f} MB")
            print(f"total {sum(f['size'] for f in fs.values()) / 1e6:.2f} MB at {res}")
        else:
            print(f"zip {acg_zip(asset, res)['size'] / 1e6:.2f} MB at {res}")
    elif cmd == "build":
        build(Project(sys.argv[2]), set(sys.argv[3:]))
    else:
        sys.exit(__doc__)
