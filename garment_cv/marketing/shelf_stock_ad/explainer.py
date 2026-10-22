"""SKIT AI Vision explainer video: draws every frame with Pillow, mixes the voice-over, encodes with ffmpeg.

    python explainer.py prep            # measure voice clips -> timeline.json, build audio.wav
    python explainer.py still 12.5 30   # save PNG frames at those seconds (for checking the look)
    python explainer.py render out.mp4  # draw all frames (parallel) and encode
"""
import json, math, os, subprocess, sys, wave
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
W, H, SS, FPS = 1920, 1080, 2, 24
TR = 0.45                                   # cross-fade between scenes (s)
LEAD, TAIL = 0.45, 0.55                     # silence before / after each voice clip (s)

def hexc(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

INK, PAPER, CARD = hexc("#17171A"), hexc("#F6F4EF"), hexc("#FFFFFF")
LINE, MUTED = hexc("#E3E0D8"), hexc("#6B6A66")
BLUE, AMBER, GREEN, RED, VIOLET = hexc("#3B6FE0"), hexc("#E49B0F"), hexc("#2E9E6B"), hexc("#D9473F"), hexc("#7A5BD0")
PINK, ODOO = hexc("#E0457B"), hexc("#714B67")
WHITE = (255, 255, 255)

FONTS = {"r": "segoeui.ttf", "s": "seguisb.ttf", "b": "segoeuib.ttf"}
_fc = {}
def font(w, size):
    k = (w, int(size))
    if k not in _fc:
        _fc[k] = ImageFont.truetype("C:/Windows/Fonts/" + FONTS[w], int(size))
    return _fc[k]

def clamp(x, a=0.0, b=1.0): return a if x < a else b if x > b else x
def seg(t, t0, d): return clamp((t - t0) / d) if d > 0 else (1.0 if t >= t0 else 0.0)
def ease_out(p): return 1 - (1 - p) ** 3
def ease_io(p): return p * p * (3 - 2 * p)
def back(p):
    c1 = 1.70158; c3 = c1 + 1
    return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2 if p < 1 else 1.0
def lerp(a, b, p): return a + (b - a) * p
def mix(c1, c2, p): return tuple(int(lerp(c1[i], c2[i], p)) for i in range(3))


class Ctx:
    """Drawing surface in 1920x1080 'logical' units, rendered at SS x and averaged down."""
    def __init__(self, bg, w=W, h=H):
        self.im = Image.new("RGB", (w * SS, h * SS), bg)
        self.d = ImageDraw.Draw(self.im, "RGBA")
        self.px = self.py = 0.0; self.s = 1.0; self.a = 1.0
        self.bg = bg

    # transform: scale s about pivot (px,py), extra alpha a
    def xf(self, px=0, py=0, s=1.0, a=1.0):
        self.px, self.py, self.s, self.a = px, py, s, a
        return self
    def reset(self): return self.xf()
    def X(self, x): return (self.px + (x - self.px) * self.s) * SS
    def Y(self, y): return (self.py + (y - self.py) * self.s) * SS
    def L(self, v): return v * self.s * SS
    def col(self, c, a=1.0):
        al = a * self.a * (c[3] / 255 if len(c) > 3 else 1)
        return (c[0], c[1], c[2], int(255 * clamp(al)))

    def rect(self, x, y, w, h, r=0, fill=None, line=None, lw=2, a=1.0):
        if self.s * self.a * a <= 0.001: return
        box = (self.X(x), self.Y(y), self.X(x + w), self.Y(y + h))
        f = self.col(fill, a) if fill else None
        o = self.col(line, a) if line else None
        if r: self.d.rounded_rectangle(box, radius=self.L(r), fill=f, outline=o, width=max(1, int(self.L(lw))))
        else: self.d.rectangle(box, fill=f, outline=o, width=max(1, int(self.L(lw))))

    def ell(self, cx, cy, rx, ry=None, fill=None, line=None, lw=2, a=1.0):
        ry = rx if ry is None else ry
        if self.s * self.a * a <= 0.001: return
        box = (self.X(cx - rx), self.Y(cy - ry), self.X(cx + rx), self.Y(cy + ry))
        self.d.ellipse(box, fill=self.col(fill, a) if fill else None,
                       outline=self.col(line, a) if line else None, width=max(1, int(self.L(lw))))

    def poly(self, pts, fill=None, line=None, lw=2, a=1.0):
        if self.s * self.a * a <= 0.001: return
        p = [(self.X(x), self.Y(y)) for x, y in pts]
        self.d.polygon(p, fill=self.col(fill, a) if fill else None)
        if line: self.d.line(p + [p[0]], fill=self.col(line, a), width=max(1, int(self.L(lw))), joint="curve")

    def line(self, pts, color, w=4, a=1.0, cap=True):
        if self.s * self.a * a <= 0.001 or len(pts) < 2: return
        p = [(self.X(x), self.Y(y)) for x, y in pts]
        wd = max(1, int(self.L(w)))
        self.d.line(p, fill=self.col(color, a), width=wd, joint="curve")
        if cap:
            r = wd / 2
            for q in (p[0], p[-1]):
                self.d.ellipse((q[0] - r, q[1] - r, q[0] + r, q[1] + r), fill=self.col(color, a))

    def dashed(self, p0, p1, color, w=3, dash=16, gap=12, a=1.0, off=0.0):
        L = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        if L == 0: return
        ux, uy = (p1[0] - p0[0]) / L, (p1[1] - p0[1]) / L
        pos = -off % (dash + gap) - (dash + gap)
        while pos < L:
            a0, a1 = max(pos, 0), min(pos + dash, L)
            if a1 > a0:
                self.line([(p0[0] + ux * a0, p0[1] + uy * a0), (p0[0] + ux * a1, p0[1] + uy * a1)], color, w, a, cap=False)
            pos += dash + gap

    def text(self, x, y, s, size, color=INK, wt="r", anchor="mm", a=1.0):
        if self.s * self.a * a <= 0.001: return
        self.d.text((self.X(x), self.Y(y)), s, font=font(wt, size * self.s * SS), fill=self.col(color, a), anchor=anchor)

    def tw(self, s, size, wt="r"):
        return font(wt, size * SS).getlength(s) / SS

    def pill(self, cx, cy, label, fg, bg_, size=26, padx=22, h=48, a=1.0, wt="s"):
        w = self.tw(label, size, wt) + padx * 2
        self.rect(cx - w / 2, cy - h / 2, w, h, r=h / 2, fill=bg_, a=a)
        self.text(cx, cy - 1, label, size, fg, wt, a=a)

    def out(self):
        return self.im.reduce(SS)


# ------------------------------------------------------------------ shared pieces
def s_path():
    """Points of the 'S' brand mark in a -1..1 box."""
    pts = []
    for k in range(0, 61):                          # upper bowl, right-top -> left -> bottom
        th = math.radians(-35 - (270 - 35) * k / 60)
        pts.append((math.cos(th) * 0.5, -0.5 + math.sin(th) * 0.5))
    for k in range(1, 61):                          # lower bowl
        th = math.radians(-90 + (145 + 90) * k / 60)
        pts.append((math.cos(th) * 0.5, 0.5 + math.sin(th) * 0.5))
    return pts
S_PTS = s_path()

def logo_mark(c, cx, cy, size, p=1.0, a=1.0, fill=INK, fg=WHITE):
    c.rect(cx - size / 2, cy - size / 2, size, size, r=size * 0.23, fill=fill, a=a)
    n = max(1, int(len(S_PTS) * p))
    k = size * 0.42
    r = size * 0.0475
    for x, y in S_PTS[:n]:                                   # round brush: smooth at any thickness
        c.ell(cx + x * k * 1.15, cy + y * k * 1.0, r, fill=fg, a=a)

def camera_icon(c, cx, cy, s=1.0, color=INK, a=1.0):
    c.rect(cx - 52 * s, cy - 30 * s, 104 * s, 66 * s, r=14 * s, fill=color, a=a)
    c.rect(cx - 20 * s, cy - 44 * s, 40 * s, 18 * s, r=6 * s, fill=color, a=a)
    c.ell(cx, cy + 3 * s, 25 * s, fill=WHITE, a=a)
    c.ell(cx, cy + 3 * s, 15 * s, fill=color, a=a)
    c.ell(cx + 5 * s, cy - 2 * s, 5 * s, fill=WHITE, a=a)

def eye_icon(c, cx, cy, s=1.0, color=INK, a=1.0, t=0.0):
    k = 44 * s
    for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):          # scan brackets
        c.line([(cx + dx * k, cy + dy * (k - 24 * s)), (cx + dx * k, cy + dy * k), (cx + dx * (k - 24 * s), cy + dy * k)], color, 7 * s, a)
    c.ell(cx, cy, 20 * s, fill=color, a=a)
    c.ell(cx + 5 * s, cy - 5 * s, 6 * s, fill=WHITE, a=a)
    sweep = (math.sin(t * 3.0) * 0.5 + 0.5)
    c.line([(cx - k + 6 * s, cy - k + 10 * s + sweep * (2 * k - 20 * s)), (cx + k - 6 * s, cy - k + 10 * s + sweep * (2 * k - 20 * s))], BLUE, 4 * s, 0.7 * a)

