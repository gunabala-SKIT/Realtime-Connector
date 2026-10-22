"""SKIT AI Vision explainer v3: real footage as the live camera feed, six-operation production line, animated cursor.

    python explainer3.py prep | still 4 12 ... | render out.mp4
"""
import json, math, subprocess, sys, wave
from functools import lru_cache
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

import explainer2 as V
from explainer2 import (CORAL, CREAM, DARK, GB, INK, LAV, LAV2, LIGHT, MINT, MUTED_D, MUTED_L, VIO, W, H, FPS, TR, Ctx, E, Mk, back, bg_dark,
                        bg_light, clamp, ease_io, ease_out, kinetic, lerp, mix, paste_col, particles, pill_tile, place, put_text, rr_mask,
                        scale_mask, seg, sweep_dot, text_center, transition, twidth, words, zoomed, logo_tile, spark)

HERE = Path(__file__).resolve().parent
FRAMES = HERE / "frames"
AMBER, BLUE = E.AMBER, E.BLUE
GREEN = E.GREEN

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

# ---------------------------------------------------------------- footage + tracks
@lru_cache(maxsize=48)
def footage(i):
    return Image.open(FRAMES / f"{i:04d}.jpg").convert("RGB")

def fidx(tc): return int(clamp(tc * 25.0, 0, 664)) + 1

_tracks = None
def person_box(i):
    global _tracks
    if _tracks is None:
        d = json.load(open(HERE / "tracks.json"))["person"]
        arr = np.array([p if p else [0, 0, 0, 0] for p in d], np.float32)
        for k in range(1, len(arr)): arr[k] = arr[k - 1] * 0.55 + arr[k] * 0.45
        for k in range(len(arr) - 2, -1, -1): arr[k] = arr[k + 1] * 0.55 + arr[k] * 0.45
        _tracks = arr
    return _tracks[min(max(i - 1, 0), len(_tracks) - 1)].tolist()

TOOL_KF = [(105, (560, 566)), (120, (557, 565)), (180, (479, 693)), (240, (516, 690)), (300, (574, 669)), (360, (650, 673)),
           (420, (667, 666)), (480, (656, 663)), (540, (698, 643)), (600, (692, 633)), (665, (695, 609))]

def tool_xy(i):
    if i < TOOL_KF[0][0]: return None
    for (f0, p0), (f1, p1) in zip(TOOL_KF, TOOL_KF[1:]):
        if f0 <= i <= f1:
            u = ease_io((i - f0) / (f1 - f0))
            return (lerp(p0[0], p1[0], u), lerp(p0[1], p1[1], u))
    return TOOL_KF[-1][1]

CLOTH_BOX = (232, 566, 1010, 718)
SCISSORS_BOX = (694, 620, 830, 666)

def feed_tile(tc, t, boxes=None, tag=("Marking", E.BLUE), zone=1.0, boxes_t0=None):
    """The 'live camera' tile: footage + the product's overlays. boxes: dict name->alpha (0..1)."""
    i = fidx(tc)
    c = Ctx((0, 0, 0), 1280, 720)
    c.im.paste(footage(i).resize((2560, 1440), Image.BILINEAR), (0, 0))
    c.d = ImageDraw.Draw(c.im, "RGBA")
    boxes = boxes or {}
    if zone > 0:
        zp = [(215, 574), (1030, 574), (1140, 716), (150, 716)]
        for k in range(4):
            c.dashed(zp[k], zp[(k + 1) % 4], (255, 255, 255), 4, 22, 14, a=zone * 0.9, off=t * 26)
        c.rect(930, 532, 190, 36, r=8, fill=(19, 3, 11, 205), a=zone)
        c.text(1025, 550, "Marking zone", 21, (255, 255, 255), "s", a=zone)

    def box(b, col, label, a, tx=(255, 255, 255)):
        if a <= 0.01: return
        x0, y0, x1, y1 = b
        c.rect(x0, y0, x1 - x0, y1 - y0, r=8, line=col, lw=4, a=a)
        w_ = c.tw(label, 20, "s") + 22
        c.rect(x0, y0 - 32, w_, 32, r=6, fill=col, a=a)
        c.text(x0 + 11, y0 - 16, label, 20, tx, "s", anchor="lm", a=a)

    pb = person_box(i)
    box(pb, E.GREEN, "Person", boxes.get("person", 0))
    box(CLOTH_BOX, (255, 255, 255), "Cloth", boxes.get("cloth", 0), INK)
    box(SCISSORS_BOX, AMBER, "Scissors", boxes.get("scissors", 0) * 0.9, INK)
    txy = tool_xy(i)
    if txy:
        x, y = txy
        box((x - 46, y - 42, x + 40, y + 24), E.PINK, "Marking tool", boxes.get("tool", 0))
    if tag and boxes.get("person", 0) > 0.2:
        lab, col = tag
        cx = (pb[0] + pb[2]) / 2
        c.pill(cx, pb[1] - 58, lab, (255, 255, 255), col, size=23, padx=20, h=42, a=boxes["person"])
    c.rect(24, 22, 200, 42, r=21, fill=(19, 3, 11, 190))
    c.ell(52, 43, 8, fill=(255, 77, 77) if int(t * 2) % 2 == 0 else (122, 32, 32))
    c.text(70, 43, "CAM 01 · LIVE", 20, (255, 255, 255), "s", anchor="lm")
    return c.out()

def graded_bg(tc, t, strength=0.62, blur=4, zoom=1.0):
    fr = footage(fidx(tc)).resize((W, H), Image.BILINEAR)
    if zoom != 1.0: fr = zoomed(fr, zoom)
    fr = fr.filter(GB(blur))
    fr = Image.blend(fr, Image.new("RGB", (W, H), (30, 8, 50)), strength)
    glow = bg_dark(t, 0.9).point(lambda v: int(v * 0.65))
    return ImageChops.screen(fr, glow)

