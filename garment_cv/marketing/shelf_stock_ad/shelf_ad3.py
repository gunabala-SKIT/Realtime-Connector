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
    text_block(fr, [words("Shelves empty faster"), words("than stock records can keep up.")], 960, 600, 80, t, A + 0.1, t_out=B - 0.15)
    if t > t1 - 0.3:
        for i, (lab, ts) in enumerate([("Manual counts", B + 0.2), ("Odoo updated late", B + 2.3), ("Noticed when it's gone", B + 4.9)]):
            a = ease_out(seg(t, ts, 0.5))
            chip(fr, 140 - (1 - a) * 40, 690 + i * 70, lab, 26, WHITE, (10, 14, 18), 0.7, dot=ORANGE, a=a, h=54, padx=26)
        a = ease_out(seg(t, B + 1.4, 0.6))
        if a > 0:
            c = Ctx(WHITE, 420, 170)
            E.odoo_chip(c, 92, 40, 0.6)
            c.text(30, 92, "Carton box L", 26, INK, "s", anchor="lm"); c.text(380, 92, "15", 44, INK, "b", anchor="rm")
            c.pill(210, 138, "Last count: 3 days ago", ORANGE, (255, 232, 224), size=18, padx=14, h=32)
            V.place(fr, c.out(), rr_mask(420, 170, 22), 1590 + (1 - a) * 80, 330, s=1.0, yaw=-8, pitch=2, a=a, shadow=0.55, sh_blur=28, sh_dy=22)
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
    chip(fr, L + 14, T_ + 56, "REC", 20, WHITE, (12, 16, 20), 0.7, dot=ORANGE if int(t * 2) % 2 == 0 else (120, 50, 30), a=on, h=36, padx=14)
    chip(fr, L + 120, T_ + 56, "SKIT AI VISION · SHELF A3", 19, (210, 225, 220), (12, 16, 20), 0.7, a=on, h=36, padx=14)
    text_block(fr, [words("The camera doesn't"), words("just detect low stock.")], 960, 540, 88, t, A, per=0.2)
    a = ease_out(seg(t, A + 1.6, 0.6))
    if a > 0:
        wlow = twidth("just detect low stock.", 88, "l")
        ul = Mk(int(wlow * a) + 4, 10); ul.rr(0, 2, int(wlow * a), 6, 3)
        paste_col(fr, EMER, ul.get(), 960 - wlow / 2, 540 + 88 * 0.62 + 40, 0.95)
    return fr

C1_BOXES = [(556, 156, 940, 224, "Carton L", 0.94), (510, 216, 1000, 450, "Carton L", 0.97), (1006, 244, 1206, 444, "Carton L", 0.95),
            (556, 606, 766, 820, "Carton L", 0.93), (766, 500, 1280, 820, "Carton L", 0.98)]