def zone_icon(c, cx, cy, s=1.0, color=INK, a=1.0, t=0.0):
    pts = [(cx - 50 * s, cy - 30 * s), (cx + 50 * s, cy - 40 * s), (cx + 58 * s, cy + 36 * s), (cx - 56 * s, cy + 40 * s)]
    for i in range(4):
        c.dashed(pts[i], pts[(i + 1) % 4], color, 6 * s, 14 * s, 10 * s, a, off=t * 30)
    for q in pts: c.ell(q[0], q[1], 7 * s, fill=color, a=a)
    c.ell(cx, cy, 11 * s, fill=AMBER, a=a)

def odoo_chip(c, cx, cy, s=1.0, a=1.0):
    w, h = 190 * s, 74 * s
    c.rect(cx - w / 2, cy - h / 2, w, h, r=18 * s, fill=CARD, line=LINE, a=a)
    c.text(cx, cy - 2 * s, "odoo", 44 * s / 1.0, ODOO, "b", a=a)

def check(c, cx, cy, s=1.0, color=GREEN, w=7, a=1.0):
    c.line([(cx - 11 * s, cy), (cx - 3 * s, cy + 9 * s), (cx + 12 * s, cy - 10 * s)], color, w * s, a)

def badge(c, cx, cy, label, kind, a=1.0, size=22):
    fg, bg_ = {"progress": (BLUE, (59, 111, 224, 40)), "done": (GREEN, (46, 158, 107, 40)), "wait": (MUTED, (107, 106, 102, 36)),
               "cut": (AMBER, (228, 155, 15, 50)), "red": (RED, (217, 71, 63, 40)), "idle": (MUTED, (107, 106, 102, 36))}[kind]
    c.pill(cx, cy, label, fg, bg_, size=size, padx=16, h=38, a=a)

