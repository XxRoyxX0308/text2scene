"""Texture helpers shared by the material, decal, shared-texture and prop steps (numpy + Pillow).

Canonical maps written to export/ (engines convert nothing except what their importer settings do):
  T_<name>_BC.png    base colour, sRGB (RGBA for decals)
  T_<name>_N.png     tangent-space normal, OpenGL convention (+Y up); Unreal flips green on import
  T_<name>_ORM.png   linear, R = ambient occlusion, G = roughness, B = metallic          (Unreal)
  T_<name>_MASK.png  linear, R = metallic, G = occlusion, B = detail mask, A = smoothness (Unity URP Lit /
                     HDRP Lit mask map / Built-in Standard all read these channels)
Blender can import this module too (it ships numpy); keep it free of other dependencies besides Pillow, and
only import Pillow inside the functions that need it.
"""
import os

import numpy as np

ALBEDO_SRGB_RANGE = (30 / 255, 240 / 255)   # real-world albedo: never pure black, never pure white
ALBEDO_MAX_SATURATION = 0.85


def load(path, channels=3):
    from PIL import Image
    im = Image.open(path)
    mode = {1: "L", 3: "RGB", 4: "RGBA"}[channels]
    a = np.asarray(im.convert(mode), dtype=np.float32) / 255.0
    return a if channels > 1 else a[:, :, None]


def save(path, a):
    from PIL import Image
    os.makedirs(os.path.dirname(path), exist_ok=True)
    a = np.clip(a, 0, 1)
    if a.ndim == 3 and a.shape[2] == 1:
        a = a[:, :, 0]
    Image.fromarray((a * 255 + 0.5).astype(np.uint8)).save(path)
    return path


def resize(a, size):
    """Resize a float image to size x size (or (w, h)) with Lanczos, channel by channel."""
    from PIL import Image
    w, h = (size, size) if isinstance(size, int) else size
    if a.shape[1] == w and a.shape[0] == h:
        return a
    chans = [np.asarray(Image.fromarray(a[:, :, c].astype(np.float32), "F").resize((w, h), Image.LANCZOS))
             for c in range(a.shape[2])]
    return np.clip(np.stack(chans, axis=2), 0, 1)


def pow2_at_most(n, lo=128, hi=4096):
    p = lo
    while p * 2 <= min(n, hi):
        p *= 2
    return p


def luminance(rgb):
    return rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722


def clamp_albedo(rgb, lo=ALBEDO_SRGB_RANGE[0], hi=ALBEDO_SRGB_RANGE[1], max_sat=ALBEDO_MAX_SATURATION):
    """Keep an sRGB base colour inside physically plausible limits without flattening it.

    Brightness is remapped linearly into [lo, hi] (so contrast is kept, only the extremes move) and saturation
    above max_sat is pulled back toward grey. Over-saturated, pure-white or pure-black albedo is one of the
    main reasons a surface reads as plastic or CG under physically based lighting.
    """
    rgb = np.clip(rgb[..., :3], 0, 1)
    lum = luminance(rgb)[..., None]
    target = lo + lum * (hi - lo)
    out = rgb * (target / np.maximum(lum, 1e-4))
    mx = out.max(axis=-1, keepdims=True)
    mn = out.min(axis=-1, keepdims=True)
    sat = (mx - mn) / np.maximum(mx, 1e-4)
    k = np.where(sat > max_sat, max_sat / np.maximum(sat, 1e-4), 1.0)
    grey = luminance(out)[..., None]
    out = grey + (out - grey) * k
    return np.clip(out, 0, 1)


def normal_from_height(h, strength=2.0):
    """Tangent-space OpenGL normal map from a height field in [0, 1] (wraps at the borders)."""
    h = h[..., 0] if h.ndim == 3 else h
    dx = (np.roll(h, -1, axis=1) - np.roll(h, 1, axis=1)) * 0.5 * strength * h.shape[1] / 256
    dy = (np.roll(h, -1, axis=0) - np.roll(h, 1, axis=0)) * 0.5 * strength * h.shape[0] / 256
    n = np.stack([-dx, dy, np.ones_like(h)], axis=-1)   # image rows go down, OpenGL +Y goes up
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return n * 0.5 + 0.5


def tileable_noise(size, feature_px, seed=0, octaves=4):
    """Seamlessly tiling fractal noise in [0, 1]: white noise low-passed in the frequency domain (periodic)."""
    rng = np.random.default_rng(seed)
    fy = np.fft.fftfreq(size)[:, None]
    fx = np.fft.fftfreq(size)[None, :]
    f = np.sqrt(fx * fx + fy * fy)
    out = np.zeros((size, size), np.float32)
    amp, px = 1.0, feature_px
    for _ in range(octaves):
        spec = np.fft.fft2(rng.standard_normal((size, size)))
        sigma = 1.0 / max(px, 1.0)
        out += amp * np.real(np.fft.ifft2(spec * np.exp(-(f / sigma) ** 2))).astype(np.float32)
        amp *= 0.5
        px /= 2
    out -= out.min()
    return out / max(out.max(), 1e-6)


CONDITION = {  # wear (edges), dirt (cavities and the bottom), rough_var (macro roughness breakup)
    "new": {"wear": 0.08, "dirt": 0.1, "rough_var": 0.6},
    "used": {"wear": 0.35, "dirt": 0.4, "rough_var": 0.8},
    "worn": {"wear": 0.7, "dirt": 0.75, "rough_var": 1.0},
    "ruined": {"wear": 1.0, "dirt": 1.0, "rough_var": 1.0},
}


