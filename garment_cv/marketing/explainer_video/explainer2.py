"""SKIT AI Vision explainer, motion-graphics version (v2).

Look: near-black plum with a violet glow, cream light-weight type that blurs in word by word, glowing pills,
tilted glass cards with shadows, a star-shaped wipe to light scenes, mint success colour, soft sound bed.

    python explainer2.py prep            # timeline.json (from the voice clips) + audio2.wav (voice + pad + whooshes) + captions.srt
    python explainer2.py still 4 12 ...  # PNG stills at those seconds
    python explainer2.py render out.mp4
"""
import json, math, subprocess, sys, wave
from functools import lru_cache
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

import explainer as E
from explainer import Ctx, clamp, ease_io, ease_out, back, font, hexc, lerp, mix, seg

HERE = Path(__file__).resolve().parent
W, H, FPS = 1920, 1080, 24
TR = 0.8                                   # transition length (s), centred on each scene boundary
LEAD, TAIL = E.LEAD, E.TAIL
E.FONTS.update({"l": "segoeuil.ttf", "sl": "segoeuisl.ttf"})
E.FX = E.FY = 0
E.FW, E.FH = 1080, 700                     # cam_view draws into its own 1080x700 tile

DARK, VIO, LAV, LAV2 = (19, 3, 11), (149, 77, 237), (181, 140, 255), (217, 193, 253)
CREAM, LIGHT, MINT, CORAL = (254, 253, 248), (251, 251, 251), (92, 240, 160), (255, 107, 107)
INK = (19, 2, 8)
MUTED_D, MUTED_L = (170, 150, 200), (107, 100, 120)
E.BLUE, E.GREEN = (124, 92, 255), (47, 203, 138)
AMBER = E.AMBER
GB = ImageFilter.GaussianBlur

# ---------------------------------------------------------------- backgrounds
_gy, _gx = np.mgrid[0:H // 4, 0:W // 4].astype(np.float32)
_gx *= 4; _gy *= 4
_rng = np.random.default_rng(7)
NOISE = _rng.integers(-2, 3, size=(H, W, 1)).astype(np.int16)

def _finish(arr):
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
    a = np.asarray(im).astype(np.int16) + NOISE
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))

def bg_dark(t, glow=1.0, cx=None, cy=1.06):
    cx = W * 0.5 + math.sin(t * 0.5) * 220 if cx is None else cx
    g1 = np.exp(-(((_gx - cx) / 860) ** 2 + ((_gy - H * cy) / 480) ** 2)) * glow
    g2 = np.exp(-(((_gx - W * 0.5) / 950) ** 2 + ((_gy + 80) / 400) ** 2)) * 0.5
    base = np.array(DARK, np.float32)
    img = base + (np.array(VIO, np.float32) - base) * g1[..., None] * 0.9 + (np.array((70, 28, 84), np.float32) - base) * g2[..., None] * 0.55
    return _finish(img)

def bg_light(t, glow=1.0):
    cx = W * 0.10 + math.sin(t * 0.5) * 160
    g1 = np.exp(-(((_gx - cx) / 760) ** 2 + ((_gy - H * 1.0) / 400) ** 2)) * glow
    g2 = np.exp(-(((_gx - W * 0.92) / 620) ** 2 + ((_gy - H * 1.05) / 300) ** 2)) * 0.6 * glow
    base = np.array(LIGHT, np.float32)
    img = base + (np.array((217, 193, 253), np.float32) - base) * g1[..., None] * 0.9 + (np.array((236, 224, 250), np.float32) - base) * g2[..., None]
    return _finish(img)

# ---------------------------------------------------------------- masks and text
class Mk:
    """Anti-aliased L-mask canvas (drawn at 2x)."""
    def __init__(s, w, h):
        s.w, s.h = w, h
        s.m = Image.new("L", (w * 2, h * 2), 0); s.d = ImageDraw.Draw(s.m)
    def rr(s, x, y, w, h, r, fill=255, outline=None, lw=2):
        s.d.rounded_rectangle((x * 2, y * 2, (x + w) * 2, (y + h) * 2), radius=r * 2, fill=fill, outline=outline, width=int(lw * 2))
    def ell(s, cx, cy, rx, ry=None, fill=255, outline=None, lw=2):
        ry = rx if ry is None else ry
        s.d.ellipse(((cx - rx) * 2, (cy - ry) * 2, (cx + rx) * 2, (cy + ry) * 2), fill=fill, outline=outline, width=int(lw * 2))
    def line(s, pts, w, v=255):
        p = [(x * 2, y * 2) for x, y in pts]
        s.d.line(p, fill=v, width=int(w * 2), joint="curve")
        for q in (p[0], p[-1]):
            r = w
            s.d.ellipse((q[0] - r, q[1] - r, q[0] + r, q[1] + r), fill=v)
    def poly(s, pts, fill=255):
        s.d.polygon([(x * 2, y * 2) for x, y in pts], fill=fill)
    def get(s): return s.m.reduce(2)

@lru_cache(maxsize=None)
def rr_mask(w, h, r):
    m = Mk(w, h); m.rr(0, 0, w, h, r); return m.get()

def scale_mask(m, a):
    return m.point(lambda v: int(v * a)) if a < 0.999 else m

def paste_col(frame, color, mask, x, y, a=1.0):
    if a <= 0.01: return
    frame.paste(color, (int(x), int(y)), scale_mask(mask, a))

@lru_cache(maxsize=None)
def tmask(txt, size, wt):
    f = font(wt, size * 2)
    wpx, hpx = int(f.getlength(txt)) + 80, int(size * 2 * 1.5) + 40
    m = Image.new("L", (wpx, hpx), 0)
    ImageDraw.Draw(m).text((40, 20), txt, font=f, fill=255, anchor="la")
    return m.reduce(2)

def twidth(txt, size, wt): return font(wt, size).getlength(txt)

def put_text(frame, txt, size, wt, x, ytop, color, a=1.0, blur=0.0):
    if a <= 0.01: return
    m = tmask(txt, size, wt)
    if blur > 0.4: m = m.filter(GB(blur))
    frame.paste(color, (int(x - 20), int(ytop - 10)), scale_mask(m, a))

def text_center(frame, txt, size, wt, cx, cy, color, a=1.0, blur=0.0):
    put_text(frame, txt, size, wt, cx - twidth(txt, size, wt) / 2, cy - size * 0.68, color, a, blur)