def subtitle(c, text, t, d, dark):
    words = text.split(); lines = []; cur = ""
    for w_ in words:
        trial = (cur + " " + w_).strip()
        if c.tw(trial, 38, "s") > 1560 and cur: lines.append(cur); cur = w_
        else: cur = trial
    lines.append(cur)
    a = seg(t, 0.25, 0.3) * (1 - seg(t, d - 0.45, 0.3))
    col = WHITE if dark else INK
    y0 = 985 - (len(lines) - 1) * 26
    for i, ln in enumerate(lines):
        c.text(960, y0 + i * 52, ln, 38, col, "s", a=a * 0.95)


def bg_ctx(dark):
    return Ctx(INK if dark else PAPER)


# ------------------------------------------------------------------ the scenes
OPS = [("Marking", BLUE), ("Cutting", AMBER), ("Stitching", VIOLET), ("Pressing", MUTED), ("Fusing", MUTED), ("Bundling", MUTED),
       ("Hemming", MUTED), ("Trimming", MUTED), ("Buttoning", MUTED), ("Checking", MUTED), ("Folding", MUTED), ("Packing", MUTED),
       ("Embroidery", MUTED), ("Pocket setting", MUTED), ("Collar making", MUTED), ("Sleeve setting", MUTED), ("Finishing", MUTED), ("Dispatch", MUTED)]

def scene1(t, d, tx):
    c = bg_ctx(True)
    p = ease_out(seg(t, 0.15, 0.7))
    c.xf(a=p)
    c.text(960, 190 - (1 - p) * 20, "In a garment unit,", 76, WHITE, "b")
    c.text(960, 285 - (1 - p) * 20, "hundreds of operations happen every day.", 76, (255, 255, 255, 170), "b")
    c.reset()
    for i, (name, col) in enumerate(OPS):
        r, k = divmod(i, 6)
        x, y = 150 + k * 274, 440 + r * 120
        pp = back(seg(t, 0.9 + i * 0.16 + (0 if i > 2 else -0.5), 0.45))
        c.xf(x + 125, y + 36, pp, a=clamp(pp * 2))
        c.rect(x, y, 250, 72, r=36, fill=(255, 255, 255, 22), line=(255, 255, 255, 46), lw=2)
        c.ell(x + 38, y + 36, 9, fill=col if col != MUTED else (255, 255, 255, 90))
        c.text(x + 62, y + 35, name, 26, (255, 255, 255, 225), "s", anchor="lm")
    c.reset()
    subtitle(c, tx, t, d, True)
    return c

def scene2(t, d, tx):
    c = bg_ctx(True)
    p = back(seg(t, 0.2, 0.6))
    c.xf(430, 520, p, a=clamp(p * 2))
    c.rect(230, 170, 600, 700, r=26, fill=hexc("#FAF8F2"))
    c.text(280, 235, "JOB SHEET", 36, INK, "b", anchor="lm")
    c.text(280, 275, "Order 00021", 24, MUTED, "r", anchor="lm")
    ang = t * 5
    c.ell(760, 240, 30, fill=None, line=INK, lw=4)
    c.line([(760, 240), (760 + 18 * math.sin(ang), 240 - 18 * math.cos(ang))], INK, 4)
    c.line([(760, 240), (760 + 11 * math.sin(ang / 12), 240 - 11 * math.cos(ang / 12))], INK, 4)
    rows = [("Marking", 1.8, "tick", "2 h late", RED), ("Cutting", 3.2, "q", "not sure", AMBER), ("Stitching", 4.6, "wrong", "wrong qty", RED), ("Pressing", 99, "none", "never entered", MUTED)]
    for i, (name, ts, kind, tag, tc) in enumerate(rows):
        y = 360 + i * 120
        c.text(330, y, name, 34, INK, "s", anchor="lm")
        c.line([(330, y + 38), (780, y + 38)], LINE, 3, cap=False)
        c.rect(268, y - 20, 40, 40, r=8, line=MUTED, lw=3)
        pp = seg(t, ts, 0.35)
        if pp > 0 and kind == "tick": check(c, 288, y, 1.2, GREEN, 7, a=pp)
        if pp > 0 and kind == "q": c.text(288, y, "?", 34, AMBER, "b", a=pp)
        if pp > 0 and kind == "wrong": c.line([(276, y - 12), (300, y + 12)], RED, 6, pp); c.line([(300, y - 12), (276, y + 12)], RED, 6, pp)
        if pp > 0: c.pill(660, y - 2, tag, tc, (tc[0], tc[1], tc[2], 38), size=22, padx=14, h=38, a=pp)
        if kind == "none":
            c.pill(660, y - 2, tag, tc, (107, 106, 102, 40), size=22, padx=14, h=38, a=seg(t, 6.0, 0.4))
    c.reset()
    lines = [("Entered late.", WHITE, 1.4), ("Entered wrong.", WHITE, 3.3), ("Or not at all.", RED, 5.3)]
    for i, (s_, col, ts) in enumerate(lines):
        pp = ease_out(seg(t, ts, 0.5))
        c.xf(a=pp)
        c.text(1000, 360 + i * 130 + (1 - pp) * 24, s_, 88, col if col != RED else (240, 96, 88), "b", anchor="lm")
    c.reset()
    subtitle(c, tx, t, d, True)
    return c

def wo_row(c, x, y, w, name, state_label, kind, bar=None):
    c.rect(x, y, w, 84, r=16, fill=(255, 255, 255, 14) if bar == "dark" else PAPER, line=(255, 255, 255, 30) if bar == "dark" else LINE, lw=2)