# ---------------------------------------------------------------- icons (white masks, drawn in a 120x120 box)
def icon_mask(kind):
    m = Mk(120, 120)
    if kind == "marking":
        m.line([(34, 88), (82, 40)], 15)
        m.poly([(84, 38), (96, 26), (100, 34)], 255)
        m.line([(20, 100), (36, 100)], 5); m.line([(46, 100), (62, 100)], 5); m.line([(72, 100), (88, 100)], 5)
    if kind == "cutting":
        m.line([(36, 20), (84, 84)], 6); m.line([(84, 20), (36, 84)], 6)
        m.ell(34, 94, 14, fill=0, outline=255, lw=6); m.ell(86, 94, 14, fill=0, outline=255, lw=6)
    if kind == "stitching":
        m.line([(24, 30), (96, 30)], 6); m.line([(78, 30), (96, 30)], 6)
        m.ell(92, 30, 4, fill=0)
        for k in range(4):
            x = 24 + k * 20
            m.line([(x, 84), (x + 10, 70)], 5); m.line([(x + 10, 70), (x + 20, 84)], 5)
        m.line([(60, 36), (60, 66)], 3)
    if kind == "pressing":
        m.poly([(18, 78), (28, 44), (88, 44), (104, 78)], 255)
        m.line([(24, 90), (104, 90)], 8)
        m.rr(40, 24, 40, 20, 8, fill=0, outline=255, lw=6)
        for k in range(3): m.ell(48 + k * 16, 62, 3, fill=0)
    if kind == "quality":
        m.ell(52, 52, 30, fill=0, outline=255, lw=8)
        m.line([(75, 76), (100, 102)], 10)
        m.line([(38, 52), (49, 64), (68, 40)], 7)
    if kind == "packing":
        m.poly([(60, 18), (100, 38), (60, 58), (20, 38)], 255)
        m.poly([(20, 44), (56, 62), (56, 104), (20, 86)], 255)
        m.poly([(64, 62), (100, 44), (100, 86), (64, 104)], 255)
        m.line([(60, 18), (60, 58)], 0, 0)
    return m.get()

@lru_cache(maxsize=None)
def icon_cached(kind):
    pad = Image.new("L", (180, 180), 0); pad.paste(icon_mask(kind), (30, 30)); return pad

STATIONS = [("Marking", "marking"), ("Cutting", "cutting"), ("Stitching", "stitching"), ("Pressing", "pressing"), ("Quality check", "quality"), ("Packing", "packing")]

# ---------------------------------------------------------------- cursor and effects
def cursor(frame, x, y, press=0.0, a=1.0):
    pts = [(0, 0), (0, 30), (8, 23), (14, 36), (20, 33), (14, 21), (24, 21)]
    s = 1.25 - 0.15 * press
    m_out = Mk(60, 60); m_out.poly([(4 + p[0] * s, 4 + p[1] * s) for p in pts])
    m_in = Mk(60, 60); m_in.poly([(7 + p[0] * s * 0.82, 7 + p[1] * s * 0.82) for p in pts])
    paste_col(frame, (19, 3, 11), m_out.get().filter(GB(0.6)), x - 4, y - 4, a)
    paste_col(frame, (255, 255, 255), m_in.get(), x - 4, y - 4, a)

def ripple(frame, x, y, p, col=LAV):
    if p <= 0 or p >= 1: return
    R = 12 + 60 * ease_out(p)
    m = Mk(int(R * 2 + 20), int(R * 2 + 20)); m.ell(R + 10, R + 10, R, fill=0, outline=255, lw=3)
    paste_col(frame, col, m.get(), x - R - 10, y - R - 10, (1 - p) * 0.9)

def burst(frame, cx, cy, p, col=MINT, n=26, R=260):
    if p <= 0 or p >= 1: return
    for k in range(n):
        ang = k / n * 2 * math.pi + (k % 3) * 0.2
        d = ease_out(p) * R * (0.6 + 0.4 * ((k * 7) % 5) / 4)
        r = 4 + (k % 3) * 2
        m = V._dot(r)
        paste_col(frame, col if k % 2 else (255, 255, 255), m, cx + math.cos(ang) * d - m.size[0] / 2, cy + math.sin(ang) * d - m.size[1] / 2, (1 - p) ** 1.3)

def glass(w, h, r=30, fill=(30, 17, 48), line=(104, 80, 180)):
    c = Ctx(fill, w, h)
    c.rect(1, 1, w - 2, h - 2, r=r, line=line, lw=2)
    return c

# ---------------------------------------------------------------- scenes
_CUES = None
def CU():
    global _CUES
    if _CUES is None: _CUES = json.load(open(HERE / "cues.json"))
    return _CUES
def cue(sid, k): return CU()[sid][k]                  # local start (s) of voice phrase k in scene sid
def cue_end(sid, k): return CU()[sid + "_end"][k]
RUN = 0.9
def s6_times():
    clicked = cue_end("s6", 0) - 0.9
    return clicked, [cue("s6", 1) + 1.2 + i * 0.85 for i in range(6)]
def s7_times():
    return cue_end("s7", 0) - 0.15, cue("s7", 1) + 2.2

def scene1(t, d):
    tc = t
    fr = graded_bg(tc, t, strength=0.58, blur=3, zoom=1.0 + 0.05 * t / d)
    # viewfinder corners + scan line
    a = ease_out(seg(t, 0.2, 0.6))
    m = Mk(W, H)
    L, T_, R_, B_ = 90, 80, W - 90, H - 80
    for sx, sy, x, y in ((1, 1, L, T_), (-1, 1, R_, T_), (1, -1, L, B_), (-1, -1, R_, B_)):
        m.line([(x, y + sy * 80), (x, y), (x + sx * 80, y)], 5)
    paste_col(fr, CREAM, m.get(), 0, 0, 0.85 * a)
    sc = (t * 0.45) % 1.0
    sx = lerp(L, R_, ease_io(sc))
    sm = Mk(80, H); sm.rr(36, 90, 6, H - 180, 3)
    paste_col(fr, LAV, sm.get().filter(GB(10)), sx - 40, 0, 0.55 * a)
    paste_col(fr, LAV2, sm.get(), sx - 40, 0, 0.7 * a)
    kinetic(fr, [[("Do", None), ("you", None), ("really", None), ("know", None), ("what", None), ("is", None)], [("running", LAV), ("on", None), ("your", None)], [("production", None), ("floor,", None), ("right", None), ("now?", LAV)]], 960, 540, 100, t, 0.5, per=0.2, dur=0.6)
    return fr

