"""Find and download CC0 PBR textures from Poly Haven (https://polyhaven.com, public API, no account).

  python3 polyhaven.py search <keyword> [more keywords]     list matching texture ids (name, categories, tags)
  python3 polyhaven.py size <asset_id> [1k|2k|4k]           show the files and total size WITHOUT downloading
  python3 polyhaven.py get <project_dir> <asset_id> [1k|2k] download diffuse / normal(GL) / roughness maps to
                                                            <project_dir>/work/textures/<asset_id>/

Standard library only. Report the size to the user before running `get` on anything large (2k+).
"""
import json
import os
import sys
import urllib.request

API = "https://api.polyhaven.com"
MAPS = {"Diffuse": "diff", "nor_gl": "nor_gl", "Rough": "rough"}
UA = {"User-Agent": "text2scene-pipeline"}


def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA)) as r:
        return r.read()


def files_for(asset, res):
    info = json.loads(fetch(f"{API}/files/{asset}"))
    out = {}
    for key, short in MAPS.items():
        entry = info.get(key, {}).get(res, {})
        f = entry.get("jpg") or entry.get("png")
        if f:
            out[short] = f
    return out


cmd = sys.argv[1]
if cmd == "search":
    words = [w.lower() for w in sys.argv[2:]]
    assets = json.loads(fetch(f"{API}/assets?t=textures"))
    hits = []
    for aid, a in assets.items():
        hay = " ".join([aid, a.get("name", "")] + a.get("categories", []) + a.get("tags", [])).lower()
        if all(w in hay for w in words):
            hits.append((a.get("download_count", 0), aid, a.get("name", ""), ",".join(a.get("categories", [])[:4])))
    for dl, aid, name, cats in sorted(hits, reverse=True)[:25]:
        print(f"{aid:32s} {name:32s} [{cats}]")
    print(f"{len(hits)} matches (top 25 by downloads shown)")
elif cmd == "size":
    asset, res = sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else "1k")
    fs = files_for(asset, res)
    for short, f in fs.items():
        print(f"{short:8s} {f['size'] / 1e6:6.2f} MB  {f['url']}")
    print(f"total {sum(f['size'] for f in fs.values()) / 1e6:.2f} MB at {res}")
elif cmd == "get":
    project, asset = sys.argv[2], sys.argv[3]
    res = sys.argv[4] if len(sys.argv) > 4 else "1k"
    out_dir = os.path.join(project, "work", "textures", asset)
    os.makedirs(out_dir, exist_ok=True)
    for short, f in files_for(asset, res).items():
        path = os.path.join(out_dir, f"{asset}_{short}_{res}{os.path.splitext(f['url'])[1]}")
        if not os.path.exists(path) or os.path.getsize(path) != f["size"]:
            open(path, "wb").write(fetch(f["url"]))
        print(f"{path}  {os.path.getsize(path) / 1e6:.2f} MB")
    open(os.path.join(out_dir, "SOURCE.txt"), "w").write(f"https://polyhaven.com/a/{asset}\nLicense: CC0\n")
else:
    sys.exit(__doc__)