def scene3(t, d, tx):
    c = bg_ctx(True)
    for k, (x, title, rows) in enumerate([(170, "What Odoo shows", [("Marking", "In progress", "progress"), ("Cutting", "Waiting", "wait")]),
                                          (1090, "What is really happening", [("Marking", "Finished", "done"), ("Cutting", "Running", "cut")])]):
        p = back(seg(t, 0.2 + k * 0.5, 0.6))
        c.xf(x + 330, 400, p, a=clamp(p * 2))
        c.rect(x, 170, 660, 420, r=26, fill=CARD)
        c.text(x + 40, 235, title, 36, INK, "b", anchor="lm")
        for i, (n, st, kd) in enumerate(rows):
            y = 295 + i * 130
            c.rect(x + 40, y, 580, 100, r=18, fill=PAPER, line=LINE, lw=2)
            c.text(x + 72, y + 50, n, 34, INK, "s", anchor="lm")
            badge(c, x + 520, y + 50, st, kd, size=24)
    c.reset()
    pp = back(seg(t, 1.9, 0.5))
    pulse = 1 + 0.06 * math.sin(t * 6)
    c.xf(960, 380, pp * pulse, a=clamp(pp * 2))
    c.ell(960, 380, 62, fill=RED)
    c.text(960, 376, "≠", 84, WHITE, "b")
    c.reset()
    c.dashed((830, 380), (898, 380), (240, 96, 88), 5, 12, 8, a=seg(t, 2.2, 0.3), off=t * 40)
    c.dashed((1022, 380), (1090, 380), (240, 96, 88), 5, 12, 8, a=seg(t, 2.2, 0.3), off=t * 40)
    pains = ["No live status", "Hidden idle time", "Odoo out of date"]
    for i, s_ in enumerate(pains):
        pp = back(seg(t, 4.2 + i * 1.5, 0.5))
        cx = 480 + i * 480
        c.xf(cx, 760, pp, a=clamp(pp * 2))
        w_ = 400
        c.rect(cx - w_ / 2, 722, w_, 76, r=38, fill=(255, 255, 255, 20), line=(255, 255, 255, 50), lw=2)
        c.ell(cx - w_ / 2 + 40, 760, 10, fill=(240, 96, 88))
        c.text(cx - w_ / 2 + 66, 759, s_, 32, WHITE, "s", anchor="lm")
    c.reset()
    subtitle(c, tx, t, d, True)
    return c

def scene4(t, d, tx):
    c = bg_ctx(False)
    pm = ease_io(seg(t, 0.3, 1.1))
    c.xf(960, 300, back(seg(t, 0.15, 0.5)))
    logo_mark(c, 960, 300, 230, p=pm)
    c.reset()
    pn = ease_out(seg(t, 1.5, 0.7))
    c.xf(a=pn)
    c.text(960, 500 + (1 - pn) * 26, "SKIT AI Vision", 124, INK, "b")
    c.reset()
    pr = seg(t, 3.3, 0.9)
    if pr > 0:
        c.xf(960, 710, back(seg(t, 3.3, 0.5)), a=clamp(pr * 2))
        camera_icon(c, 500, 720, 1.3)
        odoo_chip(c, 1420, 720, 1.3)
        c.xf(a=pr)
        c.line([(600, 720), (1290, 720)], LINE, 6, cap=False)
        for k in range(5):
            u = ((t * 0.45 + k / 5) % 1.0)
            c.ell(lerp(600, 1290, u), 720, 10, fill=BLUE, a=math.sin(u * math.pi))
        c.text(960, 780, "existing camera  →  Odoo Manufacturing", 30, MUTED, "r", a=seg(t, 4.0, 0.6))
        c.reset()
    subtitle(c, tx, t, d, False)
    return c

def scene5(t, d, tx):
    c = bg_ctx(False)
    c.xf(a=ease_out(seg(t, 0.1, 0.5)))
    c.text(960, 150, "How it works", 78, INK, "b")
    c.reset()
    xs = [330, 770, 1210, 1650 - 0]
    xs = [300, 740, 1180, 1620]
    labels = [("1", "Camera watches", "Your existing camera"), ("2", "AI recognises", "Worker · fabric · tools"),
              ("3", "Rules decide", "Zones and timing"), ("4", "Odoo updates", "Work orders start and finish")]
    starts = [0.7, 3.2, 6.0, 7.6]
    for i, (num, lab, sub) in enumerate(labels):
        p = back(seg(t, starts[i], 0.55))
        cx = xs[i]
        c.xf(cx, 450, p, a=clamp(p * 2))
        c.rect(cx - 170, 270, 340, 380, r=28, fill=CARD, line=LINE, lw=2)
        c.ell(cx - 128, 312, 24, fill=INK)
        c.text(cx - 128, 311, num, 26, WHITE, "b")
        cy = 440
        if i == 0: camera_icon(c, cx, cy, 1.5)
        if i == 1: eye_icon(c, cx, cy, 1.5, t=t)
        if i == 2: zone_icon(c, cx, cy, 1.5, t=t)
        if i == 3: odoo_chip(c, cx, cy, 1.25)
        c.text(cx, 560, lab, 36, INK, "b")
        c.text(cx, 604, sub, 24, MUTED, "r")
    c.reset()
    for i in range(3):
        pa = seg(t, starts[i + 1] - 0.2, 0.5)
        x0, x1 = xs[i] + 185, xs[i + 1] - 185
        c.line([(x0, 460), (lerp(x0, x1, pa), 460)], MUTED, 5, cap=False)
        if pa >= 1:
            c.poly([(x1 - 4, 460), (x1 - 22, 448), (x1 - 22, 472)], fill=MUTED)
            for k in range(2):
                u = (t * 0.7 + k * 0.5 + i * 0.17) % 1.0
                c.ell(lerp(x0, x1, u), 460, 8, fill=BLUE, a=math.sin(u * math.pi))
    subtitle(c, tx, t, d, False)
    return c