def scene2(t, d):
    fr = bg_dark(t, 1.0, cx=W * 0.3)
    particles(fr, t, 18, seed=5)
    p = back(seg(t, 0.2, 0.8)); bob = math.sin(t * 1.1) * 8
    for k, (dx, dy, rl, al) in enumerate([(-46, 26, -5, 0.45), (48, -20, 4, 0.6)]):
        c = Ctx((236, 232, 224), 560, 660)
        for r_ in range(4): c.line([(60, 190 + r_ * 110), (500, 190 + r_ * 110)], (214, 208, 196), 3, cap=False)
        place(fr, c.out(), rr_mask(560, 660, 26), 560 + dx, 540 + dy + bob, s=0.82 * p, yaw=-14, pitch=3, roll=rl, a=al * clamp(p), shadow=0.3)
    c = Ctx((250, 248, 242), 560, 660)
    c.text(44, 74, "JOB SHEET", 34, INK, "b", anchor="lm")
    c.text(44, 114, "Order 00021", 22, (120, 114, 100), "r", anchor="lm")
    ang = t * 6
    c.ell(496, 80, 28, line=INK, lw=4)
    c.line([(496, 80), (496 + 17 * math.sin(ang), 80 - 17 * math.cos(ang))], INK, 4)
    c.line([(496, 80), (496 + 10 * math.sin(ang / 12), 80 - 10 * math.cos(ang / 12))], INK, 4)
    rows = [("Marking", cue("s2", 1) + 0.9, "tick", "2 h late", E.RED), ("Cutting", cue("s2", 2) + 0.2, "q", "not sure", AMBER), ("Stitching", cue("s2", 2) + 1.2, "wrong", "wrong qty", E.RED), ("Pressing", 99, "none", "never entered", (120, 114, 100))]
    for i, (name, ts, kind, tag, tc_) in enumerate(rows):
        y = 200 + i * 115
        c.text(104, y, name, 32, INK, "s", anchor="lm")
        c.line([(104, y + 36), (516, y + 36)], (222, 216, 202), 3, cap=False)
        c.rect(44, y - 19, 38, 38, r=8, line=(120, 114, 100), lw=3)
        pp = seg(t, ts, 0.35)
        if pp > 0 and kind == "tick": E.check(c, 63, y, 1.2, E.GREEN, 7, a=pp)
        if pp > 0 and kind == "q": c.text(63, y, "?", 32, AMBER, "b", a=pp)
        if pp > 0 and kind == "wrong": c.line([(51, y - 12), (75, y + 12)], E.RED, 6, pp); c.line([(75, y - 12), (51, y + 12)], E.RED, 6, pp)
        pa = pp if kind != "none" else seg(t, cue("s2", 2) + 2.3, 0.4)
        if pa > 0: c.pill(420, y - 2, tag, tc_, (tc_[0], tc_[1], tc_[2], 40), size=21, padx=13, h=36, a=pa)
    place(fr, c.out(), rr_mask(560, 660, 26), 560, 540 + bob, s=0.82 * p, yaw=-14, pitch=3, a=clamp(p), shadow=0.5)
    cx = 1370
    kinetic(fr, [words("Written"), [("on", None), ("paper.", LAV)]], cx, 520, 90, t, cue("s2", 0) + 0.2, t_out=cue("s2", 1) - 0.2)
    kinetic(fr, [words("Typed"), [("in", None), ("hours", None), ("later.", CORAL)]], cx, 520, 90, t, cue("s2", 1) + 0.1, t_out=cue("s2", 2) - 0.2)
    kinetic(fr, [[("Late,", None), ("wrong,", None)], [("or", None), ("never", CORAL), ("entered.", None)]], cx, 520, 90, t, cue("s2", 2) + 0.1)
    return fr

def scene3(t, d):
    fr = bg_dark(t, 0.9, cx=W * 0.68)
    particles(fr, t, 16, seed=9)
    cards_a = 1 - seg(t, cue("s3", 2) - 0.3, 0.5)
    for k, (cx0, title, rows, yaw) in enumerate([(520, "In Odoo", [("Marking", "In progress", "progress"), ("Cutting", "Waiting", "wait")], 12),
                                                  (1400, "On the floor", [("Marking", "Finished", "done"), ("Cutting", "Running", "cut")], -12)]):
        p = ease_out(seg(t, (cue("s3", 0) - 0.1) if k == 0 else (cue("s3", 1) - 0.1), 0.8))
        c = Ctx((255, 255, 255), 560, 360)
        c.text(36, 56, title, 36, INK, "b", anchor="lm")
        for i, (n, st_, kd) in enumerate(rows):
            y = 100 + i * 122
            c.rect(30, y, 500, 100, r=18, fill=(247, 244, 252), line=(232, 226, 244), lw=2)
            c.text(60, y + 50, n, 32, INK, "s", anchor="lm")
            E.badge(c, 440, y + 50, st_, kd, size=22)
        place(fr, c.out(), rr_mask(560, 360, 28), cx0 + (1 - p) * (-380 if k == 0 else 380), 520 + math.sin(t * 1.2 + k) * 7, s=0.92, yaw=yaw * (0.4 + 0.6 * p), pitch=2,
              a=p * cards_a, shadow=0.5, blur=(1 - cards_a) * 12)
    pp = back(seg(t, cue("s3", 1) + 0.9, 0.5)); pul = 1 + 0.07 * math.sin(t * 6)
    m = Mk(150, 150); m.ell(75, 75, 56); m.line([(55, 62), (95, 62)], 5, 0); m.line([(55, 88), (95, 88)], 5, 0); m.line([(88, 50), (62, 100)], 5, 0)
    mm = m.get().resize((int(150 * pp * pul) + 1, int(150 * pp * pul) + 1), Image.BICUBIC)
    paste_col(fr, CORAL, mm.filter(GB(18)), 960 - mm.size[0] / 2, 520 - mm.size[1] / 2, 0.7 * cards_a * clamp(pp))
    paste_col(fr, CORAL, mm, 960 - mm.size[0] / 2, 520 - mm.size[1] / 2, cards_a * clamp(pp))
    kinetic(fr, [words("Odoo shows old status.")], 960, 190, 68, t, cue("s3", 0), t_out=cue("s3", 1) - 0.15, per=0.13)
    kinetic(fr, [[("The", None), ("floor", None), ("tells", None), ("another", None), ("story.", CORAL)]], 960, 190, 68, t, cue("s3", 1), t_out=cue("s3", 2) - 0.15, per=0.13)
    kinetic(fr, [words("What you can't see, costs you.")], 960, 190, 68, t, cue("s3", 2) + 0.1, per=0.12)
    for i, (lab, ts) in enumerate([("No live status", cue("s3", 2) + 0.3), ("Time per operation unknown", cue("s3", 2) + 1.9), ("Idle time hidden", cue("s3", 2) + 3.8)]):
        a = ease_out(seg(t, ts, 0.6))
        tl, mk2 = pill_tile(lab, 40, CREAM, (30, 14, 28), dot=CORAL, h=104, padx=60)
        place(fr, tl, mk2, 960, 360 + i * 150 + (1 - a) * 36, s=0.8 + 0.2 * a, a=a, blur=(1 - a) * 10, glow=(CORAL, 0.55 + 0.25 * math.sin(t * 2 + i), 16))
    return fr

