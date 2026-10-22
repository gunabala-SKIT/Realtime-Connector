"""Bright, confident cinematic-corporate score (C major, 104 BPM): marimba-style plucks, warm pad, driving
kick/clap groove for the demo, big riser + boom + sustained major chord for the brand reveal. Built from scratch."""
import numpy as np
import music as M
from music import SR, midi, ramp, lowpass, highpass, bandpass, place

BPM = 104.0
BEAT = 60.0 / BPM
# C - G - Am - F  (bass, chord tones)
BARS = [(36, [60, 64, 67, 72]), (43, [59, 62, 67, 71]), (45, [60, 64, 69, 72]), (41, [60, 65, 69, 72])]

def clap(seed=0):
    rng = np.random.default_rng(seed)
    n = int(0.22 * SR); t = np.arange(n) / SR
    s = bandpass(rng.standard_normal(n), 1100, 4200)
    e = np.exp(-t * 26) + 0.7 * np.exp(-np.maximum(t - 0.012, 0) * 34) * (t > 0.012) + 0.5 * np.exp(-np.maximum(t - 0.024, 0) * 40) * (t > 0.024)
    return s * e

def bass(freq, dur):
    n = int((dur + 0.1) * SR)
    s = M.saw(freq, n, 0, harm=10)
    s = lowpass(s, 420)
    return s * M.env(n, 0.01, 0.08)

def marimba(freq, dur=0.7):
    n = int(dur * SR); t = np.arange(n) / SR
    s = np.sin(2 * np.pi * freq * t) + 0.5 * np.sin(2 * np.pi * freq * 4.0 * t) * np.exp(-t * 18) + 0.2 * np.sin(2 * np.pi * freq * 9.2 * t) * np.exp(-t * 40)
    return s * np.exp(-t * 6.0) * np.minimum(1, t * 700)

def make_music(total, T, extras=None):
    N = int((total + 3.0) * SR)
    t = np.arange(N) / SR
    buf = np.zeros((N, 2))
    bar_len = 4 * BEAT
    nbars = int(total / bar_len) + 3
    inten = 0.45 + 0.55 * ramp(t, 0, T["core"]) + 0.25 * ramp(t, T["benefits"], T["benefits"] + 4)
    pad = np.zeros((N, 2))
    for b in range(nbars):
        bs, chord = BARS[b % 4]
        t0 = b * bar_len
        place(pad, t0, M.pad_note(midi(bs), bar_len, 0.5, 1.0), 0.8, 0.0)
        for j, m in enumerate(chord):
            place(pad, t0, M.pad_note(midi(m), bar_len, 0.7, 1.2), 0.36, -0.5 + 0.33 * j)
    buf += pad * (0.15 * inten)[:, None]
    # sparse marimba hook motif + soft pulse before the idea
    for b in range(nbars):
        t0 = b * bar_len
        if t0 < T["core"] - 0.4:
            ch = BARS[b % 4][1]
            for k, (off, ix) in enumerate(((0, 0), (1.5, 2), (2.5, 3))):
                place(buf, t0 + off * BEAT, marimba(midi(ch[ix] + 12)), 0.07 * (0.55 + 0.45 * ramp(t0, 0, T["idea"])), -0.35 + 0.35 * k)
            if t0 > 1.0 and t0 < T["idea"]:
                place(buf, t0, M.kick(0.4), 0.09, 0.0); place(buf, t0 + 2 * BEAT, M.kick(0.4), 0.07, 0.0)
    # groove: kick on every beat, clap on 2 & 4, hats on 8ths, bass on 8ths, marimba arp on 8ths
    end_drive = T["brand_riser"]
    step = BEAT / 2
    k = 0
    tt = T["core"] - 0.15
    while tt < end_drive:
        bar = int(tt / bar_len)
        bs, chord = BARS[bar % 4]
        g = 0.06 + 0.04 * ramp(tt, T["core"], T["alert"]) + 0.03 * ramp(tt, T["benefits"], T["benefits"] + 3)
        pat = [0, 1, 2, 3, 2, 1, 3, 2]
        m = chord[pat[k % 8]] + (12 if (k // 8) % 2 else 0)
        place(buf, tt, marimba(midi(m + 12)), g, -0.45 if k % 2 else 0.45)
        if k % 2 == 0:
            place(buf, tt, M.kick(0.4), 0.17 + 0.05 * ramp(tt, T["alert"], T["benefits"]), 0.0)
            place(buf, tt, bass(midi(bs + (12 if (k // 2) % 4 == 3 else 0)), step * 1.8), 0.20, 0.0)
        if k % 4 == 2:
            place(buf, tt, clap(k), 0.08 + 0.03 * ramp(tt, T["benefits"], T["benefits"] + 2), 0.0)
        if k % 2 == 1:
            place(buf, tt, M.hat(0.05, k), 0.04, 0.2)
        tt += step; k += 1
    # accents
    place(buf, T["idea"], M.boom(2.2), 0.18, 0.0)
    place(buf, T["core"], M.boom(2.0), 0.17, 0.0)
    place(buf, T["alert"], M.boom(1.6), 0.18, 0.0)
    place(buf, T["alert"], M.ding(midi(96)), 0.07, 0.3)
    place(buf, T["benefits"], M.boom(1.8), 0.15, 0.0)
    place(buf, T["idea"] - 1.5, M.riser(1.5, 3), 0.09, 0.0)
    place(buf, T["core"] - 1.3, M.riser(1.3, 4), 0.08, 0.0)
    place(buf, T["benefits"] - 1.1, M.riser(1.1, 6), 0.07, 0.0)
    rd = T["brand_hit"] - T["brand_riser"]
    place(buf, T["brand_riser"], M.riser(rd, 7), 0.20, 0.0)
    place(buf, T["brand_riser"], M.reverse_cymbal(rd, 8), 0.10, 0.0)
    place(buf, T["brand_hit"], M.boom(4.2), 0.42, 0.0)
    place(buf, T["brand_hit"], clap(7) * 2.0, 0.10, 0.0)
    fin = np.zeros((N, 2))
    for j, m in enumerate([36, 48, 60, 64, 67, 72, 76]):
        place(fin, T["brand_hit"] - 0.1, M.pad_note(midi(m), total - T["brand_hit"] + 1.5, 0.5, 2.5), 0.5 if j else 0.9, -0.6 + 0.2 * j)
    buf += fin * 0.22
    for j, m in enumerate([84, 88, 91, 96, 91, 88, 84, 79]):
        place(buf, T["brand_hit"] + 0.4 + j * BEAT / 2, marimba(midi(m), 0.9), 0.07, -0.5 + 0.14 * j)
    for (ts, kind, g) in (extras or []):
        if kind == "pop": place(buf, ts, marimba(midi(91), 0.3), g, 0.2)
        if kind == "tick": place(buf, ts, M.pluck(midi(100), 0.12), g, -0.2)
        if kind == "whoosh": place(buf, ts, M.riser(0.8, int(ts * 10) % 50) * np.hanning(int(0.8 * SR)), g, 0.0)
        if kind == "ping": place(buf, ts, M.ding(midi(88), 1.2), g, 0.2)
    buf = M.reverb(buf, 2.2, 0.26)
    buf[: int(0.3 * SR)] *= np.linspace(0, 1, int(0.3 * SR))[:, None]
    peak = np.abs(buf).max()
    return (buf / peak * 0.92).astype(np.float32)