# camera view -------------------------------------------------------------
FX, FY, FW, FH = 110, 130, 1080, 700

def cam_view(c, st):
    """st: dict(worker_x, worker_a, mode mark|cut|idle|away, mark_p, cut_p, t, boxes, tag, zone, hand_t)"""
    t = st["t"]
    im_before = c.im
    # wall + floor
    c.rect(FX, FY, FW, FH, fill=hexc("#E4DFD2"))
    c.rect(FX, FY + 470, FW, FH - 470, fill=hexc("#CFC9BA"))
    c.rect(FX + 60, FY + 70, 190, 150, r=8, fill=hexc("#F2EFE6"), line=hexc("#C9C3B3"), lw=4)           # window
    c.line([(FX + 155, FY + 70), (FX + 155, FY + 220)], hexc("#C9C3B3"), 4, cap=False)
    for k in range(3):                                                                                # background machines
        mx = FX + 640 + k * 150
        c.rect(mx, FY + 270, 110, 60, r=6, fill=hexc("#8E9AA8"))
        c.rect(mx + 20, FY + 215, 18, 60, r=4, fill=hexc("#6F7B89"))
        c.rect(mx - 6, FY + 330, 122, 12, r=4, fill=hexc("#B9B3A3"))
    for k in range(3):                                                                                # fabric rolls
        c.ell(FX + 820 + k * 55, FY + 190, 24, fill=hexc("#F3D6D2" if k % 2 == 0 else "#D6E6F3"))

    wa = st["worker_a"]; wx = st["worker_x"]
    # worker: torso + head
    if wa > 0.01:
        c.xf(a=wa)
        sh = hexc("#6BB39A")
        c.poly([(wx - 105, FY + 350), (wx - 60, FY + 290), (wx + 60, FY + 290), (wx + 105, FY + 350), (wx + 120, FY + 520), (wx - 120, FY + 520)], fill=sh)
        c.rect(wx - 18, FY + 262, 36, 40, fill=hexc("#C48E6C"))
        c.ell(wx, FY + 232, 50, fill=hexc("#C99673"))
        c.ell(wx, FY + 214, 54, 46, fill=hexc("#2F2D3A"))
        c.rect(wx - 52, FY + 215, 104, 30, fill=hexc("#C99673"))
        c.ell(wx, FY + 242, 36, 30, fill=hexc("#C99673"))
        c.line([(wx - 58, FY + 262), (wx - 20, FY + 300)], hexc("#EFE6D4"), 16, cap=True)        # dupatta
        c.line([(wx + 58, FY + 262), (wx + 20, FY + 300)], hexc("#EFE6D4"), 16, cap=True)
        c.reset()
    # table
    c.poly([(FX + 130, FY + 410), (FX + 900, FY + 410), (FX + 1020, FY + 700), (FX + 20, FY + 700)], fill=hexc("#3F7BD0"))
    c.poly([(FX + 130, FY + 410), (FX + 900, FY + 410), (FX + 910, FY + 428), (FX + 120, FY + 428)], fill=hexc("#5C93E0"))
    # cloth
    cl = [(FX + 310, FY + 455), (FX + 740, FY + 455), (FX + 805, FY + 595), (FX + 245, FY + 595)]
    cut_p = st["cut_p"]; sep = ease_out(seg(cut_p, 0.96, 0.04)) if cut_p >= 0.96 else 0
    cut_y = FY + 525
    c.poly([cl[0], cl[1], (FX + 774, cut_y), (FX + 278, cut_y)], fill=hexc("#F8F5EE"), line=hexc("#D8D2C2"), lw=2)
    c.poly([(FX + 278, cut_y + sep * 16), (FX + 774, cut_y + sep * 16), (cl[2][0], cl[2][1] + sep * 16), (cl[3][0], cl[3][1] + sep * 16)], fill=hexc("#F8F5EE"), line=hexc("#D8D2C2"), lw=2)
    for ix in range(6):
        for iy in range(3):
            fx_ = FX + 340 + ix * 70 + iy * 12; fy_ = FY + 475 + iy * 38 + (sep * 16 if iy == 2 else 0)
            c.ell(fx_, fy_, 7, fill=hexc("#E8A5B5"))
    # marking lines
    segs = [((FX + 340, FY + 478), (FX + 700, FY + 478)), ((FX + 320, FY + 556), (FX + 720, FY + 556)), ((FX + 380, FY + 465), (FX + 330, FY + 585))]
    mp = st["mark_p"]; tip = None
    for i, (a0, a1) in enumerate(segs):
        sp = clamp(mp * 3 - i)
        if sp > 0:
            b1 = (lerp(a0[0], a1[0], sp), lerp(a0[1], a1[1], sp))
            c.line([a0, b1], PINK, 6)
            if 0 < sp < 1 or (i == 2 and sp >= 1 and mp < 1): tip = b1
    if mp > 0 and tip is None: tip = segs[2][1]
    # cut line
    if cut_p > 0:
        cx0, cx1 = FX + 278, FX + 774
        cp = clamp(cut_p / 0.96)
        c.line([(cx0, cut_y), (lerp(cx0, cx1, cp), cut_y)], hexc("#2B2B33"), 4)

    mode = st["mode"]
    # arms and hands
    if wa > 0.01:
        c.xf(a=wa)
        sh = hexc("#6BB39A"); skin = hexc("#C99673")
        lh = (FX + 300, FY + 500)
        if mode == "mark" and tip: rh = (tip[0] + 38, tip[1] - 46)
        elif mode == "cut":
            cxp = lerp(FX + 278, FX + 774, clamp(cut_p / 0.96)); rh = (cxp + 58, cut_y - 40)
        elif mode == "idle": rh = (wx + 150, FY + 470); lh = (wx - 150, FY + 470)
        else: rh = (wx + 120, FY + 440); lh = (wx - 120, FY + 440)
        if mode in ("idle", "away"): pass
        c.line([(wx - 85, FY + 335), lh], sh, 40)
        c.line([(wx + 85, FY + 335), rh], sh, 40)
        c.ell(lh[0], lh[1], 22, fill=skin); c.ell(rh[0], rh[1], 22, fill=skin)
        # tools
        if mode == "mark" and tip:
            c.line([tip, (tip[0] + 56, tip[1] - 84)], PINK, 12)
            c.line([tip, (tip[0] + 8, tip[1] - 12)], hexc("#7A2342"), 12)
        if mode == "cut":
            op = 10 + 9 * math.sin(st["hand_t"] * 9)
            bx, by = rh[0] - 52, rh[1] + 36
            c.line([(rh[0], rh[1]), (bx - 6, by - op * 0.2)], hexc("#3A3A44"), 7)
            c.line([(rh[0], rh[1]), (bx, by + op * 0.9)], hexc("#7C7C86"), 7)
            c.ell(rh[0] + 18, rh[1] - 18, 15, line=hexc("#D9473F"), lw=6)
            c.ell(rh[0] + 36, rh[1] + 2, 15, line=hexc("#D9473F"), lw=6)
        c.reset()
    # zone
    zs = st.get("zone", 0)
    if zs > 0:
        zp = [(FX + 100, FY + 395), (FX + 940, FY + 395), (FX + 1040, FY + 690), (FX + 20, FY + 690)]
        for i in range(4):
            c.dashed(zp[i], zp[(i + 1) % 4], WHITE, 5, 22, 14, a=zs, off=t * 24)
        c.rect(FX + 118, FY + 358, 150, 38, r=8, fill=(23, 23, 26, 190), a=zs)
        c.text(FX + 193, FY + 377, "Cutting zone", 22, WHITE, "s", a=zs)
    # detection boxes
    bs = st["boxes"]
    def box(x0, y0, x1, y1, col, label, a, txt=WHITE):
        c.rect(x0, y0, x1 - x0, y1 - y0, r=8, line=col, lw=4, a=a)
        w_ = c.tw(label, 21, "s") + 24
        c.rect(x0, y0 - 34, w_, 34, r=6, fill=col, a=a)
        c.text(x0 + 12, y0 - 17, label, 21, txt, "s", anchor="lm", a=a)
    if bs > 0 and wa > 0.2:
        box(wx - 135, FY + 160, wx + 135, FY + 535, GREEN, "Person", bs * wa)
    if bs > 0:
        box(FX + 230, FY + 440, FX + 830, FY + 612, WHITE, "Cloth", bs, txt=INK)
        if mode == "mark" and tip:
            box(tip[0] - 14, tip[1] - 98, tip[0] + 74, tip[1] + 18, PINK, "Pen", bs * st.get("tool_a", 1))
        if mode == "cut":
            cxp = lerp(FX + 278, FX + 774, clamp(cut_p / 0.96))
            box(cxp - 6, cut_y - 90, cxp + 104, cut_y + 36, AMBER, "Scissors", bs * st.get("tool_a", 1), txt=INK)
    # activity tag
    tg = st.get("tag")
    if tg and wa > 0.2:
        lab, col = tg
        c.pill(wx, FY + 118, lab, WHITE, col, size=24, padx=20, h=44, a=wa)
    # HUD
    c.rect(FX + 24, FY + 22, 190, 42, r=21, fill=(23, 23, 26, 180))
    c.ell(FX + 52, FY + 43, 8, fill=hexc("#FF4D4D") if int(t * 2) % 2 == 0 else hexc("#7A2020"))
    c.text(FX + 70, FY + 43, "CAM 01 · LIVE", 20, WHITE, "s", anchor="lm")
    # round the corners: repaint outside a rounded rect with the page colour
    mask = Image.new("L", (FW * SS, FH * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, FW * SS - 1, FH * SS - 1), radius=34 * SS, fill=255)
    box_ = (FX * SS, FY * SS, (FX + FW) * SS, (FY + FH) * SS)
    crop = c.im.crop(box_)
    bgim = Image.new("RGB", crop.size, c.bg)
    c.im.paste(Image.composite(crop, bgim, mask), box_[:2])
    c.d = ImageDraw.Draw(c.im, "RGBA")
    c.rect(FX, FY, FW, FH, r=34, line=hexc("#2B2B33"), lw=6)