def scene4(t, d):
    fr = bg_light(t, 1.0)
    particles(fr, t, 20, dark=False, seed=11)
    for k in range(3):
        u = (t * 0.28 + k / 3) % 1.0
        R = 150 + u * 380
        m = Mk(int(R * 2 + 30), int(R * 2 + 30)); m.ell(R + 15, R + 15, R, fill=0, outline=255, lw=3)
        paste_col(fr, VIO, m.get(), 960 - R - 15, 330 - R - 15, (1 - u) * 0.22 * ease_out(seg(t, 0.5, 0.8)))
    # orbiting dots
    for k in range(3):
        ang = t * 0.9 + k * 2.094
        rr_ = 190 + 20 * k
        dm = V._dot(6)
        paste_col(fr, VIO if k != 1 else MINT, dm, 960 + math.cos(ang) * rr_ * 1.25 - 8, 330 + math.sin(ang) * rr_ * 0.62 - 8, 0.85 * ease_out(seg(t, 1.0 + k * 0.2, 0.6)))
    p = back(seg(t, 0.2, 0.7))
    tl, mk = brand_tile(250)
    place(fr, tl, mk, 960, 330, s=p, a=clamp(p * 2), shadow=0.28, shadow_col=(80, 40, 160), sh_blur=34, sh_dy=30, glow=(VIO, 0.35, 20))
    kinetic(fr, [[("SKIT", None), ("AI", None), ("Vision", None)]], 960, 580, 124, t, cue("s4", 0) + 0.1, wt="sl", per=0.16, dark=False)
    a = ease_out(seg(t, cue("s4", 1), 0.7))
    tl, mk2 = pill_tile("Live production tracking for Odoo", 36, CREAM, (19, 6, 28), icon="spark", padx=46, h=84)
    place(fr, tl, mk2, 960, 760 + (1 - a) * 26, s=0.9 + 0.1 * a, a=a, glow=(VIO, 0.6 + 0.3 * math.sin(t * 2), 18), shadow=0.35, shadow_col=(90, 50, 170), sh_blur=26, sh_dy=22)
    return fr

def scene5(t, d):
    tc = 3.2 + t * 0.95
    fr = bg_dark(t, 1.0, cx=W * 0.35)
    particles(fr, t, 16, seed=13)
    kinetic(fr, [words("How it works")], 960, 100, 62, t, 0.3, per=0.18)
    p = ease_out(seg(t, 0.2, 1.0))
    A5 = cue("s5", 0)
    boxes = {"person": ease_out(seg(t, A5 + 1.5, 0.4)), "cloth": ease_out(seg(t, A5 + 2.9, 0.4)), "tool": ease_out(seg(t, A5 + 4.0, 0.4)), "scissors": ease_out(seg(t, A5 + 4.9, 0.4))}
    tag = ("Marking", E.BLUE) if t > cue("s5", 1) + 0.9 else ("Detecting…", (120, 100, 170))
    tl = feed_tile(tc, t, boxes, tag=tag, zone=ease_out(seg(t, A5 + 0.7, 0.6)))
    place(fr, tl, rr_mask(1280, 720, 34), 800 + (1 - p) * -140, 590, s=0.78, yaw=lerp(-13, -7, ease_io(t / d)), pitch=lerp(4, 1.5, ease_io(t / d)), a=p, shadow=0.6, glow=((140, 100, 230), 0.32 * p, 12))
    # pipeline steps (right)
    steps = [("1", "Camera watches", cue("s5", 0) + 0.1), ("2", "AI recognises", cue("s5", 1)), ("3", "Rules decide", cue("s5", 2)), ("4", "Odoo updates", cue("s5", 3))]
    for i, (n, lab, ts) in enumerate(steps):
        a = ease_out(seg(t, ts, 0.55))
        on = seg(t, ts, 0.3)
        c = Ctx((30, 17, 48), 420, 96)
        c.rect(1, 1, 418, 94, r=24, line=mix((104, 80, 180), MINT, on * 0.0) if False else (104, 80, 180), lw=2)
        c.ell(52, 48, 24, fill=VIO if on < 1 or True else MINT)
        c.text(52, 47, n, 26, (255, 255, 255), "b")
        c.text(94, 48, lab, 32, CREAM, "s", anchor="lm")
        place(fr, c.out(), rr_mask(420, 96, 24), 1560 + (1 - a) * 80, 330 + i * 118 + 0 * math.sin(t + i), a=a, shadow=0.4, blur=(1 - a) * 8, glow=(LAV, 0.5 * on, 12))
    # mini Odoo card with Live toggle + cursor
    D5 = cue("s5", 3)
    a9 = ease_out(seg(t, D5, 0.6))
    if a9 > 0:
        live = seg(t, D5 + 1.5, 0.3)
        c = Ctx((255, 255, 255), 420, 190)
        E.odoo_chip(c, 90, 44, 0.62)
        c.text(30, 100, "Marking", 32, INK, "s", anchor="lm")
        E.badge(c, 300, 100, "In progress", "progress", size=22)
        c.text(30, 150, "Auto-update", 22, MUTED_L, "r", anchor="lm")
        for k, (lab_, x) in enumerate((("Shadow", 230), ("Live", 330))):
            on_ = (k == 1 and live > 0.5) or (k == 0 and live <= 0.5)
            c.rect(x - 46, 132, 92, 36, r=18, fill=(124, 92, 255) if on_ else (236, 232, 246))
            c.text(x, 150, lab_, 20, (255, 255, 255) if on_ else MUTED_L, "s")
        place(fr, c.out(), rr_mask(420, 190, 26), 1560 + (1 - a9) * 80, 842, a=a9, shadow=0.45, glow=(VIO, 0.5 * live, 14))
        # cursor path: enters, moves to Live, clicks
        cu = ease_io(seg(t, D5 + 0.6, 0.9))
        cx_, cy_ = lerp(1700, 1560 + 60, cu), lerp(700, 842 + 20, cu)
        press = seg(t, D5 + 1.5, 0.15) * (1 - seg(t, D5 + 1.7, 0.15))
        cursor(fr, cx_, cy_, press, a=a9)
        ripple(fr, 1620, 862, seg(t, D5 + 1.5, 0.6))
    return fr