def kinetic(frame, lines, cx, cy, size, t, t0, wt="l", per=0.17, dur=0.7, t_out=None, out_dur=0.45, lh=1.25, gap=0.27, rise=26, dark=True):
    """lines: [[(word, color|None), ...], ...]. Words blur in one after another; optionally blur out together."""
    base = CREAM if dark else INK
    n, idx = len(lines), 0
    for li, line in enumerate(lines):
        widths = [twidth(w, size, wt) for w, _ in line]
        total = sum(widths) + size * gap * (len(line) - 1)
        x = cx - total / 2
        ytop = cy + (li - (n - 1) / 2) * size * lh - size * 0.68
        for (w, col), ww in zip(line, widths):
            p = ease_out(seg(t, t0 + idx * per, dur))
            q = seg(t, t_out, out_dur) if t_out is not None else 0.0
            a = p * (1 - q)
            if a > 0.01:
                put_text(frame, w, size, wt, x, ytop + (1 - p) * rise - q * 14, col or base, a, (1 - p) * 16 + q * 12)
            x += ww + size * gap; idx += 1

def words(s, col=None): return [(w, col) for w in s.split()]

# ---------------------------------------------------------------- tiles, 3D placement, glow
def persp_coeffs(src, dst):
    A, B = [], []
    for (X, Y), (x, y) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -X * x, -X * y]); B.append(X)
        A.append([0, 0, 0, x, y, 1, -Y * x, -Y * y]); B.append(Y)
    return np.linalg.solve(np.array(A, float), np.array(B, float)).tolist()

def proj(w, h, s, yaw, pitch, roll, cx, cy, f=2400.0):
    out = []
    ya, pa, ra = math.radians(yaw), math.radians(pitch), math.radians(roll)
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        x, y, z = sx * w / 2 * s, sy * h / 2 * s, 0.0
        x, z = x * math.cos(ya) + z * math.sin(ya), -x * math.sin(ya) + z * math.cos(ya)
        y, z = y * math.cos(pa) - z * math.sin(pa), y * math.sin(pa) + z * math.cos(pa)
        x, y = x * math.cos(ra) - y * math.sin(ra), x * math.sin(ra) + y * math.cos(ra)
        k = f / (f + z)
        out.append((cx + x * k, cy + y * k))
    return out

def place(frame, tile, mask, cx, cy, s=1.0, yaw=0.0, pitch=0.0, roll=0.0, a=1.0, shadow=0.0, shadow_col=(8, 2, 16),
          sh_blur=30, sh_dy=28, blur=0.0, glow=None):
    """Paste an RGB tile (with L mask) at a 3D pose. glow = (color, alpha, radius) draws a soft outline halo."""
    if a <= 0.01 or s <= 0.01: return
    w, h = tile.size
    quad = proj(w, h, s, yaw, pitch, roll, cx, cy)
    xs, ys = [p[0] for p in quad], [p[1] for p in quad]
    pad = int(max(sh_blur * 2, glow[2] * 2 if glow else 0) + 30)
    x0, y0, x1, y1 = int(min(xs)) - pad, int(min(ys)) - pad, int(max(xs)) + pad, int(max(ys)) + pad
    bw, bh = x1 - x0, y1 - y0
    if bw < 4 or bh < 4 or bw > 4200 or bh > 3000: return
    cf = persp_coeffs([(0, 0), (w, 0), (w, h), (0, h)], [(px - x0, py - y0) for px, py in quad])
    rgb = tile.transform((bw, bh), Image.PERSPECTIVE, cf, Image.BILINEAR)
    m = mask.transform((bw, bh), Image.PERSPECTIVE, cf, Image.BILINEAR)
    if blur > 0.4:
        rgb = rgb.filter(GB(blur)); m = m.filter(GB(blur))
    if shadow > 0:
        sm = scale_mask(m.filter(GB(sh_blur)), shadow * a)
        frame.paste(shadow_col, (x0, y0 + sh_dy), sm)
    if glow:
        col, ga, gr = glow
        ring = ImageChops.subtract(m, m.filter(ImageFilter.MinFilter(7))).filter(GB(gr))
        ring = ring.point(lambda v: min(255, int(v * 3.2 * ga * a)))
        frame.paste(col, (x0, y0), ring)
    frame.paste(rgb, (x0, y0), scale_mask(m, a))

def glass_tile(w, h, r, fill, line=None):
    c = Ctx(fill, w, h)
    if line: c.rect(1, 1, w - 2, h - 2, r=r, line=line, lw=2)
    return c