def scene6(t, d, tx):
    c = bg_ctx(False)
    u = t / d
    st = dict(t=t, worker_x=FX + 560, worker_a=ease_out(seg(t, 0.2, 0.5)), mode="mark", mark_p=0.0, cut_p=0.0, boxes=0.0,
              zone=ease_out(seg(t, 0.4, 0.6)), hand_t=t, tag=None, tool_a=1.0)
    st["boxes"] = ease_out(seg(t, 1.3, 0.5))
    mark_p = seg(u, 0.20, 0.34)
    st["mark_p"] = mark_p
    cutting = u > 0.60
    st["mode"] = "cut" if cutting else "mark"
    st["cut_p"] = seg(u, 0.62, 0.30) * 0.96 + (seg(u, 0.93, 0.05) * 0.04)
    if st["mode"] == "mark":
        st["mark_p"] = mark_p if u > 0.2 else 0.0
        st["tag"] = ("Marking", BLUE) if u > 0.2 else None
        st["tool_a"] = 1.0
    else:
        st["mark_p"] = 1.0
        st["tag"] = ("Cutting", AMBER)
        st["tool_a"] = ease_out(seg(u, 0.60, 0.04))
    cam_view(c, st)
    # Odoo panel
    px, py, pw = 1260, 130, 560
    pp = back(seg(t, 0.6, 0.6))
    c.xf(px + pw / 2, py + 300, pp, a=clamp(pp * 2))
    c.rect(px, py, pw, 700, r=30, fill=CARD, line=LINE, lw=2)
    odoo_chip(c, px + 112, py + 60, 0.78)
    c.text(px + 40, py + 140, "Work orders", 40, INK, "b", anchor="lm")
    c.text(px + 40, py + 184, "Manufacturing order 00021", 24, MUTED, "r", anchor="lm")
    m_state = "wait" if u < 0.20 else ("progress" if u < 0.62 else "done")
    c_state = "wait" if u < 0.62 else "cut"
    for i, (n, stt, col) in enumerate([("Marking", m_state, BLUE), ("Cutting", c_state, AMBER)]):
        y = py + 240 + i * 160
        active = (stt in ("progress", "cut"))
        c.rect(px + 30, y, pw - 60, 128, r=22, fill=PAPER, line=col if active else LINE, lw=4 if active else 2)
        c.ell(px + 76, y + 64, 26, fill=GREEN if stt == "done" else (col if stt != "wait" else (200, 198, 190)))
        if stt == "done": check(c, px + 76, y + 64, 1.3, WHITE, 6)
        else: c.text(px + 76, y + 63, str(i + 1), 26, WHITE, "b")
        c.text(px + 124, y + 52, n, 36, INK, "s", anchor="lm")
        lab = {"wait": "Waiting", "progress": "In progress", "done": "Finished", "cut": "In progress"}[stt]
        kd = {"wait": "wait", "progress": "progress", "done": "done", "cut": "cut"}[stt]
        badge(c, px + 124 + c.tw(lab, 22, "s") / 2 + 16, y + 92, lab, kd)
    c.text(px + 40, py + 640, "Updated by the camera. No typing.", 24, MUTED, "r", anchor="lm", a=seg(u, 0.30, 0.1))
    c.reset()
    subtitle(c, tx, t, d, False)
    return c