def scene6(t, d):
    fr = bg_dark(t, 1.0, cx=W * 0.5)
    particles(fr, t, 22, seed=17)
    kinetic(fr, [[("Your", None), ("operations,", LAV), ("in", None), ("your", None), ("order.", None)]], 960, 120, 64, t, 0.3, per=0.12)
    # start button + cursor
    clicked, ts_ = s6_times()
    ap = ease_out(seg(t, 0.4, 0.6))
    tl, mk = pill_tile("Start order · MO 00021", 30, CREAM, (22, 120, 80) if t > clicked else (30, 17, 48), h=72, padx=40)
    place(fr, tl, mk, 330, 250, a=ap, s=0.95 + 0.05 * math.sin(t * 6) * (1 if t > clicked and t < clicked + 0.5 else 0), glow=(MINT if t > clicked else LAV, 0.6, 14))
    cu = ease_io(seg(t, clicked - 1.3, 1.2))
    press = seg(t, clicked, 0.12) * (1 - seg(t, clicked + 0.15, 0.15))
    cursor(fr, lerp(1100, 360, cu), lerp(520, 262, cu), press, a=ap * (1 - seg(t, clicked + 1.1, 0.4)))
    ripple(fr, 340, 262, seg(t, clicked, 0.6), MINT)
    # stations
    n = len(STATIONS)
    xs = [160 + i * 304 for i in range(n)]
    track_p = ease_io(seg(t, clicked + 0.3, 2.0))
    y_track = 760
    for i in range(n - 1):
        x0, x1 = xs[i] + 30, xs[i + 1] - 30
        pr_ = clamp(track_p * (n - 1) - i)
        if pr_ > 0:
            m = Mk(int(x1 - x0) + 20, 24); m.line([(10, 12), (10 + (x1 - x0) * pr_, 12)], 3)
            mm = m.get()
            paste_col(fr, LAV, mm.filter(GB(6)), x0 - 10, y_track - 12, 0.5); paste_col(fr, LAV2, mm, x0 - 10, y_track - 12, 0.9)
    for i, (name, kind) in enumerate(STATIONS):
        enter = ease_out(seg(t, 0.5 + i * 0.18, 0.7))
        st_start, st_end = ts_[i], ts_[i] + RUN
        prog = seg(t, st_start, RUN)
        state = "wait" if t < st_start else ("run" if t < st_end else "done")
        flash = seg(t, st_end, 0.5)
        c = Ctx((30, 17, 48), 280, 400)
        c.rect(1, 1, 278, 398, r=30, line=(104, 80, 180) if state == "wait" else ((140, 100, 240) if state == "run" else (60, 190, 130)), lw=3)
        c.ell(46, 46, 20, fill=(70, 50, 110) if state == "wait" else (VIO if state == "run" else (47, 203, 138)))
        c.text(46, 45, str(i + 1), 22, (255, 255, 255), "b")
        col = (150, 130, 190) if state == "wait" else (CREAM if state == "run" else MINT)
        c.text(140, 330, name, 29 if len(name) < 11 else 26, CREAM, "b")
        # status chip
        lab = {"wait": "Waiting", "run": "In progress", "done": "Finished"}[state]
        kd = {"wait": "wait", "run": "progress", "done": "done"}[state]
        c.pill(140, 372, lab, (180, 160, 210) if state == "wait" else ((255, 255, 255) if state == "run" else (190, 255, 220)),
               (60, 44, 90) if state == "wait" else ((124, 92, 255) if state == "run" else (22, 120, 80)), size=19, padx=14, h=34)
        if state == "run":
            c.rect(40, 296, 200, 8, r=4, fill=(60, 44, 90)); c.rect(40, 296, 200 * prog, 8, r=4, fill=(124, 92, 255))
        else:
            c.rect(40, 296, 200, 8, r=4, fill=(60, 44, 90) if state == "wait" else (22, 120, 80))
        place(fr, c.out(), rr_mask(280, 400, 30), xs[i] + 40, 520 + (1 - enter) * 90 - (6 * math.sin(t * 1.2 + i) if state != "wait" else 0) - (10 * (1 - flash) * 0 if True else 0),
              s=0.98 if state != "run" else 1.0 + 0.03 * math.sin(t * 6), a=enter, pitch=(1 - enter) * -12, shadow=0.5, blur=(1 - enter) * 8,
              glow=((VIO if state == "run" else (MINT if state == "done" else (140, 100, 230))), (0.8 if state == "run" else 0.35 + 0.3 * (1 - flash)), 16))
        # icon (white mask tinted)
        im_ = icon_cached(kind)
        ic = (150, 130, 190) if state == "wait" else (255, 255, 255) if state == "run" else MINT
        sc = 1.0 + (0.12 * math.sin(math.pi * clamp(flash * 2)) if state == "done" else 0) + (0.05 * math.sin(t * 8) if state == "run" else 0)
        ims = im_.resize((int(180 * 1.35 * sc), int(180 * 1.35 * sc)), Image.BICUBIC)
        yy = 520 + (1 - enter) * 90 - 70
        if state == "run": paste_col(fr, VIO, ims.filter(GB(14)), xs[i] + 40 - ims.size[0] / 2, yy - ims.size[1] / 2, 0.8 * enter)
        paste_col(fr, ic, ims, xs[i] + 40 - ims.size[0] / 2, yy - ims.size[1] / 2, enter)
        if state == "done":
            burst(fr, xs[i] + 40, 470, seg(t, st_end, 0.9), MINT, n=14, R=120)
            if flash < 1:
                ck = ease_out(seg(t, st_end, 0.35))
                cm = Mk(80, 80); cm.line([(18, 42), (34, 58), (62, 24)], 9)
                cmm = cm.get()
                paste_col(fr, MINT, cmm.filter(GB(8)), xs[i] + 40 + 50, 360, 0.8 * ck); paste_col(fr, MINT, cmm, xs[i] + 40 + 50, 360, ck)
    # order token
    tok_x = None
    if t >= ts_[0] - 0.6:
        u = clamp((t - (ts_[0] - 0.6)) / (ts_[-1] + RUN - ts_[0] + 0.6))
        pos = lerp(xs[0] + 40, xs[-1] + 40, ease_io(min(1.0, u * 1.0)))
        # snap: follow the active station
        act = max(i for i in range(n) if t >= ts_[i] - 0.4) if t >= ts_[0] - 0.4 else 0
        tgt = xs[act] + 40
        tok_x = lerp(getattr(scene6, "_tx", tgt), tgt, 1.0)
        tm = V._dot(9)
        paste_col(fr, MINT if t > ts_[-1] + RUN else LAV2, tm.filter(GB(8)), tgt - tm.size[0] / 2, y_track - tm.size[1] / 2, 0.9)
        paste_col(fr, (255, 255, 255), tm, tgt - tm.size[0] / 2, y_track - tm.size[1] / 2, 1.0)
    # completion
    done_t = ts_[-1] + RUN
    tc_ = cue("s6", 2) + 0.2
    pa = ease_out(seg(t, tc_, 0.6))
    tl, mk = pill_tile("Real start and finish times, recorded automatically", 34, MINT, (14, 38, 30), padx=44, h=84)
    place(fr, tl, mk, 960, 930 + (1 - pa) * 30, a=pa, s=0.92 + 0.08 * pa, glow=(MINT, 0.7, 18))
    burst(fr, 960, 930, seg(t, tc_, 1.2), MINT, n=26, R=340)
    foot = ease_out(seg(t, 1.0, 0.8)) * (1 - seg(t, done_t, 0.4))
    text_center(fr, "Example flow. Operations are configured for each unit.", 22, "r", 960, 1040, MUTED_D, 0.75 * ease_out(seg(t, 0.8, 0.8)))
    return fr

