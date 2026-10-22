"""SKIT AI Vision - Shelf Stock Monitoring + Odoo Warehouse (v2: light editorial theme).

Look: warm paper, forest green, lime and signal-orange; flat shapes with ink outlines and hard offset shadows,
marker-highlighted headlines, colour-block wipes. Font: Bahnschrift (DIN-style). Different from the dark
violet / navy videos on purpose.

    python shelf_ad2.py prep | still 5 12 ... | render out.mp4
"""
import json, math, subprocess, sys, wave
from functools import lru_cache
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
from scipy import signal as sps

import explainer3 as X
from explainer3 import (E, V, Ctx, Mk, W, H, FPS, GB, brand_tile, burst, ripple, clamp, ease_io, ease_out, back, lerp, mix, paste_col,
                        particles, put_text, rr_mask, scale_mask, seg, text_center, twidth)
import music2 as music

HERE = Path(__file__).resolve().parent

# ---- font: Bahnschrift (variable DIN-style) instead of Segoe UI
_BF = {}
WEIGHT = {"l": 300, "sl": 350, "r": 400, "s": 600, "b": 700}
def bf(w, size):
    k = (w, int(size))
    if k not in _BF:
        f = ImageFont.truetype("C:/Windows/Fonts/bahnschrift.ttf", int(size))
        f.set_variation_by_axes([WEIGHT[w], 100]); _BF[k] = f
    return _BF[k]
E.font = bf; V.font = bf

# ---- palette
PAPER, WHITE, INKG = (246, 243, 236), (255, 255, 255), (16, 23, 20)
FOREST, FOREST2 = (10, 52, 40), (6, 34, 27)
EMER, LIME, ALERT, PEACH = (25, 178, 107), (212, 242, 92), (255, 91, 58), (255, 214, 204)
MUT, LINEC = (104, 116, 110), (222, 217, 205)
WATERC, JUICEC, SNACKC = (80, 170, 255), (255, 140, 50), (255, 200, 50)
GAP, TAIL = 0.22, 0.55

# ---- backgrounds (numpy gradients + grain, then flat decorations)
_gx, _gy = V._gx, V._gy
def _dots():
    m = Image.new("L", (W, H), 0); d = ImageDraw.Draw(m)
    for y in range(28, H, 56):
        for x in range(28, W, 56): d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=34)
    return m
DOTS = _dots()

def bg_paper(t):
    base = np.array(PAPER, np.float32)
    g1 = np.exp(-(((_gx - (W * 0.10 + 90 * math.sin(t * 0.4))) / 720) ** 2 + ((_gy - H * 0.98) / 400) ** 2))
    g2 = np.exp(-(((_gx - W * 0.93) / 620) ** 2 + ((_gy - H * 0.03) / 340) ** 2))
    img = base + (np.array((218, 236, 214), np.float32) - base) * g1[..., None] * 0.9 + (np.array((238, 248, 200), np.float32) - base) * g2[..., None] * 0.9
    im = V._finish(img)
    im.paste(INKG, (0, 0), DOTS.point(lambda v: int(v * 0.6)))
    return im

def bg_forest(t, glow=1.0):
    k = np.clip(_gy / H, 0, 1)[..., None]
    base = np.array(FOREST2, np.float32) + (np.array(FOREST, np.float32) - np.array(FOREST2, np.float32)) * k
    cx = W * 0.5 + 160 * math.sin(t * 0.5)
    g = np.exp(-(((_gx - cx) / 780) ** 2 + ((_gy - H * 1.05) / 420) ** 2)) * glow
    img = base + (np.array((70, 150, 70), np.float32) - base) * g[..., None] * 0.55
    im = V._finish(img)
    im.paste(LIME, (0, 0), DOTS.point(lambda v: int(v * 0.35)))
    return im

def bg_lime(t):
    g = np.exp(-(((_gx - W * 0.5) / 1100) ** 2 + ((_gy - H * 0.45) / 700) ** 2))
    base = np.array(LIME, np.float32)
    img = base + (np.array((238, 252, 160), np.float32) - base) * g[..., None]
    im = V._finish(img)
    im.paste(INKG, (0, 0), DOTS.point(lambda v: int(v * 0.5)))
    return im

def dust(fr, t, n, seed, col, a=0.5):
    rng = np.random.default_rng(seed)
    for i in range(n):
        x0, y0 = rng.uniform(0, W), rng.uniform(0, H)
        sp, ph = rng.uniform(8, 26), rng.uniform(0, 6.28)
        x, y = (x0 + math.sin(t * 0.3 + ph) * 30) % W, (y0 - t * sp) % H
        m = V._dot(rng.uniform(1.6, 3.4))
        paste_col(fr, col, m, x - m.size[0] / 2, y - m.size[1] / 2, a * (0.5 + 0.5 * math.sin(t * 1.4 + ph)))