def scene7(t, d, tx):
    c = bg_ctx(False)
    u = t / d
    away = seg(u, 0.10, 0.28)
    wa = 1 - seg(u, 0.30, 0.12)
    st = dict(t=t, worker_x=FX + 560 + ease_io(away) * 380, worker_a=wa, mode="idle" if u < 0.10 else "away", mark_p=1.0, cut_p=1.0,
              boxes=ease_out(seg(u, 0.0, 0.1)) * (1 - seg(u, 0.30, 0.1)), zone=1.0, hand_t=t, tag=("Idle", MUTED) if u > 0.08 else ("Cutting", AMBER))
    cam_view(c, st)
    # countdown ring
    cx, cy, R = 1530, 380, 170
    pp = back(seg(t, 0.2, 0.6))
    c.xf(cx, cy, pp, a=clamp(pp * 2))
    prog = seg(u, 0.28, 0.5)                       # 0 -> 1 over the countdown
    done = seg(u, 0.80, 0.08)
    ring_col = mix(AMBER, GREEN, done)
    c.ell(cx, cy, R, line=LINE, lw=18)
    n = int(80 * (1 - prog))
    pts = [(cx + R * math.cos(math.radians(-90 + 360 * (k / 80) * 1.0)), cy + R * math.sin(math.radians(-90 + 360 * (k / 80)))) for k in range(0, n + 1)]
    if done > 0: pts = [(cx + R * math.cos(math.radians(-90 + 360 * k / 80)), cy + R * math.sin(math.radians(-90 + 360 * k / 80))) for k in range(0, 81)]
    if len(pts) > 1: c.line(pts, ring_col, 18)
    if done < 1:
        secs = max(0, math.ceil(15 * (1 - prog))) if u >= 0.28 else 15
        c.text(cx, cy - 6, str(secs), 150, INK, "b", a=1 - done)
        c.text(cx, cy + 92, "seconds idle", 26, MUTED, "r", a=1 - done)
    else:
        check(c, cx, cy, 5.5, GREEN, 9, a=done)
    c.reset()
    c.text(cx, 640, "Cutting", 40, INK, "s", a=seg(u, 0.2, 0.1))
    lab, kd = ("In progress", "cut") if u < 0.8 else ("Completed", "done")
    badge(c, cx, 690, lab, kd, size=26)
    pp = back(seg(u, 0.88, 0.1))
    c.xf(cx, 780, pp, a=clamp(pp * 2))
    c.pill(cx, 780, "Manufacturing order: Done", WHITE, GREEN, size=30, padx=30, h=62)
    c.reset()
    subtitle(c, tx, t, d, False)
    return c