def scene7(t, d):
    """Last operation done -> the manufacturing order is marked as done (no timers)."""
    fr = bg_dark(t, 1.0, cx=W * 0.6)
    particles(fr, t, 16, seed=19)
    T_PACK, T_DONE = s7_times()
    kinetic(fr, [words("Last operation done.")], 960, 130, 66, t, cue("s7", 0), t_out=cue("s7", 1) - 0.1, per=0.13)
    kinetic(fr, [[("Order", None), ("marked", None), ("as", None), ("done.", MINT)]], 960, 130, 66, t, cue("s7", 1) + 0.1, per=0.13)
    done = ease_out(seg(t, T_DONE, 0.5))
    pc = ease_out(seg(t, 0.3, 0.9))
    c = Ctx((255, 255, 255), 600, 440)
    E.odoo_chip(c, 112, 56, 0.78)
    c.text(40, 150, "Manufacturing order", 38, INK, "b", anchor="lm")
    c.text(40, 196, "MO 00021", 26, MUTED_L, "r", anchor="lm")
    for k in range(6):
        fin = k < 5 or t >= T_PACK
        col = (47, 203, 138) if fin else (124, 92, 255)
        c.rect(40 + k * 88, 250, 80, 18, r=9, fill=col, a=1.0 if fin else 0.55 + 0.4 * math.sin(t * 6) ** 2)
    c.text(40, 300, "6 operations", 22, MUTED_L, "r", anchor="lm")
    if t < T_DONE:
        c.pill(140, 372, "In progress", (255, 255, 255), (124, 92, 255), size=28, padx=28, h=56)
    else:
        c.pill(118, 372, "Done", (255, 255, 255), (22, 160, 106), size=30, padx=36, h=62)
        E.check(c, 226, 372, 1.4, (47, 203, 138), 7, a=done)
    place(fr, c.out(), rr_mask(600, 440, 30), 600 + (1 - pc) * -120, 560, s=1.0 + 0.04 * math.sin(math.pi * seg(t, T_DONE, 0.6)), yaw=-9, pitch=2, a=pc,
          shadow=0.55, glow=(MINT if t >= T_DONE else (140, 100, 230), 0.25 + 0.55 * done, 16))
    # beam from the finished last operation to the order
    bu = seg(t, T_PACK + 0.15, 1.0)
    if 0 < bu <= 1:
        sweep_dot(fr, 1190, 930, 560, bu, MINT)
    for i, (name, kind) in enumerate(STATIONS):
        a = ease_out(seg(t, 0.6 + i * 0.1, 0.5))
        last = i == len(STATIONS) - 1
        fin = (not last) or t >= T_PACK
        c2 = Ctx((30, 17, 48), 460, 84)
        c2.rect(1, 1, 458, 82, r=22, line=(60, 190, 130) if fin else (150, 110, 240), lw=2)
        c2.ell(46, 42, 18, fill=(47, 203, 138) if fin else VIO)
        if fin: E.check(c2, 46, 42, 0.95, (255, 255, 255), 5)
        else: c2.text(46, 41, str(i + 1), 20, (255, 255, 255), "b")
        c2.text(82, 42, name, 28, CREAM, "s", anchor="lm")
        lab = "Finished" if fin else "In progress"
        c2.pill(366, 42, lab, (190, 255, 220) if fin else (255, 255, 255), (22, 120, 80) if fin else (124, 92, 255), size=18, padx=14, h=34)
        pulse = 0.35 + (0.3 * math.sin(t * 5) if not fin else 0.0)
        if last and fin: pulse = 0.35 + 0.5 * (1 - seg(t, T_PACK, 0.8))
        place(fr, c2.out(), rr_mask(460, 84, 22), 1430 + (1 - a) * 70, 300 + i * 100, a=a, shadow=0.35, blur=(1 - a) * 8, glow=((MINT if fin else VIO), pulse, 12))
    burst(fr, 1660, 300 + 5 * 100, seg(t, T_PACK, 0.9), MINT, n=14, R=110)
    burst(fr, 600, 520, seg(t, T_DONE, 1.3), MINT, n=30, R=380)
    pp = ease_out(seg(t, cue("s7", 2) + 0.2, 0.6))
    tl, mk = pill_tile("Stock updated, no clicks", 32, MINT, (14, 38, 30), icon="spark", padx=44, h=78)
    place(fr, tl, mk, 600, 890 + (1 - pp) * 24, s=0.92 + 0.08 * pp, a=pp, glow=(MINT, 0.65, 16))
    return fr