# ---- sticker placement (ink outline lives in the tile, hard offset shadow added here)
def sticker(fr, tile, mask, cx, cy, s=1.0, a=1.0, dx=10, dy=10, sh=INKG, blur=0.0):
    if a <= 0.01 or s <= 0.01: return
    w, h = tile.size
    if abs(s - 1) > 0.002:
        sz = (max(2, int(w * s)), max(2, int(h * s)))
        tile = tile.resize(sz, Image.BILINEAR); mask = mask.resize(sz, Image.BILINEAR)
    if blur > 0.4:
        tile = tile.filter(GB(blur)); mask = mask.filter(GB(blur))
    x, y = int(cx - tile.size[0] / 2), int(cy - tile.size[1] / 2)
    m = scale_mask(mask, a)
    if dx or dy: fr.paste(sh, (x + int(dx * s), y + int(dy * s)), m)
    fr.paste(tile, (x, y), m)

def card(w, h, r=28, fill=WHITE, lw=4):
    c = Ctx(fill, w, h)
    c.rect(0, 0, w, h, r=r, line=INKG, lw=lw)
    return c

def sticker_pill(label, size, fg, fill, dot=None, h=None, padx=36, lw=3, icon=None):
    h = h or int(size * 2.1)
    w = int(twidth(label, size, "s") + padx * 2 + (40 if (dot or icon) else 0))
    c = Ctx(fill, w, h)
    c.rect(0, 0, w, h, r=h // 2, line=INKG, lw=lw)
    x = padx
    if dot: c.ell(padx + 6, h / 2, 9, fill=dot, line=INKG, lw=2); x = padx + 32
    if icon == "spark":
        pts = [(padx + 14 + 14 * math.cos(i / 64 * 2 * math.pi) ** 3, h / 2 + 14 * math.sin(i / 64 * 2 * math.pi) ** 3) for i in range(64)]
        c.poly(pts, fill=fg); x = padx + 40
    c.text(x, h / 2 - 1, label, size, fg, "s", anchor="lm")
    return c.out(), rr_mask(w, h, h // 2)

# ---- headline with marker highlights
def headline(fr, lines, cx, cy, size, t, t0, dark=False, per=0.14, t_out=None, lh=1.16, wt="b"):
    n, idx = len(lines), 0
    base = WHITE if dark else INKG
    for li, line in enumerate(lines):
        widths = [twidth(w, size, wt) for w, _ in line]
        gap = size * 0.26
        total = sum(widths) + gap * (len(line) - 1)
        x = cx - total / 2
        ytop = cy + (li - (n - 1) / 2) * size * lh - size * 0.62
        for (w, hl), ww in zip(line, widths):
            p = ease_out(seg(t, t0 + idx * per, 0.55))
            q = seg(t, t_out, 0.4) if t_out is not None else 0.0
            a = p * (1 - q)
            if a > 0.01:
                if hl is not None:
                    hp = ease_out(seg(t, t0 + idx * per + 0.2, 0.5))
                    bw = int((ww + 22) * hp)
                    if bw > 2:
                        m = Mk(bw + 4, int(size * 0.78)); m.rr(0, 0, bw, int(size * 0.76), 8)
                        paste_col(fr, hl, m.get(), x - 11, ytop + size * 0.22, a)
                col = INKG if hl is not None else base
                put_text(fr, w, int(size), wt, x, ytop + (1 - p) * 26 - q * 14, col, a, (1 - p) * 14 + q * 10)
            x += ww + gap; idx += 1

def words_(s, hl=None): return [(w, hl) for w in s.split()]

# ---- cues
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
PICK_IDX = [9, 4, 12, 2]
def alert_time(): return pick_times()[-1] + SYNC_DELAY
def shelf_count(t): return START_ON_HAND - sum(1 for p in pick_times() if t >= p + SHELF_DELAY)
def odoo_count(t): return START_ON_HAND - sum(1 for p in pick_times() if t >= p + SYNC_DELAY)

# ---- shelf drawing
SW, SH = 1000, 640
ROWS = [("snack", 190, 7, 120, 118), ("juice", 380, 6, 140, 138), ("water", 570, 15, 82, 57)]
LABELS = {"snack": ("Snack box", SNACKC), "juice": ("Orange juice 1 L", JUICEC), "water": ("Water 500 ml", WATERC)}

def draw_product(c, kind, cx, yb, a=1.0, lift=0.0, s=1.0):
    c.xf(cx, yb, s, a)
    c.ell(cx, yb + 2, 28, 5, fill=(0, 0, 0, 55))
    y = yb - lift
    if kind == "water":
        c.rect(cx - 9, y - 122, 18, 16, r=4, fill=WHITE, line=INKG, lw=3)
        c.rect(cx - 19, y - 100, 38, 96, r=12, fill=WATERC, line=INKG, lw=3)
        c.rect(cx - 19, y - 70, 38, 30, fill=WHITE)
        c.rect(cx - 13, y - 92, 5, 18, r=2, fill=(255, 255, 255, 140))
    elif kind == "juice":
        c.poly([(cx - 34, y - 132), (cx - 22, y - 150), (cx + 22, y - 150), (cx + 34, y - 132)], fill=(255, 190, 120), line=INKG, lw=3)
        c.rect(cx - 34, y - 132, 68, 128, r=6, fill=JUICEC, line=INKG, lw=3)
        c.ell(cx, y - 72, 21, fill=WHITE, line=INKG, lw=3)
        c.ell(cx, y - 72, 9, fill=(255, 120, 20))
    else:
        c.rect(cx - 40, y - 112, 80, 108, r=8, fill=SNACKC, line=INKG, lw=3)
        c.rect(cx - 37, y - 82, 74, 24, fill=WHITE)
        c.ell(cx, y - 98, 8, fill=INKG)
    c.reset()

def shelf_tile(t, removed, boxes=0.0, counts=None, low=False, hud=True, scan=None):
    c = Ctx((232, 240, 228), SW, SH)
    for k in range(32): c.rect(0, k * 20, SW, 20, fill=mix((238, 244, 232), (220, 232, 214), k / 31))
    for x in range(40, SW, 90): c.rect(x, 0, 2, SH, fill=(0, 0, 0, 10))
    c.rect(6, 0, 14, SH, fill=INKG); c.rect(SW - 20, 0, 14, SH, fill=INKG)
    for name, yb, n, x0, step in ROWS:
        c.rect(20, yb, SW - 40, 14, fill=INKG)
        c.rect(20, yb, SW - 40, 3, fill=(255, 255, 255, 90))
        c.rect(20, yb + 14, SW - 40, 8, fill=(0, 0, 0, 30))
        for i in range(n):
            tr = removed.get((name, i))
            if tr is not None:
                p = seg(t, tr, 0.9)
                if p >= 1: continue
                draw_product(c, name, x0 + i * step, yb, a=1 - ease_io(p) ** 1.6, lift=ease_out(p) * 170, s=1 - 0.1 * p)
            else:
                draw_product(c, name, x0 + i * step, yb)
    if scan is not None:
        sx = 30 + (SW - 60) * ease_io(scan)
        c.rect(sx - 22, 0, 44, SH, fill=(25, 178, 107, 30)); c.rect(sx - 3, 0, 6, SH, fill=(25, 178, 107, 200))
    def box(x0, y0, x1, y1, col, label, a, txt):
        if a <= 0.01: return
        c.rect(x0, y0, x1 - x0, y1 - y0, r=10, line=col, lw=5, a=a)
        w_ = c.tw(label, 22, "s") + 26
        c.rect(x0, y0 - 36, w_, 36, r=8, fill=col, line=INKG, lw=2, a=a)
        c.text(x0 + 13, y0 - 18, label, 22, txt, "s", anchor="lm", a=a)
    if boxes > 0:
        for name, yb, n, x0, step in ROWS:
            lab, _ = LABELS[name]
            cnt = (counts or {}).get(name, n)
            is_low = name == "water" and low
            top = yb - (150 if name != "snack" else 132)
            box(x0 - 44, top - 8, x0 + (n - 1) * step + 44, yb + 2, ALERT if is_low else EMER, f"{lab} × {cnt}" + ("  ·  LOW" if is_low else ""), boxes,
                WHITE)
    if hud:
        c.rect(26, 14, 310, 40, r=20, fill=INKG)
        c.ell(54, 34, 8, fill=(255, 77, 77) if int(t * 2) % 2 == 0 else (130, 40, 40))
        c.text(72, 34, "CAM 03 · SHELF A3 · LIVE", 19, WHITE, "s", anchor="lm")
    # ink border for the sticker look
    c.rect(0, 0, SW, SH, r=30, line=INKG, lw=5)
    return c.out()

PRODUCTS = [("Snack box", SNACKC, 7, 4), ("Orange juice 1 L", JUICEC, 6, 3), ("Water 500 ml", WATERC, None, MIN_STOCK)]

def odoo_tile(t, water, change_t=None, prev=None, synced=0.0, stale=False):
    c = Ctx(WHITE, 600, 700)
    E.odoo_chip(c, 112, 52, 0.78)
    c.text(40, 130, "Inventory · On Hand", 36, INKG, "b", anchor="lm")
    c.text(40, 170, "WH / Stock / Shelf A3", 22, MUT, "r", anchor="lm")
    for i, (name, col, oh, mn) in enumerate(PRODUCTS):
        y = 200 + i * 150
        is_w = oh is None
        oh = water if is_w else oh
        below, near = oh < mn, oh == mn
        c.rect(24, y, 552, 134, r=18, fill=PEACH if below else (247, 245, 238), line=ALERT if below else INKG, lw=4 if below else 3)
        c.rect(44, y + 22, 54, 54, r=14, fill=col, line=INKG, lw=3)
        c.text(116, y + 34, name, 28, INKG, "s", anchor="lm")
        c.text(116, y + 66, f"Minimum  {mn}", 21, MUT, "r", anchor="lm")
        if is_w and change_t is not None and prev is not None:
            u = seg(t, change_t, 0.45)
            c.text(500, y + 40 + (1 - ease_out(u)) * -26, str(oh), 56, INKG, "b", anchor="mm", a=ease_out(u))
            c.text(500, y + 40 + ease_out(u) * 26, str(prev), 56, INKG, "b", anchor="mm", a=1 - ease_out(u))
            if u < 1: c.pill(432, y + 100, "−1", WHITE, ALERT, size=20, padx=12, h=30, a=1 - u * 0.2)
        else:
            c.text(500, y + 40, str(oh), 56, INKG, "b", anchor="mm")
        c.text(500, y + 80, "on hand", 17, MUT, "r", anchor="mm")
        c.rect(44, y + 106, 360, 12, r=6, fill=(228, 224, 212), line=INKG, lw=2)
        bc = ALERT if below else ((255, 190, 40) if near else EMER)
        c.rect(44, y + 106, max(10, 360 * clamp(oh / 24.0)), 12, r=6, fill=bc, line=INKG, lw=2)
        c.rect(44 + 360 * mn / 24.0 - 2, y + 98, 5, 28, r=2, fill=INKG)
        if below: c.pill(476, y + 112, "Below minimum", WHITE, ALERT, size=17, padx=12, h=30)
        elif near: c.pill(500, y + 112, "At minimum", INKG, (255, 210, 90), size=17, padx=12, h=30)
        else: c.pill(504, y + 112, "In stock", (10, 90, 55), (205, 240, 218), size=17, padx=12, h=30)
    if stale:
        c.pill(300, 664, "Last count: 3 days ago", ALERT, PEACH, size=19, padx=18, h=34)
    elif synced > 0:
        c.ell(52, 664, 7 + 2 * math.sin(t * 6), fill=EMER, a=synced)
        c.text(70, 664, "Synced from shelf camera · live", 20, (10, 100, 62), "s", anchor="lm", a=synced)
    c.rect(0, 0, 600, 700, r=30, line=INKG, lw=5)
    return c.out()

def tile_pt(px, py, w, h, cx, cy, s):
    return (cx + (px - w / 2) * s, cy + (py - h / 2) * s)

def bezier(p0, p1, p2, u):
    return ((1 - u) ** 2 * p0[0] + 2 * (1 - u) * u * p1[0] + u * u * p2[0], (1 - u) ** 2 * p0[1] + 2 * (1 - u) * u * p1[1] + u * u * p2[1])

def bell_icon(c, cx, cy, s=1.0, col=WHITE, swing=0.0):
    ang = swing
    def R(x, y): return (cx + (x * math.cos(ang) - (y + 26) * math.sin(ang)) * s, cy - 26 * s + (x * math.sin(ang) + (y + 26) * math.cos(ang)) * s)
    c.poly([R(-26, 22), R(-22, -8), R(-12, -26), R(12, -26), R(22, -8), R(26, 22)], fill=col, line=INKG, lw=3)
    c.rect(cx - 30 * s, cy + 18 * s, 60 * s, 9 * s, r=4 * s, fill=col, line=INKG, lw=3)
    c.ell(cx, cy + 35 * s, 7 * s, fill=col, line=INKG, lw=2)

# ---- scenes
def scene_hook(t, d):
    fr = bg_paper(t)
    A, B = cue("p1", 0), cue("p1", 1)
    rng = np.random.default_rng(4)
    removed = {}
    allp = [(r[0], i) for r in ROWS for i in range(r[2])]
    order = list(rng.permutation(len(allp)))
    for n_, idx in enumerate(order[:20]):
        removed[allp[idx]] = A + 0.5 + n_ * (cue_end("p1", 0) - A - 0.5) / 20.0
    pin = ease_out(seg(t, 0.3, 0.9))
    z = lerp(0.84, 0.88, ease_io(t / d))
    st, mk = shelf_tile(t, removed, 0.0, hud=False), rr_mask(SW, SH, 30)
    sticker(fr, st, mk, 560 + (1 - pin) * -160, 560, s=z * pin, a=pin, dx=12, dy=12, sh=FOREST)
    ot, om = odoo_tile(t, START_ON_HAND, stale=True), rr_mask(600, 700, 30)
    sticker(fr, ot, om, 1590 + (1 - pin) * 160, 560, s=z * pin, a=pin, dx=12, dy=12, sh=ALERT)
    headline(fr, [words_("Shelves empty fast.")], 960, 120, 70, t, A + 0.1, t_out=A + 2.35)
    headline(fr, [[("Records", None), ("can't", PEACH), ("keep", PEACH), ("up.", PEACH)]], 960, 120, 70, t, A + 2.6, t_out=B - 0.2)
    headline(fr, [[("Always", None), ("one", None), ("step", None), ("behind.", PEACH)]], 960, 120, 70, t, B + 0.1)
    for i, (lab, ts) in enumerate([("Manual counts", B + 0.2), ("Odoo updated late", B + 2.3), ("Noticed when it's gone", B + 4.9)]):
        a = ease_out(seg(t, ts, 0.55))
        tl, mk2 = sticker_pill(lab, 38, INKG, WHITE, dot=ALERT, h=92, padx=44)
        sticker(fr, tl, mk2, 470 + i * 490, 965 + (1 - a) * 26, s=0.85 + 0.15 * a, a=a, dx=7, dy=7, sh=INKG)
    return fr

def scene_idea(t, d):
    fr = bg_forest(t, 1.0)
    A = cue("i", 0)
    on = ease_out(seg(t, A - 0.1, 0.9))
    for k in range(3):
        u = ((t - A) * 0.5 + k / 3) % 1.0 if t > A else 1.0
        R = 120 + u * 520
        m = Mk(int(R * 2 + 30), int(R * 2 + 30)); m.ell(R + 15, R + 15, R, fill=0, outline=255, lw=4)
        paste_col(fr, LIME, m.get(), 960 - R - 15, 300 - R - 15, (1 - u) * 0.5 * on)
    c = Ctx(LIME, 230, 230)
    c.ell(115, 115, 112, fill=LIME, line=INKG, lw=6)
    E.camera_icon(c, 115, 118, 1.35, color=INKG)
    cm = Mk(230, 230); cm.ell(115, 115, 115)
    sticker(fr, c.out(), cm.get(), 960, 290, s=back(on) if on < 1 else 1.0, a=on, dx=10, dy=10, sh=(4, 24, 19))
    headline(fr, [words_("The camera doesn't"), [("just", None), ("detect", None), ("low", LIME), ("stock.", LIME)]], 960, 640, 100, t, A, dark=True, per=0.22)
    return fr

def scene_core(t, d):
    fr = bg_paper(t)
    M0, Y0, A0 = cue("core", 0), cue("core", 1), cue("core", 2)
    P = pick_times(); AL = alert_time()
    pin_s = ease_out(seg(t, 0.2, 0.9)); pin_o = ease_out(seg(t, Y0 - 0.6, 0.9))
    removed = {("water", PICK_IDX[k]): P[k] for k in range(4)}
    boxes = ease_out(seg(t, M0 + 0.8, 0.8))
    scan = ((t - M0) * 0.22) % 1.0 if t > M0 else None
    sc, oc = shelf_count(t), odoo_count(t)
    low = sc < MIN_STOCK
    sS, sO = 0.9, 0.8
    cxS, cyS, cxO, cyO = 580, 580, 1590, 580
    tl = shelf_tile(t, removed, boxes, {"water": sc}, low=low, scan=scan)
    sticker(fr, tl, rr_mask(SW, SH, 30), cxS + (1 - pin_s) * -170, cyS, s=sS * pin_s, a=pin_s, dx=12, dy=12, sh=ALERT if low else FOREST)
    chg = prev = None
    for k in range(4):
        tk = P[k] + SYNC_DELAY
        if t >= tk: chg, prev = tk, START_ON_HAND - k
    synced = ease_out(seg(t, Y0 + 0.3, 0.6))
    sticker(fr, odoo_tile(t, oc, chg, prev, synced), rr_mask(600, 700, 30), cxO + (1 - pin_o) * 170, cyO, s=sO * pin_o, a=pin_o, dx=12, dy=12, sh=ALERT if oc < MIN_STOCK else FOREST)
    for k in range(4):
        u_ = seg(t, P[k] + 0.35, SYNC_DELAY - 0.35)
        if 0 < u_ < 1:
            bx = 82 + PICK_IDX[k] * 57
            p0 = tile_pt(bx, 520, SW, SH, cxS, cyS, sS)
            p2 = tile_pt(500, 200 + 2 * 150 + 40, 600, 700, cxO, cyO, sO)
            p1 = ((p0[0] + p2[0]) / 2, min(p0[1], p2[1]) - 150)
            for j in range(9):
                uu = clamp(u_ - j * 0.05)
                if uu <= 0: continue
                x_, y_ = bezier(p0, p1, p2, ease_io(uu))
                m = V._dot(max(3.0, 10 - j * 0.7))
                paste_col(fr, INKG if j == 0 else EMER, m, x_ - m.size[0] / 2, y_ - m.size[1] / 2, 1.0 if j == 0 else 0.7 * (1 - j / 9))
                if j == 0:
                    m2 = V._dot(6); paste_col(fr, LIME, m2, x_ - m2.size[0] / 2, y_ - m2.size[1] / 2, 1.0)
        ripple(fr, *tile_pt(500, 200 + 2 * 150 + 40, 600, 700, cxO, cyO, sO), seg(t, P[k] + SYNC_DELAY, 0.6), EMER)
    headline(fr, [[("Continuously", None), ("monitors", None), ("shelf", LIME), ("quantities", LIME)]], 960, 130, 64, t, M0, t_out=Y0 - 0.2, per=0.12)
    headline(fr, [[("Synchronizes", None), ("inventory", None), ("with", None), ("Odoo", LIME)]], 960, 130, 64, t, Y0, t_out=A0 - 0.2, per=0.12)
    headline(fr, [[("Alerts", None), ("the", None), ("team", None), ("when", None), ("stock", None), ("runs", None), ("low", PEACH)]], 960, 130, 64, t, A0, per=0.12)
    a_ = back(seg(t, AL, 0.6))
    if a_ > 0:
        c = Ctx(ALERT, 1060, 130)
        c.rect(0, 0, 1060, 130, r=28, line=INKG, lw=5)
        bell_icon(c, 84, 74, 1.2, swing=0.35 * math.sin((t - AL) * 16) * math.exp(-(t - AL) * 1.2))
        c.text(156, 48, "Replenishment required", 40, WHITE, "b", anchor="lm")
        c.text(156, 94, "Water 500 ml · 11 on hand · minimum 12 · Shelf A3", 25, (255, 236, 230), "r", anchor="lm")
        c.pill(924, 48, "Warehouse team", INKG, WHITE, size=19, padx=14, h=34)
        c.pill(962, 92, "Notified", LIME, INKG, size=18, padx=14, h=32)
        sticker(fr, c.out(), rr_mask(1060, 130, 28), 960, 972 + (1 - clamp(a_)) * 80, s=1.0, a=clamp(a_ * 1.6), dx=10, dy=10, sh=INKG)
    text_center(fr, "Illustrative interface", 20, "r", 960, 1060, MUT, 0.8)
    return fr

def scene_benefits(t, d):
    fr = bg_lime(t)
    headline(fr, [words_("What you get")], 960, 140, 80, t, 0.3, per=0.18)
    cards = [("Live shelf counts", "Every product, every shelf"), ("Always in sync", "On hand updated in Odoo"), ("Replenish early", "Alerts at your minimum")]
    starts = [cue("b", 0) + 0.05, cue("b", 1) + 0.05, cue("b", 2) + 0.05]
    for i, (h_, sub) in enumerate(cards):
        p = ease_out(seg(t, starts[i], 0.8))
        c = card(500, 500, 34, WHITE, 5)
        cy = 190
        if i == 0:
            for k in range(3):
                c.rect(106, cy - 66 + k * 56, 288, 10, fill=INKG)
                for j in range(4 + k % 2):
                    c.rect(122 + j * 52, cy - 100 + k * 56 + (6 if k % 2 else 0), 36, 34, r=6, fill=[SNACKC, JUICEC, WATERC][k], line=INKG, lw=3)
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                c.line([(250 + dx * 150, cy + dy * 92 - dy * 36), (250 + dx * 150, cy + dy * 92), (250 + dx * 150 - dx * 36, cy + dy * 92)], EMER, 8)
            c.ell(250, cy + 8, 32, fill=EMER, line=INKG, lw=4); c.text(250, cy + 7, str(START_ON_HAND), 30, WHITE, "b")
        if i == 1:
            ang = t * 1.4
            for k in range(2):
                a0 = ang + k * math.pi
                pts = [(250 + 76 * math.cos(a0 + j * 0.07), cy + 76 * math.sin(a0 + j * 0.07)) for j in range(0, 30)]
                c.line(pts, EMER if k == 0 else INKG, 10)
                e = pts[-1]; c.ell(e[0], e[1], 11, fill=EMER if k == 0 else INKG)
            E.odoo_chip(c, 250, cy, 0.62)
        if i == 2:
            bell_icon(c, 250, cy + 14, 2.0, col=ALERT, swing=0.3 * math.sin(t * 5))
        c.text(250, 358, h_, 42, INKG, "b")
        c.text(250, 412, sub, 24, MUT, "r")
        sticker(fr, c.out(), rr_mask(500, 500, 34), 400 + i * 560, 545 + (1 - p) * 90 + math.sin(t + i * 1.3) * 6, s=0.9 + 0.1 * p, a=p, dx=12, dy=12, sh=INKG)
    return fr

def scene_brand(t, d):
    HIT = cue("r", 0) - 0.1
    fr = bg_forest(t, 0.7 + 0.9 * ease_out(seg(t, HIT, 1.5)))
    dust(fr, t, 30, 31, LIME, 0.5)
    build = seg(t, 0.2, HIT - 0.2)
    if t < HIT:
        for k in range(28):
            ang = k / 28 * 2 * math.pi + t * 0.5
            r_ = (1 - build) ** 1.6 * 720 + 20
            m = V._dot(5)
            paste_col(fr, LIME, m, 960 + math.cos(ang) * r_ - m.size[0] / 2, 470 + math.sin(ang) * r_ * 0.6 - m.size[1] / 2, 0.9 * build)
        orb = V._dot(24)
        paste_col(fr, LIME, orb.filter(GB(14)), 960 - orb.size[0] / 2, 470 - orb.size[1] / 2, 0.5 * build)
    if t >= HIT:
        flash = 1 - ease_out(seg(t, HIT, 0.7))
        fr = Image.blend(fr, Image.new("RGB", (W, H), LIME), 0.9 * flash)
        rays = Mk(W, H)
        for k in range(14):
            a0 = k / 14 * 2 * math.pi + (t - HIT) * 0.12
            a1 = a0 + 0.075
            rays.poly([(960, 470), (960 + math.cos(a0) * 1500, 470 + math.sin(a0) * 1500), (960 + math.cos(a1) * 1500, 470 + math.sin(a1) * 1500)])
        paste_col(fr, LIME, rays.get().filter(GB(10)), 0, 0, 0.16 * ease_out(seg(t, HIT, 1.0)))
        for k in range(3):
            u = ((t - HIT) * 0.55 + k / 3) % 1.0
            R = 170 + u * 720
            m = Mk(int(R * 2 + 30), int(R * 2 + 30)); m.ell(R + 15, R + 15, R, fill=0, outline=255, lw=4)
            paste_col(fr, LIME, m.get(), 960 - R - 15, 470 - R - 15, (1 - u) * 0.55 * ease_out(seg(t, HIT, 0.5)))
        tl, mk = brand_tile(320)
        p = back(seg(t, HIT, 0.9))
        V.place(fr, tl, mk, 960, 470, s=p * (1 + 0.012 * math.sin(t * 1.4)), a=clamp(p * 2), glow=(LIME, 0.9, 34))
        burst(fr, 960, 470, seg(t, HIT, 1.6), LIME, n=34, R=520)
        headline(fr, [[("SKIT", None), ("AI", None), ("Vision", None)]], 960, 785, 112, t, cue("r", 0), dark=True, per=0.18)
        a = ease_out(seg(t, cue("r", 1), 0.8))
        text_center(fr, "B Y     S R I K E S H     I N F O T E C H", 30, "s", 960, 880, LIME, 0.95 * a, (1 - a) * 8)
        a2 = ease_out(seg(t, cue("r", 2), 0.7))
        tl2, mk2 = sticker_pill("Every shelf. Always in sync.", 36, INKG, LIME, icon="spark", padx=46, h=84)
        sticker(fr, tl2, mk2, 960, 985 + (1 - a2) * 24, s=0.9 + 0.1 * a2, a=a2, dx=8, dy=8, sh=(4, 24, 19))
    return fr

SCENES = [scene_hook, scene_idea, scene_core, scene_benefits, scene_brand]
TRANS = [("panel", FOREST), ("panel", LIME), ("panel", FOREST), ("iris", FOREST)]

def panel(A, B, p, color):
    if p < 0.5:
        L = int(W * (1 - 2 * p)); out = A.copy()
        out.paste(color, (L, 0, W, H)); out.paste(LIME if color != LIME else INKG, (max(0, L - 10), 0, L, H))
    else:
        R = int(W * (2 - 2 * p)); out = B.copy()
        out.paste(color, (0, 0, R, H)); out.paste(LIME if color != LIME else INKG, (R, 0, min(W, R + 10), H))
    return out

def iris(A, B, p, color):
    r = math.hypot(W / 2, H / 2) * 1.05 * ease_io(clamp(p * 1.5))
    m = Image.new("L", (W // 2, H // 2), 0)
    ImageDraw.Draw(m).ellipse((W / 4 - r / 2, H / 4 - r / 2, W / 4 + r / 2, H / 4 + r / 2), fill=255)
    m = m.resize((W, H), Image.BILINEAR)
    out = A.copy(); out.paste(color, (0, 0), m)
    return Image.blend(out, B, ease_io(seg(p, 0.55, 0.45)))

_vy, _vx = np.mgrid[0:H, 0:W].astype(np.float32)
VIGN = (1.0 - 0.06 * (((_vx - W / 2) / (W / 2)) ** 2 * 0.6 + ((_vy - H / 2) / (H / 2)) ** 2 * 0.9)).clip(0.7, 1.0)[..., None]

def grade(img):
    arr = np.asarray(img).astype(np.float32) * VIGN
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

def load_tl(): return json.loads((HERE / "timeline.json").read_text("utf8"))

def frame_at(T, tl):
    starts, durs = tl["starts"], tl["durs"]
    n = len(SCENES); hh = 0.7 / 2
    img = None
    for i in range(n - 1):
        tb = starts[i + 1]
        if tb - hh <= T < tb + hh:
            A = SCENES[i](T - starts[i], durs[i]); B = SCENES[i + 1](max(0.0, T - starts[i + 1]), durs[i + 1])
            kind, col = TRANS[i]
            p = (T - (tb - hh)) / 0.7
            img = panel(A, B, p, col) if kind == "panel" else iris(A, B, p, col)
            break
    if img is None:
        i = max(k for k in range(n) if starts[k] <= T + 1e-9)
        img = SCENES[i](T - starts[i], durs[i])
    img = grade(img)
    T_end = tl["total"]
    if T < 0.6: img = Image.blend(Image.new("RGB", (W, H), PAPER), img, ease_out(T / 0.6))
    if T > T_end - 1.1: img = Image.blend(img, Image.new("RGB", (W, H), (3, 20, 15)), seg(T, T_end - 1.1, 1.1))
    return img

_TL = None
def render_frame(n):
    global _TL
    if _TL is None: _TL = load_tl()
    return np.asarray(frame_at(n / FPS, _TL).convert("RGB")).tobytes()

# ---- audio / timeline
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
    nir = int(0.6 * sr); tir = np.arange(nir) / sr
    ir = rng.standard_normal(nir) * np.exp(-tir * 8.0); ir[:int(0.004 * sr)] = 0; ir /= np.sqrt((ir ** 2).sum())
    wet = sps.fftconvolve(voice, ir, mode="full")[:N]
    voice_m = (voice * 0.95 + wet * 0.12).astype(np.float32)
    sc = sid_start
    Tm = {"idea": sc["i"] + cue("i", 0) - 0.1, "core": sc["core"] + 0.1, "alert": sc["core"] + alert_time(), "benefits": sc["b"] + 0.1,
          "brand_riser": sc["r"] + 0.0, "brand_hit": sc["r"] + cue("r", 0) - 0.1}
    extras = []
    for k, p in enumerate(pick_times()):
        extras.append((sc["core"] + p + 0.05, "pop", 0.10)); extras.append((sc["core"] + p + SYNC_DELAY, "tick", 0.12))
    extras.append((sc["core"] + cue("core", 1) - 0.7, "whoosh", 0.10))
    for k in range(len(starts) - 1):
        extras.append((starts[k + 1] - 0.45, "whoosh", 0.12))
    mus = music.make_music(total, Tm, extras)
    if len(mus) < N: mus = np.pad(mus, ((0, N - len(mus)), (0, 0)))
    mus = mus[:N]
    env = np.abs(voice)
    sos = sps.butter(2, 5.0, "low", fs=sr, output="sos")
    env = np.clip(sps.sosfiltfilt(sos, env) / 0.07, 0, 1)
    duck = 1.0 - 0.55 * env
    mix_ = mus * (0.80 * duck)[:, None] + np.stack([voice_m, voice_m], 1) * 1.0
    peak = np.abs(mix_).max()
    mix_ = mix_ / max(1.0, peak / 0.93)
    out = wave.open(str(HERE / "audio2.wav"), "wb")
    out.setnchannels(2); out.setsampwidth(2); out.setframerate(sr)
    out.writeframes((mix_[: int((total + 0.2) * sr)] * 32767).astype(np.int16).tobytes()); out.close()
    (HERE / "timeline.json").write_text(json.dumps({"starts": starts, "durs": durs, "total": round(total, 3)}), "utf8")
    def ts(x):
        h, r = divmod(x, 3600); m, s_ = divmod(r, 60); return "%02d:%02d:%02d,%03d" % (h, m, int(s_), int((s_ % 1) * 1000))
    with open(HERE / "captions2.srt", "w", encoding="utf8") as f:
        for i, (at, L_, sid, k, show) in enumerate(plan, 1):
            f.write(f"{i}\n{ts(at)} --> {ts(at + L_)}\n{show}\n\n")
    print("scenes", [round(x, 1) for x in durs], "total", round(total, 1))

def render(outfile):
    tl = load_tl(); frames = int(math.ceil(tl["total"] * FPS))
    cmd = [str(HERE / "ffmpeg.exe"), "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", str(HERE / "audio2.wav"), "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p",
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
            frame_at(float(s), tl).save(HERE / f"ad2_{float(s):05.1f}.png")
        print("saved")
    elif cmd == "render": render(sys.argv[2])