def scene_core(t, d):
    M0, Y0, A0 = cue("core", 0), cue("core", 1), cue("core", 2)
    P = pick_times(); AL = alert_time()
    s1, s2 = 6.0, 13.6
    fr = seq(t, [lambda: shot("C1", t, 0.0, 6.6, 1.0, 1.05), lambda: shot("C2", t, s1 - 0.2, 8.4, 1.0, 1.06), lambda: shot("C3", t, s2 - 0.2, 7.6, 1.0, 1.06, dx=0.2)], [s1, s2])
    scrim(fr, BAR, 420, 0.5, 0.0)
    scrim(fr, H - BAR - 230, H - BAR, 0.0, 0.62)
    dust(fr, t, 22, 9, 0.3)
    sc, oc = shelf_count(t), odoo_count(t)
    low = sc < MIN_STOCK
    # --- persistent HUD
    on = ease_out(seg(t, 0.3, 0.7))
    chip(fr, 170, BAR + 56, "AI · AISLE A3 · LIVE", 20, WHITE, (12, 16, 20), 0.7, dot=EMER, a=on, h=38, padx=16)
    chip(fr, 170, BAR + 108, f"Carton box L  ·  {sc} units", 22, WHITE, ORANGE if low else (12, 16, 20), 0.85 if low else 0.7, a=ease_out(seg(t, M0 + 0.5, 0.5)), h=42, padx=18)
    # --- monitoring boxes on the static shelf
    if t < s1 + 0.4:
        for i, (x0, y0, x1, y1, lab, cf) in enumerate(C1_BOXES):
            a = ease_out(seg(t, M0 + 0.5 + i * 0.35, 0.4)) * (1 - seg(t, s1 - 0.1, 0.4))
            hud_box(fr, x0, y0, x1, y1, lab, a, WHITE, conf=cf)
        sc_ = ((t - M0) * 0.25) % 1.0 if t > M0 else None
        if sc_ is not None:
            sx = lerp(480, 1320, ease_io(sc_))
            sm = Mk(50, H); sm.rr(22, 200, 6, 640, 3)
            paste_col(fr, (150, 255, 210), sm.get().filter(GB(6)), sx - 25, 0, 0.45 * (1 - seg(t, s1 - 0.1, 0.4))); paste_col(fr, (220, 255, 240), sm.get(), sx - 25, 0, 0.7 * (1 - seg(t, s1 - 0.1, 0.4)))
    # --- zone on the top-down shot
    if s1 - 0.3 < t < s2 + 0.4:
        za = ease_out(seg(t, s1, 0.5)) * (1 - seg(t, s2 - 0.2, 0.5))
        zc = ORANGE if low else (200, 255, 225)
        hud_box(fr, 40, 290, 1090, 560, "Zone A3 · Carton box L", za, zc, lw=3, corner=48)
        for k in range(4):
            u = seg(t, P[k] + 0.0, 0.8)
            if 0 < u < 1:
                R = 20 + 90 * ease_out(u)
                m = Mk(int(R * 2 + 20), int(R * 2 + 20)); m.ell(R + 10, R + 10, R, fill=0, outline=255, lw=3)
                paste_col(fr, WHITE, m.get(), 560 + (k % 2) * 220 - R - 10, 470 - R - 10, (1 - u) * 0.9)
    # --- Odoo card
    pin_o = ease_out(seg(t, Y0 - 0.6, 0.9))
    sO, yO, pO = 0.74, -9.0, 1.5
    cxO, cyO = 1560, 425
    chg = prev = None
    for k in range(4):
        tk = P[k] + SYNC_DELAY
        if t >= tk: chg, prev = tk, START_ON_HAND - k
    synced = ease_out(seg(t, Y0 + 0.3, 0.6))
    if pin_o > 0:
        V.place(fr, odoo_card(t, oc, chg, prev, synced), rr_mask(600, 700, 28), cxO + (1 - pin_o) * 160, cyO, s=sO * (0.96 + 0.04 * pin_o), yaw=yO, pitch=pO, a=pin_o, shadow=0.6, sh_blur=36, sh_dy=30,
                glow=((ORANGE, 0.5 + 0.2 * math.sin(t * 5), 14) if oc < MIN_STOCK else None))
        for k in range(4):
            u_ = seg(t, P[k] + 0.3, SYNC_DELAY - 0.3)
            if 0 < u_ < 1:
                p0 = (700 + (k % 2) * 220, 440)
                p2 = tile_pt(500, 240, 600, 700, cxO, cyO, sO, yO, pO)
                p1 = ((p0[0] + p2[0]) / 2, min(p0[1], p2[1]) - 130)
                for j in range(9):
                    uu = clamp(u_ - j * 0.05)
                    if uu <= 0: continue
                    x_, y_ = bezier(p0, p1, p2, ease_io(uu))
                    m = V._dot(max(3.0, 9 - j * 0.6))
                    paste_col(fr, (160, 255, 215) if j else WHITE, m.filter(GB(2 if j == 0 else 1)), x_ - m.size[0] / 2, y_ - m.size[1] / 2, (1.0 if j == 0 else 0.6 * (1 - j / 9)))
            ripple(fr, *tile_pt(500, 240, 600, 700, cxO, cyO, sO, yO, pO), seg(t, P[k] + SYNC_DELAY, 0.6), (160, 255, 215))
    # --- captions
    text_block(fr, [[("Continuously monitors shelf quantities", None)]], 960, BAR + 130, 50, t, M0, t_out=Y0 - 0.2, per=0.1) if False else None
    for sid_t0, t_out, txt in ((M0, Y0 - 0.2, "Continuously monitors shelf quantities"), (Y0, A0 - 0.2, "Synchronizes inventory with Odoo"), (A0, None, "Alerts the team when stock runs low")):
        kinetic(fr, [words(txt)], 960, H - BAR - 72, 52, t, sid_t0, wt="sl", per=0.1, dur=0.7, t_out=t_out)
    # --- alert
    a_ = back(seg(t, AL, 0.6))
    if a_ > 0:
        tl, mk = toast(t, AL)
        V.place(fr, tl, mk, 1480 + (1 - clamp(a_)) * 120, 775, s=0.88, yaw=-6, pitch=1.5, a=clamp(a_ * 1.6), shadow=0.6, sh_blur=30, sh_dy=24, glow=(ORANGE, 0.35 + 0.2 * math.sin(t * 5), 14))
    text_center(fr, "Illustrative interface", 17, "r", 1740, BAR + 24, (230, 235, 232), 0.55)
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
        ln = Mk(8, 120); ln.rr(0, 0, 6, int(120 * ease_out(seg(t, t0, 0.6))), 3)
        paste_col(fr, EMER if i < 2 else ORANGE, ln.get(), 160, 668, a)
        put_text(fr, h_, 74, "l", 190, 650 + (1 - a) * 20, WHITE, a, (1 - a) * 12)
        put_text(fr, sub, 30, "sl", 194, 748 + (1 - a) * 16, (222, 230, 226), a * 0.95)
        chip(fr, 1760, 230, cp, 24, WHITE if i < 2 else WHITE, (12, 16, 20) if i < 2 else ORANGE, 0.72 if i < 2 else 0.9, dot=EMER if i < 2 else None, a=a, h=48, padx=22, anchor="r")
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
        streak = Mk(W, 60); wdt = 1700 * ease_out(seg(t, HIT, 0.9))
        streak.rr(960 - wdt / 2, 26, wdt, 8, 4)
        sm = streak.get()
        paste_col(fr, (160, 255, 215), sm.filter(GB(14)), 0, 470 - 30, 0.9 * (1 - seg(t, HIT + 0.4, 1.4)))
        paste_col(fr, WHITE, sm, 0, 470 - 30, 0.9 * (1 - seg(t, HIT + 0.2, 1.2)))
        for k in range(3):
            u = ((t - HIT) * 0.5 + k / 3) % 1.0
            R = 170 + u * 700
            m = Mk(int(R * 2 + 30), int(R * 2 + 30)); m.ell(R + 15, R + 15, R, fill=0, outline=255, lw=3)
            paste_col(fr, (160, 255, 215), m.get(), 960 - R - 15, 470 - R - 15, (1 - u) * 0.45 * ease_out(seg(t, HIT, 0.5)))
        tl, mk = brand_tile(300)
        p = back(seg(t, HIT, 0.9))
        V.place(fr, tl, mk, 960, 440, s=p * (1 + 0.01 * math.sin(t * 1.4)), a=clamp(p * 2), glow=(WHITE, 0.45, 30), shadow=0.6, sh_blur=40, sh_dy=30)
        burst(fr, 960, 440, seg(t, HIT, 1.6), (190, 255, 225), n=30, R=460)
        kinetic(fr, [[("SKIT", None), ("AI", None), ("Vision", None)]], 960, 720, 104, t, cue("r", 0), wt="sl", per=0.18, dur=0.8)
        a = ease_out(seg(t, cue("r", 1), 0.8))
        text_center(fr, "B Y     S R I K E S H     I N F O T E C H", 26, "sl", 960, 810, (190, 255, 225), 0.95 * a, (1 - a) * 8)
        a2 = ease_out(seg(t, cue("r", 2), 0.7))
        if a2 > 0:
            w_ = int(twidth("Every shelf. Always in sync.", 34, "sl")) + 120
            m = Mk(w_, 64); m.rr(0, 0, w_, 64, 32)
            mm = m.get()
            paste_col(fr, (12, 16, 20), mm, 960 - w_ / 2, 872 - 32 + (1 - a2) * 20, 0.6 * a2)
            ring = ImageChops.subtract(mm, mm.filter(ImageFilter.MinFilter(3)))
            paste_col(fr, WHITE, ring, 960 - w_ / 2, 872 - 32 + (1 - a2) * 20, 0.35 * a2)
            put_text(fr, "Every shelf. Always in sync.", 34, "sl", 960 - twidth("Every shelf. Always in sync.", 34, "sl") / 2, 872 - 24 + (1 - a2) * 20, WHITE, a2)
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