def scene8(t, d):
    fr = bg_light(t, 1.0)
    particles(fr, t, 18, dark=False, seed=23)
    kinetic(fr, [words("The result")], 960, 140, 74, t, 0.4, per=0.2, dark=False)
    cards = [("No manual entry", "Operations start and finish by themselves"), ("Live view of every order", "See what is running right now"), ("Real operation times", "See idle time and where hours go")]
    starts = [cue("s8", 0) + 0.1, cue("s8", 1) + 0.1, cue("s8", 2) + 0.1]
    for i, (h_, sub) in enumerate(cards):
        p = ease_out(seg(t, starts[i], 0.9))
        c = Ctx((255, 255, 255), 500, 500)
        c.rect(1, 1, 498, 498, r=34, line=(232, 222, 250), lw=2)
        cy = 190
        if i == 0:
            c.rect(175, cy - 74, 150, 150, r=16, line=INK, lw=6)
            for k in range(3): c.line([(205, cy - 34 + k * 36), (295, cy - 34 + k * 36)], (214, 206, 232), 8)
            sl = seg(t, starts[i] + 0.6, 0.6)
            c.line([(150, cy - 94), (150 + 200 * sl, cy - 94 + 190 * sl)], E.RED, 12)
        if i == 1:
            pts = [(110, cy + 6), (190, cy + 6), (225, cy - 74), (270, cy + 76), (305, cy + 6), (370, cy + 6)]
            c.line(pts, INK, 9)
            c.ell(392, cy + 6, 14 + 5 * (0.5 + 0.5 * math.sin(t * 5)), fill=E.GREEN)
        if i == 2:
            grow = ease_out(seg(t, starts[i] + 0.4, 1.0))
            rows = [((E.BLUE, 0.0, 0.34), (AMBER, 0.38, 0.30), ((226, 220, 238), 0.72, 0.28)), ((AMBER, 0.0, 0.2), ((226, 220, 238), 0.24, 0.2), (E.BLUE, 0.48, 0.36)), ((E.BLUE, 0.0, 0.5), ((226, 220, 238), 0.54, 0.46))]
            for r_, row in enumerate(rows):
                for col, a0, w_ in row:
                    c.rect(100 + 300 * a0, cy - 66 + r_ * 56, max(2, (300 * w_ - 6) * grow), 38, r=10, fill=col)
        c.text(250, 358, h_, 33 if len(h_) > 20 else 36, INK, "b")
        c.text(250, 412, sub, 22, MUTED_L, "r")
        place(fr, c.out(), rr_mask(500, 500, 34), 400 + i * 560, 540 + (1 - p) * 80 + math.sin(t * 1.0 + i * 1.3) * 7, a=p, pitch=(1 - p) * -10,
              shadow=0.32, shadow_col=(110, 70, 190), sh_blur=34, sh_dy=30, blur=(1 - p) * 8)
    a = ease_out(seg(t, cue("s8", 2) + 2.6, 0.7))
    tl, mk = pill_tile("Built for Odoo Manufacturing", 32, CREAM, (19, 6, 28), icon="spark", padx=44, h=76)
    place(fr, tl, mk, 960, 905 + (1 - a) * 20, a=a, glow=(VIO, 0.55, 16), shadow=0.3, shadow_col=(90, 50, 170))
    return fr

def scene0(t, d):
    """Intro: background only (violet glow + soft sparkles). Logo and title are added by hand in the editor."""
    fr = bg_dark(t, 0.5 + 0.45 * ease_out(seg(t, 0.3, 2.0)), cx=W * 0.5)
    particles(fr, t, 30, seed=2)
    return fr

def scene9(t, d):
    fr = bg_light(t, 1.25)
    particles(fr, t, 22, dark=False, seed=29)
    for k in range(3):
        u = (t * 0.3 + k / 3) % 1.0
        R = 150 + u * 420
        m = Mk(int(R * 2 + 30), int(R * 2 + 30)); m.ell(R + 15, R + 15, R, fill=0, outline=255, lw=3)
        paste_col(fr, VIO, m.get(), 960 - R - 15, 360 - R - 15, (1 - u) * 0.2 * ease_out(seg(t, 0.4, 0.8)))
    p = back(seg(t, 0.2, 0.8))
    tl, mk = brand_tile(260)
    place(fr, tl, mk, 960, 360, s=p, a=clamp(p * 2), shadow=0.28, shadow_col=(80, 40, 160), sh_blur=34, sh_dy=30, glow=(VIO, 0.35, 20))
    kinetic(fr, [[("SKIT", None), ("AI", None), ("Vision", None)]], 960, 620, 124, t, cue("s9", 0) + 0.05, wt="sl", per=0.16, dark=False)
    a = ease_out(seg(t, cue("s9", 1), 0.7))
    tl, mk2 = pill_tile("Your camera, connected to Odoo.", 36, CREAM, (19, 6, 28), icon="spark", padx=46, h=84)
    place(fr, tl, mk2, 960, 790 + (1 - a) * 26, s=0.9 + 0.1 * a, a=a, glow=(VIO, 0.6 + 0.3 * math.sin(t * 2), 18), shadow=0.35, shadow_col=(90, 50, 170), sh_blur=26, sh_dy=22)
    return fr


SCENES = [scene0, scene1, scene2, scene3, scene4, scene5, scene6, scene7, scene8, scene9]
TRANS = ["zoom", "whip", "zoom", "star", "zoom", "whip", "zoom", "star", "zoom"]

def whip(A, B, p):
    e = ease_io(p)
    sm = math.sin(math.pi * p)
    def smear(im, k):
        if k < 0.02: return im
        w = max(8, int(W * (1 - 0.9 * k)))
        return im.resize((w, H), Image.BILINEAR).resize((W, H), Image.BILINEAR)
    out = Image.new("RGB", (W, H))
    xa = int(-e * W * 0.55); xb = int((1 - e) * W * 0.55)
    Ab, Bb = smear(A, sm), smear(B, sm)
    out.paste(Bb, (xb - 0, 0)) if False else None
    base = Image.blend(Ab, Bb, e)
    out.paste(base, (0, 0))
    out.paste(Ab.crop((int(e * W * 0.55), 0, W, H)), (0, 0), scale_mask(Image.new("L", (W - int(e * W * 0.55), H), 255), 1 - e))
    out.paste(Bb.crop((0, 0, int((e) * W * 0.55) + 1, H)), (W - int(e * W * 0.55), 0), scale_mask(Image.new("L", (int(e * W * 0.55) + 1, H), 255), e))
    return out

_orig_transition = transition
def transition3(kind, A, B, p):
    if kind == "whip": return whip(A, B, p)
    return _orig_transition(kind, A, B, p)

