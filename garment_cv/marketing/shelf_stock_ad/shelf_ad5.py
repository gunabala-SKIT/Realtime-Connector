"""SKIT AI Vision - Shelf Stock Monitoring + Odoo: realistic enterprise advertisement (v3).

Real footage plates (Pexels, free commercial licence) + cinematic grade, film grain, 2.39:1 frame, clean enterprise UI
layers and AI detection overlays composited on top. Voice: neural TTS. Music: synthesized.

    python shelf_ad3.py prep | still 5 12 ... | render out.mp4
"""
import json, math, os, subprocess, sys, wave
from functools import lru_cache
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter
from scipy import signal as sps

import explainer3 as X
from explainer3 import (E, V, Ctx, Mk, W, H, FPS, GB, brand_tile, burst, ripple, clamp, ease_io, ease_out, back, lerp, mix, paste_col,
                        put_text, rr_mask, scale_mask, seg, text_center, twidth, kinetic, words)
import music2 as music

HERE = Path(__file__).resolve().parent
PLATES = HERE / "plates"
GAP, TAIL = 0.22, 0.55
BAR = 139                                        # 2.39:1 letterbox
WHITE, INK = (255, 255, 255), (22, 27, 32)
EMER, ORANGE, SOFT, MUT = (22, 170, 110), (255, 98, 56), (246, 247, 246), (104, 112, 118)
GRAPH = (14, 18, 22)

# ---------------------------------------------------------------- plates + grade
@lru_cache(maxsize=None)
def nframes(name): return len(os.listdir(PLATES / name))

def plate(name, t, speed=1.0):
    n = nframes(name)
    idx = int(clamp(t * FPS * speed, 0, n - 1)) + 1
    return Image.open(PLATES / name / f"{idx:04d}.jpg").convert("RGB")

def push(img, z, dx=0.0, dy=0.0):
    if z <= 1.0005 and not dx and not dy: return img
    cw, ch = W / z, H / z
    x0 = (W - cw) / 2 + dx * (W - cw) / 2; y0 = (H - ch) / 2 + dy * (H - ch) / 2
    return img.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch))).resize((W, H), Image.BICUBIC)