def scene8(t, d, tx):
    c = bg_ctx(False)
    c.xf(a=ease_out(seg(t, 0.1, 0.5)))
    c.text(960, 140, "What you get", 78, INK, "b")
    c.reset()
    cards = [("No manual entry", "Operations start and finish by themselves"), ("Live production status", "See what is running right now"), ("Clear time picture", "Know where the hours really go")]
    starts = [0.7, 2.9, 5.2]
    for i, (h_, sub) in enumerate(cards):
        x = 150 + i * 560
        p = back(seg(t, starts[i], 0.55))
        c.xf(x + 250, 480, p, a=clamp(p * 2))
        c.rect(x, 250, 500, 480, r=30, fill=CARD, line=LINE, lw=2)
        cy = 400
        if i == 0:
            c.rect(x + 175, cy - 70, 150, 150, r=16, line=INK, lw=6)
            for k in range(3): c.line([(x + 205, cy - 30 + k * 36), (x + 295, cy - 30 + k * 36)], LINE, 8, cap=True)
            c.line([(x + 150, cy - 90), (x + 350, cy + 100)], RED, 12)
        if i == 1:
            pts = [(x + 110, cy + 10), (x + 190, cy + 10), (x + 225, cy - 70), (x + 270, cy + 80), (x + 305, cy + 10), (x + 390, cy + 10)]
            c.line(pts, INK, 9)
            pulse = 0.5 + 0.5 * math.sin(t * 5)
            c.ell(x + 390, cy + 10, 14 + 5 * pulse, fill=GREEN)
        if i == 2:
            rows = [((BLUE, 0.0, 0.34), (AMBER, 0.38, 0.30), (LINE, 0.72, 0.28)), ((AMBER, 0.0, 0.2), (LINE, 0.24, 0.2), (BLUE, 0.48, 0.36)), ((BLUE, 0.0, 0.5), (LINE, 0.54, 0.46))]
            for r_, row in enumerate(rows):
                for col, a0, w_ in row:
                    c.rect(x + 100 + 300 * a0, cy - 62 + r_ * 56, 300 * w_ - 6, 38, r=10, fill=col)
        c.text(x + 250, 580, h_, 38, INK, "b")
        c.text(x + 250, 632, sub, 24, MUTED, "r")
    c.reset()
    subtitle(c, tx, t, d, False)
    return c

def scene9(t, d, tx):
    c = bg_ctx(True)
    for k in range(3):                                                  # soft pulse rings
        u = ((t * 0.35) + k / 3) % 1.0
        c.ell(960, 380, 140 + u * 420, line=(255, 255, 255, 40), lw=3, a=(1 - u) * ease_out(seg(t, 0.4, 0.6)))
    c.xf(960, 380, back(seg(t, 0.2, 0.55)))
    logo_mark(c, 960, 380, 210, p=1.0, fill=WHITE, fg=INK)
    c.reset()
    pn = ease_out(seg(t, 0.9, 0.6))
    c.xf(a=pn)
    c.text(960, 590 + (1 - pn) * 24, "SKIT AI Vision", 116, WHITE, "b")
    c.text(960, 700 + (1 - pn) * 24, "Your camera, connected to Odoo.", 46, (255, 255, 255, 190), "r")
    c.reset()
    subtitle(c, "", t, d, True)
    return c


SCENES = [scene1, scene2, scene3, scene4, scene5, scene6, scene7, scene8, scene9]
DARK = [True, True, True, False, False, False, False, False, True]

def load_timeline():
    return json.loads((HERE / "timeline.json").read_text("utf8"))

def frame_at(T, tl):
    starts, durs, texts = tl["starts"], tl["durs"], tl["texts"]
    i = max(k for k in range(len(starts)) if starts[k] <= T + 1e-9)
    t = T - starts[i]; d = durs[i]
    img = SCENES[i](t, d, texts[i]).out()
    left = d - t
    if i + 1 < len(SCENES) and left < TR:
        nxt = SCENES[i + 1](0.0, durs[i + 1], texts[i + 1]).out()
        img = Image.blend(img, nxt, ease_io(1 - left / TR))
    return img

def render_frame(n):
    tl = load_timeline()
    return np.asarray(frame_at(n / FPS, tl).convert("RGB")).tobytes()


def prep():
    items = json.loads((HERE / "narration.json").read_text("utf8"))
    starts, durs, texts, clips = [], [], [], []
    T = 0.0
    for it in items:
        w = wave.open(str(HERE / (it["id"] + ".wav")))
        n = w.getnframes() / w.getframerate()
        d = LEAD + n + TAIL
        starts.append(round(T, 3)); durs.append(round(d, 3)); texts.append(it["show"] if it["id"] != "s9" else "")
        clips.append((T + LEAD, it["id"]))
        T += d
    total = T
    sr = 44100
    track = np.zeros(int(sr * (total + 0.5)), dtype=np.int16)
    for at, cid in clips:
        w = wave.open(str(HERE / (cid + ".wav")))
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        s0 = int(at * sr)
        track[s0:s0 + len(raw)] = raw
    out = wave.open(str(HERE / "audio.wav"), "wb")
    out.setnchannels(1); out.setsampwidth(2); out.setframerate(sr); out.writeframes(track.tobytes()); out.close()
    (HERE / "timeline.json").write_text(json.dumps({"starts": starts, "durs": durs, "texts": texts, "total": round(total, 3)}), "utf8")
    print("scenes", [round(x, 1) for x in durs], "total", round(total, 1))


def render(outfile):
    tl = load_timeline()
    frames = int(math.ceil(tl["total"] * FPS))
    ff = HERE / "ffmpeg.exe"
    cmd = [str(ff), "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", str(HERE / "audio.wav"), "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", outfile]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    with Pool(6) as pool:
        for k, buf in enumerate(pool.imap(render_frame, range(frames), chunksize=4)):
            p.stdin.write(buf)
            if k % 120 == 0: print(f"frame {k}/{frames}", flush=True)
    p.stdin.close(); p.wait()
    print("done", outfile)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "prep": prep()
    elif cmd == "still":
        tl = load_timeline()
        for s in sys.argv[2:]:
            frame_at(float(s), tl).save(HERE / f"still_{float(s):05.1f}.png")
        print("saved")
    elif cmd == "render": render(sys.argv[2])