def load_tl(): return json.loads((HERE / "timeline.json").read_text("utf8"))

def frame_at(T, tl):
    starts, durs = tl["starts"], tl["durs"]
    n = len(SCENES); hh = TR / 2
    img = None
    for i in range(n - 1):
        tb = starts[i + 1]
        if tb - hh <= T < tb + hh:
            A = SCENES[i](T - starts[i], durs[i]); B = SCENES[i + 1](max(0.0, T - starts[i + 1]), durs[i + 1])
            img = transition3(TRANS[i], A, B, (T - (tb - hh)) / TR)
            break
    if img is None:
        i = max(k for k in range(n) if starts[k] <= T + 1e-9)
        img = SCENES[i](T - starts[i], durs[i])
    lum = np.asarray(img.crop((40, 28, 330, 70))).mean()
    col = INK if lum > 140 else CREAM
    hide = (starts[4] - 0.5 < T < starts[5] + 0.5) or T > starts[9] - 0.5 or T < starts[2] - 0.3
    if not hide:
        put_text(img, "SKIT AI Vision", 24, "s", 78, 36, col, 0.55)
        bt, bm = brand_tile(40)
        img.paste(bt, (28, 28), scale_mask(bm, 0.9))
    T_end = tl["total"]
    if T < 0.5: img = Image.blend(Image.new("RGB", (W, H), DARK), img, ease_out(T / 0.5))
    if T > T_end - 0.7: img = Image.blend(img, Image.new("RGB", (W, H), LIGHT), seg(T, T_end - 0.7, 0.7))
    return img

_TL = None
def render_frame(n):
    global _TL
    if _TL is None: _TL = load_tl()
    return np.asarray(frame_at(n / FPS, _TL).convert("RGB")).tobytes()


# ---------------------------------------------------------------- audio
GAP = 0.22
def prep():
    items = json.loads((HERE / "narration.json").read_text("utf8"))
    sr = 44100
    starts, durs, plan, cues, sid_start = [], [], [], {}, {}
    T = 0.0
    for it in items:
        if "dur" in it:
            starts.append(round(T, 3)); durs.append(it["dur"]); sid_start[it["id"]] = T; T += it["dur"]; continue
        lens = []
        for k in range(len(it["parts"])):
            w = wave.open(str(HERE / f"{it['id']}_{k}.wav")); lens.append(w.getnframes() / w.getframerate())
        offs, o = [], V.LEAD
        for L_ in lens: offs.append(o); o += L_ + GAP
        d = o - GAP + V.TAIL
        cues[it["id"]] = [round(x, 3) for x in offs]
        cues[it["id"] + "_end"] = [round(x + L_, 3) for x, L_ in zip(offs, lens)]
        starts.append(round(T, 3)); durs.append(round(d, 3)); sid_start[it["id"]] = T
        for k, (off, L_) in enumerate(zip(offs, lens)): plan.append((T + off, L_, it["id"], k, it["parts"][k]["show"]))
        T += d
    total = T
    (HERE / "cues.json").write_text(json.dumps(cues), "utf8")
    global _CUES
    _CUES = cues
    N = int(sr * (total + 0.5))
    voice = np.zeros(N, np.float32)
    for at, L_, sid, k, show in plan:
        w = wave.open(str(HERE / f"{sid}_{k}.wav"))
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
    def add(s0, arr):
        s0 = int(s0 * sr)
        if s0 < 0 or s0 >= N: return
        arr = arr[: N - s0]; sfx[s0:s0 + len(arr)] += arr
    for k in range(len(starts) - 1):
        L = int(sr * 1.1)
        spec = np.fft.rfft(rng.standard_normal(L).astype(np.float32)); fr = np.fft.rfftfreq(L, 1 / sr)
        spec *= np.exp(-((np.log(fr + 1) - np.log(1800)) / 0.9) ** 2)
        wsh = np.fft.irfft(spec, L) * np.sin(np.linspace(0, np.pi, L)) ** 3
        add(starts[k + 1] - 0.55, wsh * 0.12 / (np.abs(wsh).max() + 1e-6))
    def blip(freq, dur=0.22, amp=0.10):
        x = np.arange(int(sr * dur)) / sr
        return (np.sin(2 * np.pi * freq * x) + 0.5 * np.sin(2 * np.pi * freq * 2 * x)) * np.exp(-x * 16) * amp
    s5, s6, s7 = sid_start["s5"], sid_start["s6"], sid_start["s7"]
    clicked, ts6 = s6_times()
    add(s6 + clicked, blip(520, 0.18, 0.12))
    for i, tsn in enumerate(ts6): add(s6 + tsn + RUN, blip(660 + i * 70))
    add(s6 + cue("s6", 2) + 0.2, blip(988, 0.5, 0.12))
    add(s5 + cue("s5", 3) + 1.5, blip(520, 0.18, 0.12))
    tp, td = s7_times()
    add(s7 + tp, blip(760, 0.3, 0.12)); add(s7 + td, blip(988, 0.6, 0.13))
    mix_ = voice + pad + sfx
    mix_ /= max(1.0, np.abs(mix_).max() / 0.95)
    out = wave.open(str(HERE / "audio3.wav"), "wb")
    out.setnchannels(1); out.setsampwidth(2); out.setframerate(sr); out.writeframes((mix_ * 32767).astype(np.int16).tobytes()); out.close()
    (HERE / "timeline.json").write_text(json.dumps({"starts": starts, "durs": durs, "total": round(total, 3)}), "utf8")
    def ts(x):
        h, r = divmod(x, 3600); m, s_ = divmod(r, 60); return "%02d:%02d:%02d,%03d" % (h, m, int(s_), int((s_ % 1) * 1000))
    with open(HERE / "captions.srt", "w", encoding="utf8") as f:
        for i, (at, L_, sid, k, show) in enumerate(plan, 1):
            f.write(f"{i}\n{ts(at)} --> {ts(at + L_)}\n{show}\n\n")
    print("scenes", [round(x, 1) for x in durs], "total", round(total, 1))

def render(outfile):
    tl = load_tl(); frames = int(math.ceil(tl["total"] * FPS))
    cmd = [str(HERE / "ffmpeg.exe"), "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", str(HERE / "audio3.wav"), "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p",
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
            frame_at(float(s), tl).save(HERE / f"v3_{float(s):05.1f}.png")
        print("saved")
    elif cmd == "render": render(sys.argv[2])