_gy, _gx = np.mgrid[0:H, 0:W].astype(np.float32)
VIGN = (1.0 - 0.34 * (((_gx - W / 2) / (W / 2)) ** 2 * 0.55 + ((_gy - H / 2) / (H / 2)) ** 2 * 0.95)).clip(0.5, 1.0)[..., None]
_rng = np.random.default_rng(11)
GRAIN = [(_rng.standard_normal((H // 2, W // 2, 1)).astype(np.float32) * 1.0) for _ in range(6)]

def grade(img, exposure=0.97, teal=1.0, warm=1.0, sat=0.92):
    a = np.asarray(img).astype(np.float32) / 255.0
    a = a * a * (3 - 2 * a) * 0.5 + a * 0.5                      # gentle S-curve
    lum = a.mean(axis=2, keepdims=True)
    a = a + (1 - lum) ** 2 * np.array([-0.025, 0.02, 0.045], np.float32) * teal + lum ** 2 * np.array([0.04, 0.014, -0.03], np.float32) * warm
    a = lum + (a - lum) * sat
    a = a * exposure
    out = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))
    small = out.resize((W // 4, H // 4), Image.BILINEAR)
    hi = small.point(lambda v: int(min(255, max(0, v - 185) * 3.2))).filter(GB(6)).resize((W, H), Image.BILINEAR)
    hi = ImageChops.multiply(hi, Image.new("RGB", (W, H), (255, 214, 176)))               # warm halation
    out = ImageChops.screen(out, hi.point(lambda v: int(v * 0.5)))
    arr = np.asarray(out).astype(np.float32) * VIGN
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

def scrim(img, y0, y1, a0, a1, color=(6, 10, 14)):
    """vertical dark gradient between y0..y1 (alpha a0 -> a1) for legible type"""
    h = max(2, int(y1 - y0))
    vals = ((a0 + (a1 - a0) * np.linspace(0, 1, h)) * 255).clip(0, 255).astype(np.uint8)
    m = Image.fromarray(np.tile(vals[:, None], (1, W)))
    img.paste(color, (0, int(y0)), m)

def seq(t, makers, cuts, d=0.6):
    i = 0
    while i < len(cuts) and t >= cuts[i] + d / 2: i += 1
    img = makers[i]()
    if i < len(cuts) and t > cuts[i] - d / 2:
        nxt = makers[i + 1]()
        img = Image.blend(img, nxt, ease_io((t - (cuts[i] - d / 2)) / d))
    return img

def dust(fr, t, n, seed, a=0.35):
    rng = np.random.default_rng(seed)
    for i in range(n):
        x0, y0 = rng.uniform(0, W), rng.uniform(BAR, H - BAR)
        sp, ph = rng.uniform(6, 20), rng.uniform(0, 6.28)
        x, y = (x0 + math.sin(t * 0.3 + ph) * 26) % W, BAR + ((y0 - BAR - t * sp) % (H - 2 * BAR))
        m = V._dot(rng.uniform(1.4, 3.0))
        paste_col(fr, WHITE, m, x - m.size[0] / 2, y - m.size[1] / 2, a * (0.4 + 0.6 * math.sin(t * 1.3 + ph) ** 2))

def shot(name, t, t0, dur, z0=1.0, z1=1.07, dx=0.0, dy=0.0, speed=1.0, **g):
    u = clamp((t - t0) / dur)
    img = plate(name, max(0.0, t - t0), speed)
    img = push(img, lerp(z0, z1, ease_io(u)), dx * u, dy * u)
    return grade(img, **g)

def xfade(a, b, t, tc, d=0.5):
    p = ease_io(clamp((t - (tc - d / 2)) / d))
    return b if p >= 1 else a if p <= 0 else Image.blend(a, b, p)

# ---------------------------------------------------------------- UI building blocks
def chip(fr, x, y, text, size=22, fg=WHITE, bg=(12, 16, 20), alpha=0.66, dot=None, a=1.0, h=None, anchor="l", wt="sl", padx=22):
    if a <= 0.01: return 0
    tw = twidth(text, size, wt)
    h = h or int(size * 2.0)
    w = int(tw + padx * 2 + (size * 1.1 if dot else 0))
    x0 = x if anchor == "l" else x - w / 2
    m = Mk(w, h); m.rr(0, 0, w, h, h / 2)
    mm = m.get()
    paste_col(fr, bg, mm, x0, y - h / 2, alpha * a)
    ring = ImageChops.subtract(mm, mm.filter(ImageFilter.MinFilter(3)))
    paste_col(fr, WHITE, ring, x0, y - h / 2, 0.18 * a)
    tx = x0 + padx
    if dot:
        d = Mk(int(size * 0.6) + 4, int(size * 0.6) + 4); d.ell(d.w / 2, d.h / 2, size * 0.26)
        paste_col(fr, dot, d.get(), tx - 2, y - d.h / 2, a)
        tx += size * 1.1
    put_text(fr, text, size, wt, tx, y - size * 0.66, fg, a)
    return w

def hud_box(fr, x0, y0, x1, y1, label=None, a=1.0, col=WHITE, lw=2, corner=34, conf=None):
    if a <= 0.01: return
    w, h = int(x1 - x0) + 16, int(y1 - y0) + 16
    m = Mk(w, h)
    m.d.rectangle((8 * 2, 8 * 2, (w - 8) * 2, (h - 8) * 2), outline=110, width=lw * 2)
    mm = m.get()
    paste_col(fr, col, mm, x0 - 8, y0 - 8, 0.55 * a)
    c = Mk(w, h)
    for (px, py, sx, sy) in ((8, 8, 1, 1), (w - 8, 8, -1, 1), (8, h - 8, 1, -1), (w - 8, h - 8, -1, -1)):
        c.line([(px, py + sy * corner), (px, py), (px + sx * corner, py)], lw + 2)
    paste_col(fr, col, c.get(), x0 - 8, y0 - 8, a)
    if label:
        chip(fr, x0, y0 - 24, label if conf is None else f"{label}  {conf:.2f}", 19, WHITE, (12, 16, 20), 0.7, a=a, h=34, padx=14)

def hud_corner(fr, a):
    pass

def tile_pt(px, py, w, h, cx, cy, s, yaw, pitch):
    quad = V.proj(w, h, s, yaw, pitch, 0, cx, cy)
    cf = V.persp_coeffs(quad, [(0, 0), (w, 0), (w, h), (0, h)])
    a, b, c_, d, e, f, g, hh = cf
    den = g * px + hh * py + 1
    return ((a * px + b * py + c_) / den, (d * px + e * py + f) / den)

def bezier(p0, p1, p2, u):
    return ((1 - u) ** 2 * p0[0] + 2 * (1 - u) * u * p1[0] + u * u * p2[0], (1 - u) ** 2 * p0[1] + 2 * (1 - u) * u * p1[1] + u * u * p2[1])

def card_tile(w, h, r=26, fill=WHITE):
    c = Ctx(fill, w, h)
    return c

# --- timing helpers (cues)
_CUES = None
def CU():
    global _CUES
    if _CUES is None: _CUES = json.load(open(HERE / "cues.json"))
    return _CUES
def cue(sid, k): return CU()[sid][k]
def cue_end(sid, k): return CU()[sid + "_end"][k]

START_ON_HAND, MIN_STOCK = 15, 12
SYNC_DELAY, SHELF_DELAY = 1.05, 0.30
def pick_times():
    a0 = cue("core", 2)
    return [a0 - 6.0, a0 - 4.2, a0 - 2.6, a0 - 1.15]
def alert_time(): return pick_times()[-1] + SYNC_DELAY
def shelf_count(t): return START_ON_HAND - sum(1 for p in pick_times() if t >= p + SHELF_DELAY)
def odoo_count(t): return START_ON_HAND - sum(1 for p in pick_times() if t >= p + SYNC_DELAY)

PRODUCTS = [("Carton box L", (200, 156, 100), None, MIN_STOCK), ("Mailer box M", (222, 196, 150), 24, 8), ("Packing tape", (240, 214, 120), 18, 10)]

def odoo_card(t, hero, change_t=None, prev=None, synced=0.0):
    c = Ctx(WHITE, 600, 700)
    E.odoo_chip(c, 112, 52, 0.78)
    c.text(40, 130, "Inventory · On Hand", 36, INK, "b", anchor="lm")
    c.text(40, 170, "WH / Stock / Aisle A3", 22, MUT, "r", anchor="lm")
    for i, (name, col, oh, mn) in enumerate(PRODUCTS):
        y = 200 + i * 150
        is_h = oh is None
        oh = hero if is_h else oh
        below, near = oh < mn, oh == mn
        c.rect(24, y, 552, 134, r=18, fill=(255, 241, 237) if below else (249, 250, 249), line=ORANGE if below else (228, 231, 229), lw=3 if below else 2)
        c.rect(44, y + 22, 54, 54, r=12, fill=col)
        c.rect(44, y + 22, 54, 54, r=12, line=(0, 0, 0, 40), lw=2)
        c.text(116, y + 34, name, 28, INK, "s", anchor="lm")
        c.text(116, y + 66, f"Minimum  {mn}", 21, MUT, "r", anchor="lm")
        if is_h and change_t is not None and prev is not None:
            u = seg(t, change_t, 0.45)
            c.text(500, y + 40 + (1 - ease_out(u)) * -26, str(oh), 56, INK, "b", anchor="mm", a=ease_out(u))
            c.text(500, y + 40 + ease_out(u) * 26, str(prev), 56, INK, "b", anchor="mm", a=1 - ease_out(u))
            if u < 1: c.pill(432, y + 100, "−1", WHITE, ORANGE, size=20, padx=12, h=30, a=1 - u * 0.2)
        else:
            c.text(500, y + 40, str(oh), 56, INK, "b", anchor="mm")
        c.text(500, y + 80, "on hand", 17, MUT, "r", anchor="mm")
        c.rect(44, y + 108, 360, 10, r=5, fill=(228, 232, 230))
        bc = ORANGE if below else ((255, 186, 40) if near else EMER)
        c.rect(44, y + 108, max(10, 360 * clamp(oh / 28.0)), 10, r=5, fill=bc)
        c.rect(44 + 360 * mn / 28.0 - 2, y + 100, 4, 26, r=2, fill=INK)
        if below: c.pill(476, y + 113, "Below minimum", WHITE, ORANGE, size=17, padx=12, h=30)
        elif near: c.pill(500, y + 113, "At minimum", INK, (255, 214, 110), size=17, padx=12, h=30)
        else: c.pill(504, y + 113, "In stock", (10, 96, 60), (208, 238, 222), size=17, padx=12, h=30)
    if synced > 0:
        c.ell(52, 664, 7 + 2 * math.sin(t * 6), fill=EMER, a=synced)
        c.text(70, 664, "Synced from shelf camera · live", 20, (10, 100, 62), "s", anchor="lm", a=synced)
    return c.out()

def bell_icon(c, cx, cy, s=1.0, col=WHITE, swing=0.0):
    ang = swing
    def R(x, y): return (cx + (x * math.cos(ang) - (y + 26) * math.sin(ang)) * s, cy - 26 * s + (x * math.sin(ang) + (y + 26) * math.cos(ang)) * s)
    c.poly([R(-26, 22), R(-22, -8), R(-12, -26), R(12, -26), R(22, -8), R(26, 22)], fill=col)
    c.rect(cx - 30 * s, cy + 18 * s, 60 * s, 9 * s, r=4 * s, fill=col)
    c.ell(cx, cy + 35 * s, 7 * s, fill=col)

def toast(t, t0, w=640, title="Replenishment required", body="Carton box L · 11 on hand · minimum 12 · Aisle A3"):
    c = Ctx(WHITE, w, 150)
    c.rect(0, 0, 10, 150, fill=ORANGE)
    c.ell(80, 75, 40, fill=ORANGE)
    bell_icon(c, 80, 76, 0.95, swing=0.35 * math.sin((t - t0) * 16) * math.exp(-(t - t0) * 1.2))
    c.text(142, 52, title, 34, INK, "b", anchor="lm")
    c.text(142, 92, body, 22, MUT, "r", anchor="lm")
    c.pill(214, 128, "Warehouse team", INK, (234, 238, 236), size=16, padx=12, h=28)
    c.pill(364, 128, "Notified", (10, 96, 60), (208, 238, 222), size=16, padx=12, h=28)
    return c.out(), rr_mask(w, 150, 26)

def text_block(fr, lines, cx, cy, size, t, t0, dark=True, per=0.16, t_out=None, lh=1.18, wt="l", underline=None):
    kinetic(fr, lines, cx, cy, size, t, t0, wt=wt, per=per, dur=0.8, t_out=t_out, lh=lh, dark=dark)

def letterbox(img):
    img.paste((0, 0, 0), (0, 0, W, BAR)); img.paste((0, 0, 0), (0, H - BAR, W, H))
    return img

def add_grain(img, idx):
    g = GRAIN[idx % 6]
    arr = np.asarray(img).astype(np.float32)
    gg = np.repeat(np.repeat(g, 2, axis=0), 2, axis=1)[:H, :W]
    return Image.fromarray(np.clip(arr + gg * 2.6, 0, 255).astype(np.uint8))

# ---------------------------------------------------------------- scenes
def scene_hook(t, d):
    A, B = cue("p1", 0), cue("p1", 1)
    t1, t2 = 6.4, 10.6
    fr = seq(t, [lambda: shot("H1", t, 0.0, 7.0, 1.0, 1.08), lambda: shot("H2", t, t1, 4.8, 1.0, 1.06, dx=0.3), lambda: shot("H3", t, t2, 5.4, 1.02, 1.08, dx=-0.2)], [t1, t2])
    scrim(fr, 380, 620, 0.0, 0.5)
    scrim(fr, 620, 860, 0.5, 0.0)
    dust(fr, t, 26, 3)
    text_block(fr, [words("Shelves empty faster"), words("than stock records can keep up.")], 960, 600, 96, t, A + 0.1, t_out=B - 0.15)
    if t > t1 - 0.3:
        for i, (lab, ts) in enumerate([("Manual counts", B + 0.2), ("Odoo updated late", B + 2.3), ("Noticed when it's gone", B + 4.9)]):
            a = ease_out(seg(t, ts, 0.5))
            chip(fr, 130 - (1 - a) * 40, 670 + i * 92, lab, 36, WHITE, (10, 14, 18), 0.72, dot=ORANGE, a=a, h=70, padx=32)
        a = ease_out(seg(t, B + 1.4, 0.6))
        if a > 0:
            tl, mk = stale_card(a)
            V.place(fr, tl, mk, 1500 + (1 - a) * 80, 360, s=1.15, yaw=-8, pitch=2, a=a, shadow=0.55, sh_blur=28, sh_dy=22)
    return fr

def scene_idea(t, d):
    A = cue("i", 0)
    fr = shot("I1", t, 0.0, 5.6, 1.02, 1.12, speed=0.94, exposure=0.78)
    scrim(fr, BAR, H - BAR, 0.38, 0.38)
    dust(fr, t, 20, 5)
    on = ease_out(seg(t, 0.1, 0.8))
    m = Mk(W, H)
    L, T_, R_, B_ = 170, BAR + 40, W - 170, H - BAR - 40
    for sx, sy, x, y in ((1, 1, L, T_), (-1, 1, R_, T_), (1, -1, L, B_), (-1, -1, R_, B_)):
        m.line([(x, y + sy * 70), (x, y), (x + sx * 70, y)], 4)
    paste_col(fr, WHITE, m.get(), 0, 0, 0.9 * on)
    sc = ((t - 0.2) * 0.28) % 1.0
    sx = lerp(L, R_, ease_io(sc))
    sm = Mk(60, H); sm.rr(27, T_ + 6, 5, B_ - T_ - 12, 2)
    paste_col(fr, (150, 255, 210), sm.get().filter(GB(6)), sx - 30, 0, 0.55 * on); paste_col(fr, (210, 255, 235), sm.get(), sx - 30, 0, 0.8 * on)
    chip(fr, L + 14, T_ + 56, "REC", 26, WHITE, (12, 16, 20), 0.7, dot=ORANGE if int(t * 2) % 2 == 0 else (120, 50, 30), a=on, h=46, padx=18)
    chip(fr, L + 150, T_ + 56, "SKIT AI VISION · SHELF A3", 24, (210, 225, 220), (12, 16, 20), 0.7, a=on, h=46, padx=18)
    text_block(fr, [words("The camera doesn't"), words("just detect low stock.")], 960, 540, 112, t, A, per=0.2)
    a = ease_out(seg(t, A + 1.6, 0.6))
    if a > 0:
        wlow = twidth("just detect low stock.", 112, "l")
        ul = Mk(int(wlow * a) + 4, 10); ul.rr(0, 2, int(wlow * a), 6, 3)
        paste_col(fr, EMER, ul.get(), 960 - wlow / 2, 540 + 112 * 0.62 + 52, 0.95)
    return fr

C1_BOXES = [(510, 216, 1000, 450, "Carton L", 0.97), (1006, 244, 1206, 444, "Carton L", 0.95),
            (556, 606, 766, 820, "Carton L", 0.93), (766, 500, 1280, 820, "Carton L", 0.98)]

# ---- the product window: live camera feed (real footage + detections) beside the Odoo On Hand panel
WIN_W, WIN_H = 1732, 672
FEED = (24, 88, 984, 648)            # x0,y0,x1,y1 inside the window tile
PAN = (1008, 88, 1708, 648)
CROPS = {"C1": (290, 130, 1200, 700), "C2": (0, 0, 1500, 875), "C3": (0, 100, 1500, 875)}
FEED_W, FEED_H = FEED[2] - FEED[0], FEED[3] - FEED[1]

def feed_pt(name, x, y, zoom=1.0):
    cx0, cy0, cw, ch = CROPS[name]
    cw2, ch2 = cw / zoom, ch / zoom
    cx0 += (cw - cw2) / 2; cy0 += (ch - ch2) / 2
    return (FEED[0] + (x - cx0) * FEED_W / cw2, FEED[1] + (y - cy0) * FEED_H / ch2)

def feed_and_bg(name, t, t0, dur, **g):
    u = clamp((t - t0) / dur)
    img = grade(plate(name, max(0.0, t - t0)), **g)
    zoom = 1.0 + 0.05 * ease_io(u)
    cx0, cy0, cw, ch = CROPS[name]
    cw2, ch2 = cw / zoom, ch / zoom
    cx0 += (cw - cw2) / 2; cy0 += (ch - ch2) / 2
    feed = img.crop((int(cx0), int(cy0), int(cx0 + cw2), int(cy0 + ch2))).resize((FEED_W * 2, FEED_H * 2), Image.BILINEAR)
    bg = img.resize((W // 6, H // 6), Image.BILINEAR).filter(GB(2.2)).resize((W, H), Image.BILINEAR).point(lambda v: int(v * 0.55))
    return feed, bg, zoom

def seq2(t, makers, cuts, d=0.5):
    i = 0
    while i < len(cuts) and t >= cuts[i] + d / 2: i += 1
    a = makers[i]()
    if i < len(cuts) and t > cuts[i] - d / 2:
        b = makers[i + 1]()
        p = ease_io((t - (cuts[i] - d / 2)) / d)
        return Image.blend(a[0], b[0], p), Image.blend(a[1], b[1], p), (a[2] if p < 0.5 else b[2]), (i if p < 0.5 else i + 1)
    return a[0], a[1], a[2], i

def c_label(c, x, y, text, size, fg, bg, a=1.0, padx=14, h=None, dot=None):
    h = h or int(size * 1.9)
    w = c.tw(text, size, "s") + padx * 2 + (size if dot else 0)
    c.rect(x, y - h / 2, w, h, r=h / 2, fill=bg, a=a)
    tx = x + padx
    if dot:
        c.ell(tx + size * 0.3, y, size * 0.26, fill=dot, a=a); tx += size
    c.text(tx, y - 1, text, size, fg, "s", anchor="lm", a=a)
    return w

def c_box(c, x0, y0, x1, y1, label, a=1.0, col=WHITE, lw=3, corner=36, conf=None):
    if a <= 0.01: return
    c.rect(x0, y0, x1 - x0, y1 - y0, r=4, line=col, lw=2, a=0.45 * a)
    for (px, py, sx, sy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        c.line([(px, py + sy * corner), (px, py), (px + sx * corner, py)], col, lw + 2, a=a)
    if label:
        txt = label if conf is None else f"{label}  {conf:.2f}"
        c_label(c, x0, max(y0 - 22, FEED[1] + 20), txt, 22, WHITE, (10, 14, 18, 190), a=a, h=36)

ODOO_PURPLE, ODOO_ACCENT = (113, 75, 103), (104, 78, 160)
COL_AV, COL_LOC, COL_ST = 372, 452, 548          # table columns (relative to the card)

def thumb(c, kind, cx, cy):
    if kind == "carton":
        c.rect(cx - 24, cy - 20, 48, 42, r=6, fill=(196, 150, 96))
        c.poly([(cx - 24, cy - 20), (cx - 14, cy - 30), (cx + 14, cy - 30), (cx + 24, cy - 20)], fill=(224, 186, 132))
        c.rect(cx - 5, cy - 20, 10, 20, fill=(238, 220, 180))
    elif kind == "mailer":
        c.ell(cx, cy, 25, 22, fill=(242, 236, 222)); c.ell(cx, cy, 12, 10, fill=(255, 255, 255)); c.ell(cx, cy, 25, 22, line=(214, 206, 188), lw=2)
    else:
        c.ell(cx, cy, 25, 22, fill=(242, 190, 50)); c.ell(cx, cy, 12, 10, fill=(255, 255, 255)); c.ell(cx, cy, 25, 22, line=(214, 160, 30), lw=2)

def cube_icon(c, cx, cy, r, col=WHITE):
    top = [(cx, cy - r), (cx + r * 0.87, cy - r * 0.5), (cx, cy), (cx - r * 0.87, cy - r * 0.5)]
    left = [(cx - r * 0.87, cy - r * 0.5), (cx, cy), (cx, cy + r), (cx - r * 0.87, cy + r * 0.5)]
    right = [(cx + r * 0.87, cy - r * 0.5), (cx, cy), (cx, cy + r), (cx + r * 0.87, cy + r * 0.5)]
    c.poly(top, fill=col, a=0.95); c.poly(left, fill=col, a=0.6); c.poly(right, fill=col, a=0.78)

PRODUCTS5 = [("Carton Box L", "carton", None, MIN_STOCK), ("Mailer Box M", "mailer", 32, 10), ("Packing tape", "tape", 18, 10)]

def odoo_panel(c, t, state):
    px0, py0 = PAN[0], PAN[1]
    PW, PH = PAN[2] - PAN[0], PAN[3] - PAN[1]
    c.rect(px0, py0, PW, PH, r=20, fill=(243, 241, 246))
    # --- purple navigation bar
    c.rect(px0, py0, PW, 56, r=20, fill=ODOO_PURPLE); c.rect(px0, py0 + 30, PW, 26, fill=ODOO_PURPLE)
    c.text(px0 + 20, py0 + 28, "odoo", 32, WHITE, "b", anchor="lm")
    mx = px0 + 108
    for i, lab in enumerate(["Inventory", "Overview", "Operations", "Products", "Reporting"]):
        w = c.tw(lab, 16, "s") + 14
        if i == 0: c.rect(mx - 4, py0 + 12, w, 32, r=9, fill=(255, 255, 255, 52))
        c.text(mx + 3, py0 + 28, lab, 16, WHITE, "s" if i == 0 else "r", anchor="lm", a=1.0 if i == 0 else 0.85)
        mx += w + 2
    sx = px0 + PW - 150
    c.ell(sx, py0 + 26, 8, line=WHITE, lw=2.5); c.line([(sx + 6, py0 + 32), (sx + 13, py0 + 39)], WHITE, 3)
    bell_icon(c, px0 + PW - 98, py0 + 28, 0.42, col=WHITE)
    c.ell(px0 + PW - 88, py0 + 16, 5, fill=(255, 77, 77))
    c.ell(px0 + PW - 44, py0 + 28, 16, fill=(255, 255, 255, 70)); c.text(px0 + PW - 44, py0 + 28, "G", 17, WHITE, "b")
    # --- Inventory on Hand card
    x, y, cw, ch = px0 + 12, py0 + 68, PW - 24, 356
    c.rect(x, y, cw, ch, r=18, fill=WHITE, line=(226, 228, 232), lw=2)
    c.ell(x + 52, y + 50, 28, fill=ODOO_ACCENT); cube_icon(c, x + 52, y + 50, 13)
    c.text(x + 96, y + 38, "Inventory on Hand", 30, INK, "b", anchor="lm")
    c.text(x + 96, y + 68, "Real-time stock across all locations", 18, MUT, "r", anchor="lm")
    for k in range(3): c.ell(x + cw - 26, y + 34 + k * 9, 2.6, fill=(130, 136, 142))
    c.rect(x + 14, y + 98, cw - 28, 38, r=8, fill=(246, 246, 249))
    c.text(x + 30, y + 118, "Product", 17, MUT, "s", anchor="lm"); c.text(x + COL_AV, y + 118, "Available", 17, MUT, "s", anchor="mm")
    c.text(x + COL_LOC, y + 118, "Location", 17, MUT, "s", anchor="lm"); c.text(x + COL_ST, y + 118, "Status", 17, MUT, "s", anchor="lm")
    c.rect(x + COL_AV + 44, y + 104, 2, 26, fill=(228, 230, 234))
    for i, (name, kind, oh, mn) in enumerate(PRODUCTS5):
        yr = y + 140 + i * 66
        is_h = oh is None
        oh = state["oc"] if is_h else oh
        low = oh <= mn
        if is_h and low: c.rect(x + 14, yr, cw - 28, 62, r=12, fill=(255, 245, 238))
        if i: c.rect(x + 20, yr - 2, cw - 40, 2, fill=(238, 240, 242))
        thumb(c, kind, x + 54, yr + 31)
        c.text(x + 92, yr + 22, name, 23, INK, "b", anchor="lm")
        c.text(x + 92, yr + 46, f"Minimum {mn}", 16, MUT, "r", anchor="lm")
        nx = x + COL_AV
        if is_h and state["chg"] is not None:
            u = seg(t, state["chg"], 0.45)
            c.text(nx, yr + 24 + (1 - ease_out(u)) * -26, str(oh), 32, INK, "b", anchor="mm", a=ease_out(u))
            c.text(nx, yr + 24 + ease_out(u) * 26, str(state["prev"]), 32, INK, "b", anchor="mm", a=1 - ease_out(u))
            if u < 1: c.pill(nx + 56, yr + 22, "−1", WHITE, ORANGE, size=17, padx=10, h=26, a=1 - u * 0.2)
        else:
            c.text(nx, yr + 24, str(oh), 32, INK, "b", anchor="mm")
        c.text(nx, yr + 49, "on hand", 14, MUT, "r", anchor="mm")
        c.text(x + COL_LOC, yr + 31, "WH/Stock", 18, MUT, "r", anchor="lm")
        if low:
            c.rect(x + COL_ST, yr + 15, 112, 32, r=16, fill=(255, 226, 170))
            c.ell(x + COL_ST + 18, yr + 31, 8, fill=(244, 150, 30)); c.text(x + COL_ST + 18, yr + 30, "!", 12, WHITE, "b")
            c.text(x + COL_ST + 32, yr + 31, "Low Stock", 16, (140, 78, 0), "s", anchor="lm")
        else:
            c.rect(x + COL_ST, yr + 15, 112, 32, r=16, fill=(38, 182, 122))
            c.line([(x + COL_ST + 11, yr + 31), (x + COL_ST + 16, yr + 36), (x + COL_ST + 25, yr + 25)], WHITE, 3)
            c.text(x + COL_ST + 32, yr + 31, "In Stock", 16, WHITE, "s", anchor="lm")
    if state["synced"] > 0:
        c.ell(x + cw - 20 - c.tw("Synced from camera · live", 15, "s") - 14, y + 74, 5 + 1.4 * math.sin(t * 6), fill=EMER, a=state["synced"])
        c.text(x + cw - 20, y + 74, "Synced from camera · live", 15, (10, 100, 62), "s", anchor="rm", a=state["synced"])
    # --- replenishment card (healthy placeholder -> alert with Create PO)
    y2 = y + ch + 12
    aa = state["alert_a"]
    ah = clamp(aa * 1.5)
    c.rect(x, y2, cw, 112, r=18, fill=WHITE, line=(226, 228, 232), lw=2)
    if ah < 1:
        c.ell(x + 52, y2 + 56, 28, fill=(214, 240, 226), a=1 - ah)
        c.line([(x + 40, y2 + 56), (x + 49, y2 + 65), (x + 66, y2 + 46)], (20, 140, 90), 5, a=1 - ah)
        c.text(x + 96, y2 + 44, "Stock levels healthy", 26, INK, "b", anchor="lm", a=1 - ah)
        c.text(x + 96, y2 + 76, "No replenishment needed", 18, MUT, "r", anchor="lm", a=1 - ah)
    if ah > 0:
        yo = (1 - ah) * 26
        c.rect(x + 2, y2 + 2, cw - 4, 108, r=17, fill=(255, 248, 244), a=ah)
        c.ell(x + 52, y2 + 56 + yo, 28, fill=(228, 222, 246), a=ah)
        c.xf(0, 0, 1.0, ah)
        bell_icon(c, x + 52, y2 + 58 + yo, 0.78, col=ODOO_ACCENT, swing=0.35 * math.sin((t - state["AL"]) * 16) * math.exp(-(t - state["AL"]) * 1.2))
        c.reset()
        c.text(x + 96, y2 + 34 + yo, "Replenishment required", 28, INK, "b", anchor="lm", a=ah)
        c.text(x + 96, y2 + 64 + yo, "Carton box L · 11 on hand · minimum: 12", 19, MUT, "r", anchor="lm", a=ah)
        c.ell(x + 104, y2 + 90 + yo, 5, fill=EMER, a=ah); c.text(x + 116, y2 + 90 + yo, "Warehouse team notified", 16, (10, 100, 62), "s", anchor="lm", a=ah)
        pul = 1 + 0.04 * math.sin(t * 5)
        bw, bh = 168 * pul, 58 * pul
        c.rect(x + cw - 194 - (bw - 168) / 2, y2 + 27 + yo - (bh - 58) / 2, bw, bh, r=17, fill=(104, 78, 160, 70), a=ah)
        c.rect(x + cw - 190, y2 + 29 + yo, 160, 54, r=16, fill=(92, 62, 140), a=ah)
        c.text(x + cw - 110, y2 + 55 + yo, "Create PO  ›", 21, WHITE, "s", a=ah)

def stale_card(a):
    c = Ctx((243, 241, 246), 470, 230)
    c.rect(0, 0, 470, 52, r=20, fill=ODOO_PURPLE); c.rect(0, 28, 470, 24, fill=ODOO_PURPLE)
    c.text(18, 27, "odoo", 28, WHITE, "b", anchor="lm")
    c.text(104, 27, "Inventory", 16, WHITE, "s", anchor="lm")
    c.rect(12, 64, 446, 154, r=16, fill=WHITE, line=(226, 228, 232), lw=2)
    thumb(c, "carton", 62, 112)
    c.text(100, 100, "Carton Box L", 26, INK, "b", anchor="lm"); c.text(100, 128, "Minimum 12", 17, MUT, "r", anchor="lm")
    c.text(396, 100, "15", 46, INK, "b", anchor="mm"); c.text(396, 134, "on hand", 15, MUT, "r", anchor="mm")
    c.rect(32, 160, 406, 40, r=20, fill=(255, 232, 224))
    c.ell(56, 180, 9, fill=ORANGE); c.text(56, 179, "!", 13, WHITE, "b")
    c.text(76, 180, "Not updated · last count 3 days ago", 18, ORANGE, "s", anchor="lm")
    return c.out(), rr_mask(470, 230, 22)

def product_tile(t, feed, fname, zoom, state):
    c = Ctx((236, 239, 238), WIN_W, WIN_H)
    c.rect(0, 0, WIN_W, 64, fill=WHITE)
    c.rect(0, 62, WIN_W, 2, fill=(222, 226, 224))
    bt, bm = brand_tile(40)
    c.im.paste(bt.resize((80, 80), Image.LANCZOS), (24 * 2, 12 * 2), bm.resize((80, 80), Image.LANCZOS))
    c.text(78, 33, "SKIT AI Vision", 28, INK, "b", anchor="lm")
    c.text(272, 34, "Shelf Monitoring", 24, MUT, "r", anchor="lm")
    c_label(c, 1296, 32, "Camera 03 online", 20, (10, 96, 60), (210, 240, 224), dot=EMER, h=36)
    c_label(c, 1524, 32, "Odoo connected", 20, (10, 96, 60), (210, 240, 224), dot=EMER, h=36)
    fw, fh = FEED_W, FEED_H
    c.im.paste(feed, (FEED[0] * 2, FEED[1] * 2), rr_mask(fw, fh, 18).resize((fw * 2, fh * 2), Image.BILINEAR))
    c.d = ImageDraw.Draw(c.im, "RGBA")
    c_label(c, FEED[2] - 18 - 262, FEED[1] + 34, "CAM 03 · AISLE A3", 20, WHITE, (10, 14, 18, 200), dot=(255, 77, 77) if int(t * 2) % 2 == 0 else (130, 40, 40), h=38)
    low = state["low"]
    c_label(c, FEED[2] - 18 - 258, FEED[3] - 34, f"Carton box L  ·  {state['sc']} units", 22, WHITE, ORANGE if low else (10, 14, 18, 200), h=42)
    fp = lambda x, y: feed_pt(fname, x, y, zoom)
    if fname == "C1":
        for (x0, y0, x1, y1, lab, cf), tb in zip(C1_BOXES, range(len(C1_BOXES))):
            (a0, b0), (a1, b1) = fp(x0, y0), fp(x1, y1)
            c_box(c, a0, b0, a1, b1, "Carton L", state["box_a"][tb], WHITE, conf=cf)
        if state["scan"] is not None:
            sx = FEED[0] + 20 + (fw - 40) * ease_io(state["scan"])
            c.rect(sx - 24, FEED[1], 48, fh, fill=(150, 255, 210, 26)); c.rect(sx - 2, FEED[1], 4, fh, fill=(190, 255, 225, 190))
    if fname == "C2":
        (a0, b0), (a1, b1) = fp(40, 250), fp(1090, 570)
        c_box(c, a0, b0, a1, b1, "Zone A3 · Carton box L", state["zone_a"], ORANGE if low else (200, 255, 225), lw=4, corner=50)
        for k in range(4):
            u = seg(t, state["P"][k], 0.8)
            if 0 < u < 1:
                px, py = fp(560 + (k % 2) * 220, 470)
                c.ell(px, py, 16 + 70 * ease_out(u), line=WHITE, lw=3, a=(1 - u) * 0.9)
    px0, py0 = PAN[0], PAN[1]
    odoo_panel(c, t, state)
    for k, p in enumerate(state["P"]):
        u_ = seg(t, p + 0.3, SYNC_DELAY - 0.3)
        if 0 < u_ < 1:
            p0 = (FEED[0] + 560 + (k % 2) * 160, FEED[1] + 380)
            p2 = (px0 + 12 + COL_AV, py0 + 68 + 140 + 24)
            p1 = ((p0[0] + p2[0]) / 2, min(p0[1], p2[1]) - 90)
            for j in range(9):
                uu = clamp(u_ - j * 0.05)
                if uu <= 0: continue
                x_, y_ = bezier(p0, p1, p2, ease_io(uu))
                c.ell(x_, y_, max(3.0, 11 - j * 0.8), fill=WHITE if j == 0 else (160, 255, 215), a=1.0 if j == 0 else 0.6 * (1 - j / 9))
        uu = seg(t, p + SYNC_DELAY, 0.6)
        if 0 < uu < 1:
            c.ell(px0 + 12 + COL_AV, py0 + 68 + 140 + 24, 14 + 56 * ease_out(uu), line=EMER, lw=3, a=(1 - uu) * 0.9)
    c.rect(0, 0, WIN_W, WIN_H, r=26, line=(255, 255, 255, 120), lw=2)
    return c.out()

def scene_core(t, d):
    M0, Y0, A0 = cue("core", 0), cue("core", 1), cue("core", 2)
    P = pick_times(); AL = alert_time()
    s1, s2 = 6.0, 13.6
    feed, bg, zoom, idx = seq2(t, [lambda: feed_and_bg("C1", t, 0.0, 6.6), lambda: feed_and_bg("C2", t, s1 - 0.2, 8.4), lambda: feed_and_bg("C3", t, s2 - 0.2, 7.6)], [s1, s2])
    fr = bg.copy()
    scrim(fr, BAR, 420, 0.4, 0.0)
    fname = ["C1", "C2", "C3"][idx]
    sc, oc = shelf_count(t), odoo_count(t)
    chg = prev = None
    for k in range(4):
        tk = P[k] + SYNC_DELAY
        if t >= tk: chg, prev = tk, START_ON_HAND - k
    nb = len(C1_BOXES)
    state = dict(sc=sc, oc=oc, chg=chg, prev=prev, synced=ease_out(seg(t, Y0 + 0.3, 0.6)), low=sc < MIN_STOCK, scan=((t - M0) * 0.25) % 1.0 if t > M0 else None,
                 box_a=[ease_out(seg(t, M0 + 0.5 + i * 0.35, 0.4)) * (1 - seg(t, s1 - 0.1, 0.4)) for i in range(nb)],
                 zone_a=ease_out(seg(t, s1, 0.5)) * (1 - seg(t, s2 - 0.2, 0.5)), P=P, AL=AL, alert_a=back(seg(t, AL, 0.6)))
    tile = product_tile(t, feed, fname, zoom, state)
    pin = ease_out(seg(t, 0.1, 0.9))
    push_in = 1.0 + 0.025 * ease_io(clamp((t - Y0) / (d - Y0)))
    V.place(fr, tile, rr_mask(WIN_W, WIN_H, 26), 960, 585 + (1 - pin) * 40, s=(0.92 + 0.08 * pin) * push_in, a=pin, shadow=0.75, sh_blur=46, sh_dy=34)
    for sid_t0, t_out, txt in ((M0, Y0 - 0.2, "Continuously monitors shelf quantities"), (Y0, A0 - 0.2, "Synchronizes inventory with Odoo"), (A0, None, "Alerts the team when stock runs low")):
        kinetic(fr, [words(txt)], 960, BAR + 56, 60, t, sid_t0, wt="sl", per=0.1, dur=0.7, t_out=t_out)
    text_center(fr, "Illustrative interface", 17, "r", 1760, H - BAR - 22, (230, 235, 232), 0.55)
    return fr

def scene_benefits(t, d):
    b0, b1, b2 = cue("b", 0), cue("b", 1), cue("b", 2)
    k1, k2 = b1 - 0.3, b2 - 0.3
    fr = seq(t, [lambda: shot("B1", t, 0.0, 3.6, 1.0, 1.06), lambda: shot("B2", t, k1, 4.4, 1.0, 1.06, dx=-0.2), lambda: shot("B3", t, k2, 6.0, 1.0, 1.1, dy=0.2)], [k1, k2])
    scrim(fr, 480, 800, 0.0, 0.7)
    scrim(fr, 800, 941, 0.7, 0.7)
    dust(fr, t, 18, 13, 0.3)
    items = [(b0, "Live shelf counts", "Every product. Every shelf. In real time.", "Aisle A3 · 15 units"),
             (b1, "Always in sync", "On hand is updated in Odoo automatically.", "Odoo · On hand synced"),
             (b2, "Replenish early", "The team is alerted at your minimum.", "Replenishment required")]
    for i, (t0, h_, sub, cp) in enumerate(items):
        t1 = items[i + 1][0] - 0.3 if i < 2 else 1e9
        a = ease_out(seg(t, t0, 0.7)) * (1 - seg(t, t1, 0.4))
        if a <= 0.01: continue
        ln = Mk(8, 190); ln.rr(0, 0, 6, int(190 * ease_out(seg(t, t0, 0.6))), 3)
        paste_col(fr, EMER if i < 2 else ORANGE, ln.get(), 160, 612, a)
        put_text(fr, h_, 100, "l", 190, 600 + (1 - a) * 20, WHITE, a, (1 - a) * 12)
        put_text(fr, sub, 40, "sl", 194, 730 + (1 - a) * 16, (230, 236, 232), a * 0.95)
        chip(fr, 1780, 240, cp, 34, WHITE, (12, 16, 20) if i < 2 else ORANGE, 0.74 if i < 2 else 0.92, dot=EMER if i < 2 else None, a=a, h=68, padx=30, anchor="r")
    return fr

def scene_brand(t, d):
    HIT = cue("r", 0) - 0.1
    base = shot("BR", t, 0.0, 16.0, 1.0, 1.12, exposure=0.5, teal=1.6, warm=0.5, sat=0.7)
    fr = base
    scrim(fr, BAR, H - BAR, 0.5, 0.5)
    dust(fr, t, 30, 17, 0.45)
    build = seg(t, 0.3, HIT - 0.3)
    if t < HIT:
        for k in range(26):
            ang = k / 26 * 2 * math.pi + t * 0.5
            r_ = (1 - build) ** 1.6 * 700 + 20
            m = V._dot(4.5)
            paste_col(fr, WHITE, m, 960 + math.cos(ang) * r_ - m.size[0] / 2, 470 + math.sin(ang) * r_ * 0.55 - m.size[1] / 2, 0.85 * build)
        orb = V._dot(22)
        paste_col(fr, (190, 255, 225), orb.filter(GB(14)), 960 - orb.size[0] / 2, 470 - orb.size[1] / 2, 0.5 * build)
    if t >= HIT:
        flash = 1 - ease_out(seg(t, HIT, 0.7))
        fr = Image.blend(fr, Image.new("RGB", (W, H), (235, 255, 245)), 0.85 * flash)
        tl, mk = brand_tile(360)
        p = back(seg(t, HIT, 0.9))
        V.place(fr, tl, mk, 960, 430, s=p * (1 + 0.01 * math.sin(t * 1.4)), a=clamp(p * 2), shadow=0.6, sh_blur=40, sh_dy=30)
        kinetic(fr, [[("SKIT", None), ("AI", None), ("Vision", None)]], 960, 725, 132, t, cue("r", 0), wt="sl", per=0.18, dur=0.8)
        a = ease_out(seg(t, cue("r", 1), 0.8))
        text_center(fr, "B Y     S R I K E S H     I N F O T E C H", 32, "sl", 960, 820, (190, 255, 225), 0.95 * a, (1 - a) * 8)
        a2 = ease_out(seg(t, cue("r", 2), 0.7))
        if a2 > 0:
            w_ = int(twidth("Every shelf. Always in sync.", 42, "sl")) + 130
            m = Mk(w_, 76); m.rr(0, 0, w_, 76, 38)
            mm = m.get()
            paste_col(fr, (12, 16, 20), mm, 960 - w_ / 2, 886 - 38 + (1 - a2) * 20, 0.6 * a2)
            ring = ImageChops.subtract(mm, mm.filter(ImageFilter.MinFilter(3)))
            paste_col(fr, WHITE, ring, 960 - w_ / 2, 886 - 38 + (1 - a2) * 20, 0.35 * a2)
            put_text(fr, "Every shelf. Always in sync.", 42, "sl", 960 - twidth("Every shelf. Always in sync.", 42, "sl") / 2, 886 - 29 + (1 - a2) * 20, WHITE, a2)
    return fr

SCENES = [scene_hook, scene_idea, scene_core, scene_benefits, scene_brand]

def load_tl(): return json.loads((HERE / "timeline.json").read_text("utf8"))

def frame_at(T, tl, idx=0):
    starts, durs = tl["starts"], tl["durs"]
    n = len(SCENES); hh = 0.6 / 2
    img = None
    for i in range(n - 1):
        tb = starts[i + 1]
        if tb - hh <= T < tb + hh:
            A = SCENES[i](T - starts[i], durs[i]); B = SCENES[i + 1](max(0.0, T - starts[i + 1]), durs[i + 1])
            p = ease_io((T - (tb - hh)) / 0.6)
            img = Image.blend(A, B, p)
            break
    if img is None:
        i = max(k for k in range(n) if starts[k] <= T + 1e-9)
        img = SCENES[i](T - starts[i], durs[i])
    img = add_grain(img, idx)
    img = letterbox(img)
    T_end = tl["total"]
    if T < 0.8: img = Image.blend(Image.new("RGB", (W, H), (0, 0, 0)), img, ease_out(T / 0.8))
    if T > T_end - 1.2: img = Image.blend(img, Image.new("RGB", (W, H), (0, 0, 0)), seg(T, T_end - 1.2, 1.2))
    return img

_TL = None
def render_frame(n):
    global _TL
    if _TL is None: _TL = load_tl()
    return np.asarray(frame_at(n / FPS, _TL, n).convert("RGB")).tobytes()

# ---------------------------------------------------------------- audio / timeline (same structure as before)
def prep():
    items = json.loads((HERE / "narration.json").read_text("utf8"))
    sr = music.SR
    starts, durs, plan, cues, sid_start = [], [], [], {}, {}
    T = 0.0
    for it in items:
        lens, pads = [], []
        for k, p in enumerate(it["parts"]):
            w = wave.open(str(HERE / f"{it['id']}_{k}.wav")); lens.append(w.getnframes() / w.getframerate()); pads.append(p.get("pad", 0.0))
        offs, o = [], it.get("lead", 0.45)
        for L_, pad in zip(lens, pads): offs.append(o); o += L_ + pad + GAP
        d = o - GAP + TAIL
        cues[it["id"]] = [round(x, 3) for x in offs]
        cues[it["id"] + "_end"] = [round(x + L_, 3) for x, L_ in zip(offs, lens)]
        starts.append(round(T, 3)); durs.append(round(d, 3)); sid_start[it["id"]] = T
        for k, (off, L_) in enumerate(zip(offs, lens)): plan.append((T + off, L_, it["id"], k, it["parts"][k]["show"]))
        T += d
    total = T
    (HERE / "cues.json").write_text(json.dumps(cues), "utf8")
    global _CUES
    _CUES = cues
    N = int((total + 3.0) * sr)
    voice = np.zeros(N, np.float32)
    for at, L_, sid, k, show in plan:
        w = wave.open(str(HERE / f"{sid}_{k}.wav"))
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        s0 = int(at * sr); voice[s0:s0 + len(raw)] = raw
    rng = np.random.default_rng(3)
    nir = int(0.8 * sr); tir = np.arange(nir) / sr
    ir = rng.standard_normal(nir) * np.exp(-tir * 6.0); ir[:int(0.004 * sr)] = 0; ir /= np.sqrt((ir ** 2).sum())
    wet = sps.fftconvolve(voice, ir, mode="full")[:N]
    voice_m = (voice * 0.94 + wet * 0.14).astype(np.float32)
    sc = sid_start
    Tm = {"idea": sc["i"] + cue("i", 0) - 0.1, "core": sc["core"] + 0.1, "alert": sc["core"] + alert_time(), "benefits": sc["b"] + 0.1,
          "brand_riser": sc["r"] + 0.0, "brand_hit": sc["r"] + cue("r", 0) - 0.1}
    extras = []
    for k, p in enumerate(pick_times()):
        extras.append((sc["core"] + p + 0.05, "pop", 0.10)); extras.append((sc["core"] + p + SYNC_DELAY, "tick", 0.12))
    for k in range(len(starts) - 1):
        extras.append((starts[k + 1] - 0.45, "whoosh", 0.10))
    mus = music.make_music(total, Tm, extras)
    if len(mus) < N: mus = np.pad(mus, ((0, N - len(mus)), (0, 0)))
    mus = mus[:N]
    env = np.abs(voice)
    sos = sps.butter(2, 5.0, "low", fs=sr, output="sos")
    env = np.clip(sps.sosfiltfilt(sos, env) / 0.07, 0, 1)
    duck = 1.0 - 0.55 * env
    mix_ = mus * (0.78 * duck)[:, None] + np.stack([voice_m, voice_m], 1) * 1.0
    peak = np.abs(mix_).max()
    mix_ = mix_ / max(1.0, peak / 0.93)
    out = wave.open(str(HERE / "audio3.wav"), "wb")
    out.setnchannels(2); out.setsampwidth(2); out.setframerate(sr)
    out.writeframes((mix_[: int((total + 0.2) * sr)] * 32767).astype(np.int16).tobytes()); out.close()
    (HERE / "timeline.json").write_text(json.dumps({"starts": starts, "durs": durs, "total": round(total, 3)}), "utf8")
    print("scenes", [round(x, 1) for x in durs], "total", round(total, 1))

def render(outfile):
    tl = load_tl(); frames = int(math.ceil(tl["total"] * FPS))
    cmd = [str(HERE / "ffmpeg.exe"), "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", str(HERE / "audio3.wav"), "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart", outfile]
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
            frame_at(float(s), tl, int(float(s) * FPS)).save(HERE / f"ad3_{float(s):05.1f}.png")
        print("saved")
    elif cmd == "render": render(sys.argv[2])