def blur(a, radius):
    """Separable box blur (two passes ~ gaussian), wrapping at the borders."""
    r = max(1, int(radius))
    for axis in (0, 1):
        for _ in range(2):
            c = np.cumsum(np.concatenate([a.take(range(-r - 1, 0), axis=axis), a, a.take(range(0, r), axis=axis)],
                                         axis=axis), axis=axis)
            n = a.shape[axis]
            a = (c.take(range(2 * r + 1, 2 * r + 1 + n), axis=axis) - c.take(range(0, n), axis=axis)) / (2 * r + 1)
    return a


def delight(rgb, strength, radius):
    """Partly remove lighting baked into a generated albedo: divide out low-frequency brightness changes.

    There is no reliable automatic de-lighting; this only flattens broad shading gradients (a lit top and a dark
    underside) and lowers contrast, which is usually what makes an AI texture fight the engine's lighting.
    strength 0 = off, 1 = brightness fully flattened at scales above radius pixels.
    """
    if strength <= 0:
        return rgb
    lum = luminance(rgb)
    low = blur(lum, radius)
    gain = (lum.mean() / np.maximum(low, 1e-3)) ** strength
    return np.clip(rgb * np.clip(gain, 0.5, 2.0)[..., None], 0, 1)


def weather(albedo, rough, metal, ao, edge, cavity, noise, height, wear, dirt, rough_var, bare_metal=0.0):
    """Turn a clean generated texture set into a believable surface (the 'not plastic' pass).

    All inputs are float HxW (albedo HxWx3 sRGB). edge / cavity come from mesh curvature, noise is 3D noise
    baked through the UVs (seamless across islands), height is 0 at the bottom of the object and 1 at the top.
      - dirt settles in cavities, occluded areas and near the ground: darker, desaturated, rougher
      - exposed edges wear: lighter, smoother, and (bare_metal > 0) paint chips off down to metal
      - roughness always gets a macro breakup, and is forced to vary if the source was nearly uniform
    Returns albedo, rough, metal.
    """
    cav = np.clip(np.maximum(cavity * 1.3, (1 - ao) * 1.2), 0, 1)
    dirt_mask = np.clip(cav + (1 - height) ** 4 * 0.7 + (noise - 0.5) * 0.8, 0, 1) * dirt
    edge_mask = np.clip(edge * 1.6 * (0.4 + noise), 0, 1) * wear

    grey = luminance(albedo)[..., None]
    dirty = (grey * 0.6 + albedo * 0.4) * np.array([0.62, 0.58, 0.52], np.float32)
    out = albedo + (dirty - albedo) * (dirt_mask[..., None] * 0.7)
    out = out + (np.minimum(out * 1.2 + 0.04, 1) - out) * (edge_mask[..., None] * 0.6)

    spread = float(rough.std())
    breakup = (noise - 0.5) * (0.22 + max(0.0, 0.05 - spread) * 4) * rough_var
    r = rough + breakup + dirt_mask * 0.3 - edge_mask * 0.25
    m = np.maximum(metal, edge_mask * bare_metal)
    return np.clip(out, 0, 1), np.clip(r, 0.04, 1), np.clip(m, 0, 1)


def pack_orm(ao, rough, metal):
    return np.concatenate([_g(ao), _g(rough), _g(metal)], axis=2)


def pack_mask(ao, rough, metal):
    return np.concatenate([_g(metal), _g(ao), np.ones_like(_g(ao)), 1.0 - _g(rough)], axis=2)


def _g(a):
    return a if a.ndim == 3 else a[:, :, None]


def write_maps(out_dir, name, bc, normal=None, ao=None, rough=None, metal=None, size=None, clamp=True):
    """Write the canonical map set for one material. Returns {"bc", "n", "orm", "mask"} file names.

    bc is sRGB RGB(A) float; ao / rough / metal are linear single-channel float images or scalars.
    size, when given, resizes everything to that edge length first (texel density control).
    """
    h, w = bc.shape[:2]
    if size:
        bc = resize(bc, size)
        h, w = bc.shape[:2]

    def plane(v, default):
        if v is None:
            v = default
        if np.isscalar(v):
            return np.full((h, w, 1), float(v), np.float32)
        v = _g(v.astype(np.float32))
        return resize(v, (w, h)) if v.shape[:2] != (h, w) else v

    ao, rough, metal = plane(ao, 1.0), plane(rough, 0.6), plane(metal, 0.0)
    rgb = clamp_albedo(bc[..., :3]) if clamp else bc[..., :3]
    out = {"bc": f"T_{name}_BC.png", "orm": f"T_{name}_ORM.png", "mask": f"T_{name}_MASK.png", "n": f"T_{name}_N.png"}
    save(os.path.join(out_dir, out["bc"]), np.concatenate([rgb, bc[..., 3:4]], axis=2) if bc.shape[2] == 4 else rgb)
    save(os.path.join(out_dir, out["orm"]), pack_orm(ao, rough, metal))
    save(os.path.join(out_dir, out["mask"]), pack_mask(ao, rough, metal))
    if normal is None:
        normal = np.concatenate([np.full((4, 4, 2), 0.5, np.float32), np.ones((4, 4, 1), np.float32)], axis=2)
    elif normal.shape[:2] != (h, w):
        normal = resize(normal, (w, h))
    save(os.path.join(out_dir, out["n"]), normal)
    return out