@lru_cache(maxsize=None)
def pill_tile(label, size, fg, fill, dot=None, icon=None, padx=44, h=None):
    h = h or int(size * 2.1)
    lw = twidth(label, size, "s")
    extra = 44 if (dot or icon) else 0
    w = int(lw + padx * 2 + extra)
    c = Ctx(fill, w, h)
    x = padx
    if dot: c.ell(padx + 6, h / 2, 9, fill=dot); x = padx + 34
    if icon == "spark":
        k = 15
        pts = []
        for i in range(0, 64):
            th = i / 64 * 2 * math.pi
            pts.append((padx + 14 + k * math.cos(th) ** 3, h / 2 + k * math.sin(th) ** 3))
        c.poly(pts, fill=fg); x = padx + 44
    c.text(x, h / 2 - 1, label, size, fg, "s", anchor="lm")
    return c.out(), rr_mask(w, h, h // 2)

def star_pts(cx, cy, R, n=72):
    return [(cx + R * math.cos(2 * math.pi * i / n) ** 3, cy + R * math.sin(2 * math.pi * i / n) ** 3) for i in range(n)]

def spark(frame, cx, cy, R, color, a=1.0, glow=0.0):
    m = Mk(int(R * 2 + 8), int(R * 2 + 8))
    m.poly(star_pts(R + 4, R + 4, R))
    mm = m.get()
    if glow > 0:
        paste_col(frame, color, mm.filter(GB(glow)), cx - R - 4, cy - R - 4, a * 0.8)
    paste_col(frame, color, mm, cx - R - 4, cy - R - 4, a)

def particles(frame, t, n=26, dark=True, seed=3):
    rng = np.random.default_rng(seed)
    for i in range(n):
        x0, y0 = rng.uniform(0, W), rng.uniform(0, H)
        sp, ph = rng.uniform(8, 26), rng.uniform(0, 6.28)
        x, y = (x0 + math.sin(t * 0.3 + ph) * 30) % W, (y0 - t * sp) % H
        r = rng.uniform(1.5, 3.2)
        a = (0.25 + 0.25 * math.sin(t * 1.4 + ph)) * (0.6 if dark else 0.35)
        m = _dot(int(r * 10) / 10)
        paste_col(frame, LAV2 if dark else VIO, m, x - m.size[0] / 2, y - m.size[1] / 2, a)

@lru_cache(maxsize=None)
def _dot(r):
    m = Mk(int(r * 2 + 10), int(r * 2 + 10)); m.ell(r + 5, r + 5, r); return m.get().filter(GB(1.1))

def sweep_dot(frame, x0, x1, y, u, col=LAV2, a=1.0):
    x = lerp(x0, x1, u)
    m = _dot(7)
    paste_col(frame, col, m.filter(GB(6)), x - m.size[0] / 2, y - m.size[1] / 2, 0.9 * a * math.sin(u * math.pi))
    paste_col(frame, (255, 255, 255), m, x - m.size[0] / 2, y - m.size[1] / 2, a * math.sin(u * math.pi))

@lru_cache(maxsize=None)
def logo_base(size):
    c = Ctx((19, 2, 8), size, size)
    return c

def _load_brand():
    im = Image.open(HERE / "logo_src.png").convert("RGBA").crop((50, 48, 352, 350))
    rgb = Image.new("RGB", im.size, (19, 3, 11)); rgb.paste(im, (0, 0), im.split()[3])
    return rgb
BRAND = _load_brand()

@lru_cache(maxsize=None)
def brand_tile(size):
    t = BRAND.resize((size, size), Image.LANCZOS)
    m = Mk(size, size); m.ell(size / 2, size / 2, size / 2 - 0.5)
    return t, m.get()

def logo_tile(size, p, fg=(255, 255, 255), fill=(19, 2, 8)):
    c = Ctx(fill, size, size)
    n = max(1, int(len(E.S_PTS) * p)); k = size * 0.42; r = size * 0.0475
    for x, y in E.S_PTS[:n]:
        c.ell(size / 2 + x * k * 1.15, size / 2 + y * k, r, fill=fg)
    return c.out(), rr_mask(size, size, int(size * 0.23))

# ---------------------------------------------------------------- scene helpers
def fade_tile(a, t0, dur): return ease_out(seg(a, t0, dur))

def cam_icon_dark(c, cx, cy, s):
    c.rect(cx - 52 * s, cy - 30 * s, 104 * s, 66 * s, r=14 * s, fill=CREAM)
    c.rect(cx - 20 * s, cy - 44 * s, 40 * s, 18 * s, r=6 * s, fill=CREAM)
    c.ell(cx, cy + 3 * s, 26 * s, fill=(30, 17, 48)); c.ell(cx, cy + 3 * s, 18 * s, fill=VIO); c.ell(cx + 6 * s, cy - 3 * s, 5 * s, fill=(255, 255, 255))

def eye_icon_dark(c, cx, cy, s, t):
    k = 44 * s
    for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        c.line([(cx + dx * k, cy + dy * (k - 24 * s)), (cx + dx * k, cy + dy * k), (cx + dx * (k - 24 * s), cy + dy * k)], CREAM, 7 * s)
    c.ell(cx, cy, 22 * s, fill=VIO); c.ell(cx + 6 * s, cy - 6 * s, 7 * s, fill=(255, 255, 255))
    sweep = math.sin(t * 3.0) * 0.5 + 0.5
    yy = cy - k + 10 * s + sweep * (2 * k - 20 * s)
    c.line([(cx - k + 6 * s, yy), (cx + k - 6 * s, yy)], LAV, 4 * s, 0.9)

def odoo_tile(u, t):
    c = Ctx((255, 255, 255), 560, 700)
    E.odoo_chip(c, 112, 60, 0.78)
    c.text(40, 140, "Work orders", 40, INK, "b", anchor="lm")
    c.text(40, 184, "Manufacturing order 00021", 24, MUTED_L, "r", anchor="lm")
    m_state = "wait" if u < 0.20 else ("progress" if u < 0.62 else "done")
    c_state = "wait" if u < 0.62 else "cut"
    for i, (n, stt, col) in enumerate([("Marking", m_state, E.BLUE), ("Cutting", c_state, AMBER)]):
        y = 240 + i * 160
        active = stt in ("progress", "cut")
        c.rect(30, y, 500, 128, r=22, fill=(247, 244, 252), line=col if active else (232, 226, 244), lw=4 if active else 2)
        c.ell(76, y + 64, 26, fill=E.GREEN if stt == "done" else (col if stt != "wait" else (200, 196, 214)))
        if stt == "done": E.check(c, 76, y + 64, 1.3, (255, 255, 255), 6)
        else: c.text(76, y + 63, str(i + 1), 26, (255, 255, 255), "b")
        c.text(124, y + 52, n, 36, INK, "s", anchor="lm")
        lab = {"wait": "Waiting", "progress": "In progress", "done": "Finished", "cut": "In progress"}[stt]
        kd = {"wait": "wait", "progress": "progress", "done": "done", "cut": "cut"}[stt]
        E.badge(c, 124 + c.tw(lab, 22, "s") / 2 + 16, y + 92, lab, kd)
    c.text(40, 640, "Updated by the camera. No typing.", 24, MUTED_L, "r", anchor="lm", a=seg(u, 0.30, 0.1))
    # rounded: tile bg is white; mask does the rest
    return c.out()

def cam_tile(st):
    c = Ctx(hexc("#E4DFD2"), 1080, 700)
    E.cam_view(c, st)
    return c.out()

def ring_mask(R, width, frac, size):
    m = Mk(size, size)
    n = max(2, int(120 * frac))
    pts = [(size / 2 + R * math.cos(math.radians(-90 + 360 * k / 120)), size / 2 + R * math.sin(math.radians(-90 + 360 * k / 120))) for k in range(0, n + 1)]
    if frac > 0.004: m.line(pts, width / 2)
    return m.get()

def bar_tile_card(w, h, light=True):
    return Ctx((255, 255, 255) if light else (30, 17, 48), w, h)


# ---------------------------------------------------------------- scenes
OPS = [("Marking", E.BLUE), ("Cutting", AMBER), ("Stitching", (170, 120, 255)), ("Pressing", None), ("Fusing", None), ("Bundling", None),
       ("Hemming", None), ("Trimming", None), ("Buttoning", None), ("Checking", None), ("Folding", None), ("Packing", None), ("Finishing", None), ("Dispatch", None)]
CHIP_POS = [(210, 160, 1.0), (560, 110, 0.8), (930, 150, 0.65), (1300, 100, 0.85), (1650, 170, 1.0), (330, 280, 0.7),
            (1500, 290, 0.75), (190, 800, 1.0), (520, 900, 0.8), (880, 930, 0.65), (1250, 880, 0.9), (1620, 820, 1.0), (1740, 560, 0.7), (150, 520, 0.7)]

@lru_cache(maxsize=None)
def chip(name, col):
    c = Ctx((38, 22, 60), 280, 76)
    c.rect(1, 1, 278, 74, r=36, line=(110, 84, 190), lw=2)
    c.ell(40, 38, 9, fill=col or (150, 130, 190))
    c.text(66, 37, name, 27, (226, 214, 250), "s", anchor="lm")
    return c.out(), rr_mask(280, 76, 38)

def scene1(t, d):
    fr = bg_dark(t, 0.9)
    particles(fr, t)
    for i, ((name, col), (x, y, dep)) in enumerate(zip(OPS, CHIP_POS)):
        a = ease_out(seg(t, 0.2 + i * 0.07, 0.7))
        tl, mk = chip(name, col)
        place(fr, tl, mk, x + math.sin(t * 0.5 + i) * 18, y + math.cos(t * 0.45 + i * 1.7) * 14 - t * 4 * dep, s=0.7 * dep + 0.25, yaw=(x - 960) / 160, pitch=-4,
              a=a * (0.35 + 0.5 * dep) * (1 - seg(t, d - 0.5, 0.6) * 0.5), blur=(1.05 - dep) * 5, shadow=0.0)
    kinetic(fr, [words("Hundreds of"), [("operations", LAV)] + words("happen"), words("every single day.")], 960, 520, 96, t, 0.5, per=0.2)
    return fr

def scene2(t, d):
    fr = bg_dark(t, 1.0, cx=W * 0.3)
    particles(fr, t, 18, seed=5)
    # job sheet stack
    p = back(seg(t, 0.25, 0.9))
    bob = math.sin(t * 1.1) * 8
    for k, (dx, dy, rl, al) in enumerate([(-46, 26, -5, 0.45), (48, -20, 4, 0.6)]):
        c = Ctx((236, 232, 224), 560, 660)
        for r_ in range(4): c.line([(60, 190 + r_ * 110), (500, 190 + r_ * 110)], (214, 208, 196), 3, cap=False)
        place(fr, c.out(), rr_mask(560, 660, 26), 560 + dx, 540 + dy + bob, s=0.82 * p, yaw=-14, pitch=3, roll=rl, a=al * clamp(p), shadow=0.3)
    c = Ctx((250, 248, 242), 560, 660)
    c.text(44, 74, "JOB SHEET", 34, INK, "b", anchor="lm")
    c.text(44, 114, "Order 00021", 22, (120, 114, 100), "r", anchor="lm")
    ang = t * 5
    c.ell(496, 80, 28, line=INK, lw=4)
    c.line([(496, 80), (496 + 17 * math.sin(ang), 80 - 17 * math.cos(ang))], INK, 4)
    c.line([(496, 80), (496 + 10 * math.sin(ang / 12), 80 - 10 * math.cos(ang / 12))], INK, 4)
    rows = [("Marking", 2.2, "tick", "2 h late", E.RED), ("Cutting", 3.6, "q", "not sure", AMBER), ("Stitching", 4.8, "wrong", "wrong qty", E.RED), ("Pressing", 99, "none", "never entered", (120, 114, 100))]
    for i, (name, ts, kind, tag, tc) in enumerate(rows):
        y = 200 + i * 115
        c.text(104, y, name, 32, INK, "s", anchor="lm")
        c.line([(104, y + 36), (516, y + 36)], (222, 216, 202), 3, cap=False)
        c.rect(44, y - 19, 38, 38, r=8, line=(120, 114, 100), lw=3)
        pp = seg(t, ts, 0.35)
        if pp > 0 and kind == "tick": E.check(c, 63, y, 1.2, E.GREEN, 7, a=pp)
        if pp > 0 and kind == "q": c.text(63, y, "?", 32, AMBER, "b", a=pp)
        if pp > 0 and kind == "wrong": c.line([(51, y - 12), (75, y + 12)], E.RED, 6, pp); c.line([(75, y - 12), (51, y + 12)], E.RED, 6, pp)
        pa = pp if kind != "none" else seg(t, 6.9, 0.4)
        if pa > 0: c.pill(420, y - 2, tag, tc, (tc[0], tc[1], tc[2], 40), size=21, padx=13, h=36, a=pa)
    place(fr, c.out(), rr_mask(560, 660, 26), 560, 540 + bob, s=0.82 * p, yaw=-14, pitch=3, a=clamp(p), shadow=0.5)
    cx = 1370
    kinetic(fr, [words("Written"), [("on", None), ("paper.", LAV)]], cx, 520, 88, t, 0.7, t_out=3.4)
    kinetic(fr, [words("Entered"), [("late.", CORAL)]], cx, 520, 88, t, 3.7, t_out=5.8)
    kinetic(fr, [[("Or", None), ("never", CORAL)], words("entered at all.")], cx, 520, 88, t, 6.1)
    return fr

def scene3(t, d):
    fr = bg_dark(t, 0.9, cx=W * 0.68)
    particles(fr, t, 16, seed=9)
    cards_a = 1 - seg(t, 5.0, 0.6)
    for k, (cx0, title, rows, yaw) in enumerate([(520, "In Odoo", [("Marking", "In progress", "progress"), ("Cutting", "Waiting", "wait")], 12),
                                                  (1400, "On the floor", [("Marking", "Finished", "done"), ("Cutting", "Running", "cut")], -12)]):
        p = ease_out(seg(t, 0.6 + k * 0.9, 0.9))
        c = Ctx((255, 255, 255), 560, 360)
        c.text(36, 56, title, 36, INK, "b", anchor="lm")
        for i, (n, st_, kd) in enumerate(rows):
            y = 100 + i * 122
            c.rect(30, y, 500, 100, r=18, fill=(247, 244, 252), line=(232, 226, 244), lw=2)
            c.text(60, y + 50, n, 32, INK, "s", anchor="lm")
            E.badge(c, 440, y + 50, st_, kd, size=22)
        place(fr, c.out(), rr_mask(560, 360, 28), cx0 + (1 - p) * (-380 if k == 0 else 380), 520 + math.sin(t * 1.2 + k) * 7, s=0.92, yaw=yaw * (0.4 + 0.6 * p), pitch=2,
              a=p * cards_a, shadow=0.5, blur=(1 - cards_a) * 12)
    pp = back(seg(t, 3.0, 0.55))
    pul = 1 + 0.07 * math.sin(t * 6)
    m = Mk(150, 150); m.ell(75, 75, 56); m.line([(55, 62), (95, 62)], 5, 0); m.line([(55, 88), (95, 88)], 5, 0); m.line([(88, 50), (62, 100)], 5, 0)
    mk = m.get()
    s_ = pp * pul
    mm = mk.resize((int(150 * s_) + 1, int(150 * s_) + 1), Image.BICUBIC)
    paste_col(fr, CORAL, mm.filter(GB(18)), 960 - mm.size[0] / 2, 520 - mm.size[1] / 2, 0.7 * cards_a * clamp(pp))
    paste_col(fr, CORAL, mm, 960 - mm.size[0] / 2, 520 - mm.size[1] / 2, cards_a * clamp(pp))
    kinetic(fr, [words("Odoo says one thing.")], 960, 190, 66, t, 0.6, t_out=2.9, per=0.14)
    kinetic(fr, [[("The", None), ("floor", None), ("says", None), ("another.", CORAL)]], 960, 190, 66, t, 3.2, t_out=4.9, per=0.14)
    kinetic(fr, [words("What you can't see, costs you.")], 960, 190, 66, t, 5.4, per=0.14)
    for i, (lab, ts) in enumerate([("No live status", 5.5), ("Hidden idle time", 7.5), ("Odoo out of date", 9.2)]):
        a = ease_out(seg(t, ts, 0.7))
        tl, mk2 = pill_tile(lab, 40, CREAM, (30, 14, 28), dot=CORAL, h=104, padx=60)
        place(fr, tl, mk2, 960, 360 + i * 150 + (1 - a) * 36, s=0.8 + 0.2 * a, a=a, shadow=0.0, blur=(1 - a) * 10, glow=(CORAL, 0.55 + 0.25 * math.sin(t * 2 + i), 16))
    return fr

def scene4(t, d):
    fr = bg_light(t, 1.0)
    particles(fr, t, 20, dark=False, seed=11)
    for k in range(3):
        u = (t * 0.28 + k / 3) % 1.0
        R = 150 + u * 380
        m = Mk(int(R * 2 + 30), int(R * 2 + 30)); m.ell(R + 15, R + 15, R, fill=0, outline=255, lw=3)
        paste_col(fr, VIO, m.get(), 960 - R - 15, 340 - R - 15, (1 - u) * 0.22 * ease_out(seg(t, 0.5, 0.8)))
    p = back(seg(t, 0.2, 0.7))
    tl, mk = brand_tile(250)
    place(fr, tl, mk, 960, 330, s=p, a=clamp(p * 2), shadow=0.28, shadow_col=(80, 40, 160), sh_blur=34, sh_dy=30, glow=(VIO, 0.35, 20))
    kinetic(fr, [[("SKIT", None), ("AI", None), ("Vision", None)]], 960, 580, 124, t, 1.2, wt="sl", per=0.16, dark=False)
    a = ease_out(seg(t, 3.0, 0.7))
    tl, mk2 = pill_tile("Your camera, connected to Odoo", 36, CREAM, (19, 6, 28), icon="spark", padx=46, h=84)
    place(fr, tl, mk2, 960, 760 + (1 - a) * 26, s=0.9 + 0.1 * a, a=a, glow=(VIO, 0.6 + 0.3 * math.sin(t * 2), 18), shadow=0.35, shadow_col=(90, 50, 170), sh_blur=26, sh_dy=22)
    return fr

@lru_cache(maxsize=None)
def card5(i, w=360, h=440):
    return None

def scene5(t, d):
    fr = bg_dark(t, 1.0, cx=W * 0.5)
    particles(fr, t, 20, seed=13)
    kinetic(fr, [words("How it works")], 960, 140, 72, t, 0.4, per=0.2)
    xs = [310, 750, 1190, 1630]
    starts = [1.6, 3.4, 6.5, 8.4]
    labels = [("1", "Camera watches", "Your existing camera"), ("2", "AI recognises", "Worker, fabric, tools"), ("3", "Rules decide", "Zones and timing"), ("4", "Odoo updates", "Work orders by themselves")]
    for i in range(3):
        a = seg(t, starts[i + 1] - 0.4, 0.6)
        if a > 0:
            x0, x1 = xs[i] + 185, xs[i + 1] - 185
            m = Mk(int(x1 - x0) + 20, 24); m.line([(10, 12), (10 + (x1 - x0) * a, 12)], 2.5)
            mm = m.get()
            paste_col(fr, LAV, mm.filter(GB(5)), x0 - 10, 600 - 12, 0.6)
            paste_col(fr, LAV2, mm, x0 - 10, 600 - 12, 0.9)
            if a >= 1:
                for k in range(2):
                    sweep_dot(fr, x0, x1, 600, (t * 0.55 + k * 0.5 + i * 0.2) % 1.0)
    for i, (num, lab, sub) in enumerate(labels):
        p = ease_out(seg(t, starts[i], 0.9))
        c = Ctx((30, 17, 48), 360, 440)
        c.rect(1, 1, 358, 438, r=30, line=(104, 80, 180), lw=2)
        c.ell(50, 52, 22, fill=VIO)
        c.text(50, 51, num, 24, (255, 255, 255), "b")
        cy = 200
        if i == 0: cam_icon_dark(c, 180, cy, 1.55)
        if i == 1:
            eye_icon_dark(c, 180, cy, 1.55, t)
            for k, (lb, col) in enumerate([("Person", E.GREEN), ("Cloth", (255, 255, 255)), ("Pen", E.PINK)]):
                if t > starts[1] + 1.2 + k * 0.7:
                    c.rect(262 - 10 * 0, 128 + k * 50 - 14, 80, 30, r=7, fill=col)
                    c.text(302, 128 + k * 50 + 1, lb, 17, INK if lb == "Cloth" else (255, 255, 255), "s")
        if i == 2: E.zone_icon(c, 180, cy, 1.55, color=CREAM, t=t)
        if i == 3: E.odoo_chip(c, 180, cy, 1.3)
        c.text(180, 340, lab, 36, CREAM, "b")
        c.text(180, 388, sub, 23, (176, 156, 210), "r")
        place(fr, c.out(), rr_mask(360, 440, 30), xs[i], 600 + (1 - p) * 70 + math.sin(t * 1.0 + i) * 5, s=0.96, pitch=(1 - p) * -14, yaw=(xs[i] - 960) / 120 * (0.4 + 0.6 * (1 - p)),
              a=p, blur=(1 - p) * 10, shadow=0.55, glow=((140, 100, 230), 0.35 * p, 14))
    return fr

def scene6(t, d):
    u = t / d
    fr = bg_dark(t, 1.0, cx=W * 0.35)
    particles(fr, t, 18, seed=17)
    st = dict(t=t, worker_x=560, worker_a=ease_out(seg(t, 0.6, 0.5)), mode="mark", mark_p=0.0, cut_p=0.0, zone=ease_out(seg(t, 1.0, 0.6)), hand_t=t, tag=None, tool_a=1.0)
    st["boxes"] = ease_out(seg(t, 1.8, 0.5))
    mark_p = seg(u, 0.22, 0.34)
    cutting = u > 0.60
    st["mode"] = "cut" if cutting else "mark"
    st["cut_p"] = seg(u, 0.62, 0.30) * 0.96 + (seg(u, 0.93, 0.05) * 0.04)
    if not cutting:
        st["mark_p"] = mark_p if u > 0.22 else 0.0
        st["tag"] = ("Marking", E.BLUE) if u > 0.22 else None
    else:
        st["mark_p"] = 1.0; st["tag"] = ("Cutting", AMBER); st["tool_a"] = ease_out(seg(u, 0.60, 0.04))
    p = ease_out(seg(t, 0.2, 1.0))
    zoom = lerp(0.78, 0.9, ease_io(u))
    place(fr, cam_tile(st), rr_mask(1080, 700, 34), 610 + (1 - p) * -120, 560, s=zoom, yaw=lerp(-16, -6, ease_io(u)), pitch=lerp(5, 1.5, ease_io(u)), a=p, shadow=0.6, glow=((140, 100, 230), 0.3 * p, 12))
    p2 = ease_out(seg(t, 0.8, 1.0))
    place(fr, odoo_tile(u, t), rr_mask(560, 700, 30), 1560 + (1 - p2) * 140, 560, s=0.82, yaw=lerp(14, 6, ease_io(u)), pitch=lerp(5, 1.5, ease_io(u)), a=p2, shadow=0.6, glow=((140, 100, 230), 0.3 * p2, 12))
    kinetic(fr, [words("Marking begins."), ], 960, 110, 58, t, 0.9, t_out=d * 0.58, per=0.14)
    kinetic(fr, [[("Scissors", None), ("out.", None), ("Cutting", AMBER), ("starts.", AMBER)]], 960, 110, 58, t, d * 0.62, per=0.14)
    return fr

def scene7(t, d):
    u = t / d
    fr = bg_dark(t, 1.0, cx=W * 0.7)
    particles(fr, t, 16, seed=19)
    away = seg(u, 0.12, 0.28); wa = 1 - seg(u, 0.30, 0.12)
    st = dict(t=t, worker_x=560 + ease_io(away) * 380, worker_a=wa, mode="idle" if u < 0.12 else "away", mark_p=1.0, cut_p=1.0,
              boxes=ease_out(seg(u, 0.0, 0.1)) * (1 - seg(u, 0.30, 0.1)), zone=1.0, hand_t=t, tag=("Idle", (120, 112, 140)) if u > 0.1 else ("Cutting", AMBER))
    p = ease_out(seg(t, 0.1, 0.9))
    place(fr, cam_tile(st), rr_mask(1080, 700, 34), 560 + (1 - p) * -100, 560, s=0.62, yaw=-14, pitch=3, a=p, shadow=0.55, glow=((140, 100, 230), 0.25 * p, 12))
    # countdown ring
    cx, cy, R = 1390, 480, 190
    prog = seg(u, 0.30, 0.46); done = seg(u, 0.80, 0.10)
    pr = ease_out(seg(t, 0.4, 0.8))
    size = 520
    col = tuple(int(lerp(a_, b_, done)) for a_, b_ in zip(AMBER, MINT))
    track = ring_mask(R, 16, 1.0, size)
    paste_col(fr, (70, 46, 100), track, cx - size / 2, cy - size / 2, pr)
    frac = 1 - prog if done < 0.01 else 1.0
    rm = ring_mask(R, 16, frac, size)
    paste_col(fr, col, rm.filter(GB(14)), cx - size / 2, cy - size / 2, 0.85 * pr)
    paste_col(fr, col, rm, cx - size / 2, cy - size / 2, pr)
    if done < 1:
        secs = max(0, math.ceil(15 * (1 - prog))) if u >= 0.30 else 15
        text_center(fr, str(secs), 150, "sl", cx, cy - 8, CREAM, pr * (1 - done))
        text_center(fr, "seconds without work", 26, "r", cx, cy + 96, MUTED_D, pr * (1 - done))
    if done > 0:
        m = Mk(size, size); m.line([(cx - cx + size / 2 - 55, size / 2 + 4), (size / 2 - 14, size / 2 + 46), (size / 2 + 62, size / 2 - 40)], 11)
        mm = m.get()
        paste_col(fr, MINT, mm.filter(GB(14)), cx - size / 2, cy - size / 2, 0.8 * done)
        paste_col(fr, MINT, mm, cx - size / 2, cy - size / 2, done)
    text_center(fr, "Cutting", 40, "sl", cx, cy + 270, CREAM, pr)
    lab = "In progress" if u < 0.8 else "Completed"
    tl, mk = pill_tile(lab, 28, (255, 255, 255) if u >= 0.8 else INK, (22, 160, 106) if u >= 0.8 else (245, 190, 80), h=58, padx=34)
    place(fr, tl, mk, cx, cy + 330, a=pr)
    pp = back(seg(u, 0.88, 0.10))
    tl, mk = pill_tile("Manufacturing order: Done", 30, MINT, (14, 38, 30), icon=None, padx=40, h=74)
    place(fr, tl, mk, cx, cy + 420, s=0.9 + 0.1 * clamp(pp), a=clamp(pp), glow=(MINT, 0.7, 16))
    return fr

def scene8(t, d):
    fr = bg_light(t, 1.0)
    particles(fr, t, 18, dark=False, seed=23)
    kinetic(fr, [words("What you get")], 960, 140, 74, t, 0.4, per=0.2, dark=False)
    cards = [("No manual entry", "Operations start and finish by themselves"), ("Live production status", "See what is running right now"), ("A clear time picture", "Know where the hours really go")]
    starts = [0.9, 3.1, 5.4]
    for i, (h_, sub) in enumerate(cards):
        p = ease_out(seg(t, starts[i], 0.9))
        c = Ctx((255, 255, 255), 500, 500)
        c.rect(1, 1, 498, 498, r=34, line=(232, 222, 250), lw=2)
        cy = 190
        if i == 0:
            c.rect(175, cy - 74, 150, 150, r=16, line=INK, lw=6)
            for k in range(3): c.line([(205, cy - 34 + k * 36), (295, cy - 34 + k * 36)], (214, 206, 232), 8)
            c.line([(150, cy - 94), (350, cy + 96)], E.RED, 12)
        if i == 1:
            pts = [(110, cy + 6), (190, cy + 6), (225, cy - 74), (270, cy + 76), (305, cy + 6), (370, cy + 6)]
            c.line(pts, INK, 9)
            c.ell(392, cy + 6, 14 + 5 * (0.5 + 0.5 * math.sin(t * 5)), fill=E.GREEN)
        if i == 2:
            rows = [((E.BLUE, 0.0, 0.34), (AMBER, 0.38, 0.30), ((226, 220, 238), 0.72, 0.28)), ((AMBER, 0.0, 0.2), ((226, 220, 238), 0.24, 0.2), (E.BLUE, 0.48, 0.36)), ((E.BLUE, 0.0, 0.5), ((226, 220, 238), 0.54, 0.46))]
            for r_, row in enumerate(rows):
                for col, a0, w_ in row:
                    c.rect(100 + 300 * a0, cy - 66 + r_ * 56, 300 * w_ - 6, 38, r=10, fill=col)
        c.text(250, 358, h_, 36, INK, "b")
        c.text(250, 412, sub, 22, MUTED_L, "r")
        place(fr, c.out(), rr_mask(500, 500, 34), 400 + i * 560 + 0 * i, 540 + (1 - p) * 80 + math.sin(t * 1.0 + i * 1.3) * 7, s=1.0, pitch=(1 - p) * -10, a=p,
              shadow=0.32, shadow_col=(110, 70, 190), sh_blur=34, sh_dy=30, blur=(1 - p) * 8)
    return fr

def scene9(t, d):
    fr = bg_light(t, 1.25)
    particles(fr, t, 22, dark=False, seed=29)
    for k in range(3):
        u = (t * 0.3 + k / 3) % 1.0
        R = 150 + u * 420
        m = Mk(int(R * 2 + 30), int(R * 2 + 30)); m.ell(R + 15, R + 15, R, fill=0, outline=255, lw=3)
        paste_col(fr, VIO, m.get(), 960 - R - 15, 360 - R - 15, (1 - u) * 0.2 * ease_out(seg(t, 0.4, 0.8)))
    p = back(seg(t, 0.2, 0.7))
    tl, mk = brand_tile(260)
    place(fr, tl, mk, 960, 360, s=p, a=clamp(p * 2), shadow=0.28, shadow_col=(80, 40, 160), sh_blur=34, sh_dy=30, glow=(VIO, 0.35, 20))
    kinetic(fr, [[("SKIT", None), ("AI", None), ("Vision", None)]], 960, 600, 124, t, 0.9, wt="sl", per=0.16, dark=False)
    a = ease_out(seg(t, 2.3, 0.7))
    tl, mk2 = pill_tile("Your camera, connected to Odoo.", 36, CREAM, (19, 6, 28), icon="spark", padx=46, h=84)
    place(fr, tl, mk2, 960, 770 + (1 - a) * 26, s=0.9 + 0.1 * a, a=a, glow=(VIO, 0.6 + 0.3 * math.sin(t * 2), 18), shadow=0.35, shadow_col=(90, 50, 170), sh_blur=26, sh_dy=22)
    return fr


SCENES = [scene1, scene2, scene3, scene4, scene5, scene6, scene7, scene8, scene9]
TRANS = ["zoom", "zoom", "star", "zoom", "zoom", "zoom", "star", "slide"]

def zoomed(im, z):
    w, h = im.size
    cw, ch = w / z, h / z
    return im.crop((int((w - cw) / 2), int((h - ch) / 2), int((w + cw) / 2), int((h + ch) / 2))).resize((w, h), Image.BICUBIC)

def star_mask(p):
    R = 40 + 2500 * ease_io(p) ** 1.15
    m = Image.new("L", (W // 2, H // 2), 0)
    ImageDraw.Draw(m).polygon([(x / 2, y / 2) for x, y in star_pts(W / 2, H / 2, R)], fill=255)
    return m.resize((W, H), Image.BILINEAR).filter(GB(5))

def transition(kind, A, B, p):
    if kind == "zoom":
        e = ease_io(p)
        return Image.blend(zoomed(A, 1 + 0.18 * e), zoomed(B, 1.14 - 0.14 * e), e)
    if kind == "star":
        M = star_mask(p)
        out = Image.composite(B, A, M)
        edge = M.filter(GB(22)).point(lambda v: int(255 * max(0.0, 1 - abs(v - 127.5) / 127.5) ** 1.6))
        out.paste((196, 164, 255), (0, 0), edge)
        return out
    if kind == "slide":
        e = ease_io(p)
        out = A.copy()
        out = Image.blend(A, B, e)
        off = int((1 - e) * 220)
        out.paste(B.crop((0, 0, W - off, H)), (off, 0), scale_mask(Image.new("L", (W - off, H), 255), e))
        return out

def load_tl(): return json.loads((HERE / "timeline.json").read_text("utf8"))

def frame_at(T, tl):
    starts, durs = tl["starts"], tl["durs"]
    n = len(SCENES)
    hh = TR / 2
    for i in range(n - 1):
        tb = starts[i + 1]
        if tb - hh <= T < tb + hh:
            A = SCENES[i](T - starts[i], durs[i])
            B = SCENES[i + 1](max(0.0, T - starts[i + 1]), durs[i + 1])
            img = transition(TRANS[i], A, B, (T - (tb - hh)) / TR)
            break
    else:
        i = max(k for k in range(n) if starts[k] <= T + 1e-9)
        img = SCENES[i](T - starts[i], durs[i])
    # tiny brand mark (top-left) on the scenes where the big logo is not on screen
    lum = np.asarray(img.crop((40, 28, 330, 70))).mean()
    col = INK if lum > 140 else CREAM
    show = 1.0 if not (starts[3] - 0.5 < T < starts[4] + 0.5 or T > starts[8] - 0.5) and T > 1.0 else 0.0
    if show:
        put_text(img, "SKIT AI Vision", 24, "s", 78, 36, col, 0.55)
        bt, bm = brand_tile(40)
        img.paste(bt, (28, 28), scale_mask(bm, 0.9))
    T_end = tl["total"]
    if T < 0.5:
        img = Image.blend(Image.new("RGB", (W, H), DARK), img, ease_out(T / 0.5))
    if T > T_end - 0.7:
        img = Image.blend(img, Image.new("RGB", (W, H), LIGHT), seg(T, T_end - 0.7, 0.7))
    return img

_TL = None
def render_frame(n):
    global _TL
    if _TL is None: _TL = load_tl()
    return np.asarray(frame_at(n / FPS, _TL).convert("RGB")).tobytes()


# ---------------------------------------------------------------- audio: voice + soft pad + whooshes, captions
def prep():
    items = json.loads((HERE / "narration.json").read_text("utf8"))
    starts, durs, clips = [], [], []
    T = 0.0
    for it in items:
        w = wave.open(str(HERE / (it["id"] + ".wav")))
        n = w.getnframes() / w.getframerate()
        d = LEAD + n + TAIL
        starts.append(round(T, 3)); durs.append(round(d, 3)); clips.append((T + LEAD, n, it))
        T += d
    total = T
    sr = 44100
    N = int(sr * (total + 0.5))
    voice = np.zeros(N, dtype=np.float32)
    for at, n, it in clips:
        w = wave.open(str(HERE / (it["id"] + ".wav")))
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        s0 = int(at * sr); voice[s0:s0 + len(raw)] = raw
    tt = np.arange(N) / sr
    pad = np.zeros(N, np.float32)
    for f, g in ((110.0, 1.0), (164.8, 0.8), (220.0, 0.6), (277.2, 0.4), (329.6, 0.3)):
        pad += g * np.sin(2 * np.pi * f * tt + 0.7 * np.sin(2 * np.pi * 0.11 * tt))
    pad *= (0.65 + 0.35 * np.sin(2 * np.pi * 0.09 * tt))
    pad *= np.minimum(1, tt / 2.5) * np.minimum(1, (total - tt).clip(0) / 2.5)
    pad *= 0.045 / (np.abs(pad).max() + 1e-6)
    rng = np.random.default_rng(1)
    sfx = np.zeros(N, np.float32)
    for k in range(len(starts) - 1):
        tb = starts[k + 1]
        L = int(sr * 1.1)
        noise = rng.standard_normal(L).astype(np.float32)
        spec = np.fft.rfft(noise)
        fr = np.fft.rfftfreq(L, 1 / sr)
        spec *= np.exp(-((np.log(fr + 1) - np.log(1800)) / 0.9) ** 2)
        wsh = np.fft.irfft(spec, L)
        env = np.sin(np.linspace(0, np.pi, L)) ** 3
        wsh = wsh * env
        wsh *= 0.12 / (np.abs(wsh).max() + 1e-6)
        s0 = int(sr * (tb - 0.55))
        sfx[s0:s0 + L] += wsh[: N - s0] if s0 + L > N else wsh
    mix_ = voice * 1.0 + pad + sfx
    mix_ /= max(1.0, np.abs(mix_).max() / 0.95)
    out = wave.open(str(HERE / "audio2.wav"), "wb")
    out.setnchannels(1); out.setsampwidth(2); out.setframerate(sr); out.writeframes((mix_ * 32767).astype(np.int16).tobytes()); out.close()
    (HERE / "timeline.json").write_text(json.dumps({"starts": starts, "durs": durs, "total": round(total, 3)}), "utf8")
    # captions (.srt)
    def ts(x):
        h, r = divmod(x, 3600); m, s = divmod(r, 60); return "%02d:%02d:%02d,%03d" % (h, m, int(s), int((s % 1) * 1000))
    with open(HERE / "captions.srt", "w", encoding="utf8") as f:
        for i, (at, n, it) in enumerate(clips, 1):
            f.write(f"{i}\n{ts(at)} --> {ts(at + n)}\n{it['show']}\n\n")
    print("scenes", [round(x, 1) for x in durs], "total", round(total, 1))

def render(outfile):
    tl = load_tl()
    frames = int(math.ceil(tl["total"] * FPS))
    cmd = [str(HERE / "ffmpeg.exe"), "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", str(HERE / "audio2.wav"), "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", outfile]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    with Pool(6) as pool:
        for k, buf in enumerate(pool.imap(render_frame, range(frames), chunksize=3)):
            p.stdin.write(buf)
            if k % 120 == 0: print(f"frame {k}/{frames}", flush=True)
    p.stdin.close(); p.wait()
    print("done", outfile)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "prep": prep()
    elif cmd == "still":
        tl = load_tl()
        for s in sys.argv[2:]:
            frame_at(float(s), tl).save(HERE / f"v2_{float(s):05.1f}.png")
        print("saved")
    elif cmd == "render": render(sys.argv[2])
