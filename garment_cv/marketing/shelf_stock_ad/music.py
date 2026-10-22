"""A cinematic score built from scratch with numpy/scipy (no samples, no licensed audio).

Structure follows the video: sparse and tense -> idea hit -> driving pulse for the core demo -> bright lift for the
benefits -> big riser, boom and a sustained major chord for the brand reveal.
"""
import numpy as np
from scipy import signal

SR = 44100
BPM = 84.0
BEAT = 60.0 / BPM

def midi(n): return 440.0 * 2 ** ((n - 69) / 12.0)

# Am - F - C - G, one chord per bar of four beats
BARS = [(45, [57, 60, 64, 69]), (41, [53, 57, 60, 65]), (48, [55, 60, 64, 67]), (43, [55, 59, 62, 67])]

def ramp(t, a, b):
    return np.clip((t - a) / max(b - a, 1e-6), 0.0, 1.0)

def smooth(x, hz=6.0):
    sos = signal.butter(2, hz, "low", fs=SR, output="sos")
    return signal.sosfiltfilt(sos, x)

def lowpass(x, fc, order=4):
    return signal.sosfilt(signal.butter(order, fc, "low", fs=SR, output="sos"), x)

def highpass(x, fc, order=2):
    return signal.sosfilt(signal.butter(order, fc, "high", fs=SR, output="sos"), x)

def bandpass(x, lo, hi, order=2):
    return signal.sosfilt(signal.butter(order, [lo, hi], "band", fs=SR, output="sos"), x)

def place(buf, start_s, sig, gain=1.0, pan=0.0):
    """Mix a mono signal into stereo buf at start_s (pan -1..1)."""
    s0 = int(start_s * SR)
    if s0 >= len(buf) or s0 + len(sig) <= 0: return
    a = max(0, -s0); s0 = max(0, s0)
    sig = sig[a:a + len(buf) - s0]
    gl, gr = np.sqrt(0.5 * (1 - pan)) * 1.0, np.sqrt(0.5 * (1 + pan)) * 1.0
    buf[s0:s0 + len(sig), 0] += sig * gain * gl
    buf[s0:s0 + len(sig), 1] += sig * gain * gr

def saw(f, n, detune_cents=0.0, harm=14, tilt=0.55):
    t = np.arange(n) / SR
    f = f * 2 ** (detune_cents / 1200.0)
    out = np.zeros(n)
    for k in range(1, harm + 1):
        if f * k > 9000: break
        out += np.sin(2 * np.pi * f * k * t + k * 0.7) / (k ** (1.0 + tilt * 0.3)) * np.exp(-k * 0.09)
    return out

def env(n, a, r, hold=1.0):
    e = np.ones(n)
    na, nr = int(a * SR), int(r * SR)
    if na > 0: e[:min(na, n)] = np.linspace(0, 1, min(na, n)) ** 1.5
    if nr > 0: e[max(0, n - nr):] *= np.linspace(1, 0, min(nr, n)) ** 1.5
    return e * hold

def pad_note(freq, dur, a=0.9, r=1.4):
    n = int((dur + r) * SR)
    s = (saw(freq, n, -8) + saw(freq, n, 0) + saw(freq, n, 9)) / 3.0
    return lowpass(s, 2400) * env(n, a, r)

def pluck(freq, dur=0.5):
    n = int(dur * SR); t = np.arange(n) / SR
    s = np.sin(2 * np.pi * freq * t) + 0.45 * np.sin(2 * np.pi * freq * 2 * t) * np.exp(-t * 9) + 0.25 * np.sin(2 * np.pi * freq * 3 * t) * np.exp(-t * 14)
    return s * np.exp(-t * 7.5) * np.minimum(1, t * 600)

def bell(freq, dur=2.2):
    n = int(dur * SR); t = np.arange(n) / SR
    s = np.sin(2 * np.pi * freq * t) + 0.4 * np.sin(2 * np.pi * freq * 2.76 * t) * np.exp(-t * 2.5) + 0.25 * np.sin(2 * np.pi * freq * 5.4 * t) * np.exp(-t * 4)
    return s * np.exp(-t * 1.8) * np.minimum(1, t * 400)

def kick(dur=0.45):
    n = int(dur * SR); t = np.arange(n) / SR
    f = 42 + 90 * np.exp(-t * 28)
    ph = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(ph) * np.exp(-t * 9) * np.minimum(1, t * 800)

def boom(dur=3.2):
    n = int(dur * SR); t = np.arange(n) / SR
    f = 36 + 40 * np.exp(-t * 3.2)
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 1.3)
    rng = np.random.default_rng(5)
    nz = lowpass(rng.standard_normal(n), 2600, 2) * np.exp(-t * 3.5) * 0.35
    return (s + nz) * np.minimum(1, t * 900)

def hat(dur=0.05, seed=0):
    rng = np.random.default_rng(seed)
    n = int(dur * SR); t = np.arange(n) / SR
    return highpass(rng.standard_normal(n), 6500) * np.exp(-t * 90)

def riser(dur, seed=1):
    rng = np.random.default_rng(seed)
    n = int(dur * SR); t = np.arange(n) / SR; u = t / dur
    nz = bandpass(rng.standard_normal(n), 400, 7000) * (u ** 2.4)
    f = 180 * (12 ** u)
    sw = np.sin(2 * np.pi * np.cumsum(f) / SR) * (u ** 2) * 0.5
    return (nz * 0.8 + sw) * np.minimum(1, (dur - t) * 40) * 0.0 + (nz * 0.8 + sw)

def reverse_cymbal(dur, seed=2):
    rng = np.random.default_rng(seed)
    n = int(dur * SR); t = np.arange(n) / SR; u = t / dur
    return highpass(rng.standard_normal(n), 3500) * (u ** 3)

def ding(freq, dur=1.4):
    n = int(dur * SR); t = np.arange(n) / SR
    return (np.sin(2 * np.pi * freq * t) + 0.5 * np.sin(2 * np.pi * freq * 2 * t) * np.exp(-t * 4)) * np.exp(-t * 3.2) * np.minimum(1, t * 900)

def reverb(buf, seconds=2.6, wet=0.3):
    out = np.zeros_like(buf)
    for ch in range(2):
        rng = np.random.default_rng(11 + ch)
        n = int(seconds * SR); t = np.arange(n) / SR
        ir = rng.standard_normal(n) * np.exp(-t * 2.6)
        ir = lowpass(ir, 5200, 2)
        ir[:int(0.012 * SR)] *= np.linspace(0, 1, int(0.012 * SR))
        ir /= np.sqrt(np.sum(ir ** 2))
        out[:, ch] = signal.fftconvolve(buf[:, ch], ir, mode="full")[:len(buf)]
    return buf * (1 - wet * 0.5) + out * wet * 1.6

def make_music(total, T, extras=None):
    """T: dict of absolute event times (s): idea, core, alert, benefits, brand_riser, brand_hit."""
    N = int((total + 3.0) * SR)
    t = np.arange(N) / SR
    buf = np.zeros((N, 2))
    bar_len = 4 * BEAT
    nbars = int(total / bar_len) + 3
    # --- low drone + pad: one chord per bar
    inten_pad = 0.35 + 0.65 * ramp(t, 0, T["core"]) + 0.3 * ramp(t, T["benefits"], T["benefits"] + 4)
    pad_stem = np.zeros((N, 2))
    for b in range(nbars):
        bass, chord = BARS[b % 4]
        t0 = b * bar_len
        place(pad_stem, t0, pad_note(midi(bass - 12), bar_len, 0.6, 1.2), 0.9, 0.0)
        for j, m in enumerate(chord):
            place(pad_stem, t0, pad_note(midi(m), bar_len, 0.9, 1.4), 0.38, -0.55 + 0.37 * j)
    buf += pad_stem * (0.17 * inten_pad)[:, None]
    # --- sparse bell motif before the core (one note per bar)
    for b in range(nbars):
        t0 = b * bar_len
        if t0 < T["core"] - 0.5:
            place(buf, t0 + BEAT * 2, bell(midi(BARS[b % 4][1][3] + 12)), 0.05 * (0.6 + 0.4 * ramp(t0, 0, T["idea"])), 0.3 if b % 2 else -0.3)
    # --- heartbeat low tom in the hook
    for b in range(nbars):
        t0 = b * bar_len
        if 1.0 < t0 < T["idea"]:
            place(buf, t0, kick(0.5), 0.10, 0.0); place(buf, t0 + BEAT * 0.75, kick(0.4), 0.06, 0.0)
    # --- arpeggio + pulse for core and benefits
    end_drive = T["brand_riser"]
    step = BEAT / 2
    k = 0
    tt = T["core"] - 0.2
    while tt < end_drive:
        bar = int(tt / bar_len)
        chord = BARS[bar % 4][1]
        pat = [0, 2, 3, 1, 2, 3, 1, 2]
        m = chord[pat[k % 8]] + (12 if (k // 8) % 2 else 0)
        g = 0.05 + 0.04 * ramp(tt, T["core"], T["alert"]) + 0.03 * ramp(tt, T["benefits"], T["benefits"] + 3)
        place(buf, tt, pluck(midi(m)), g, -0.5 if k % 2 else 0.5)
        if k % 2 == 0:
            place(buf, tt, kick(0.4), 0.17 + 0.05 * ramp(tt, T["alert"], T["benefits"]), 0.0)
        if k % 2 == 1:
            place(buf, tt, hat(0.05, k), 0.035 + 0.02 * ramp(tt, T["benefits"], T["benefits"] + 2), 0.2)
        tt += step; k += 1
    # --- accents
    place(buf, T["idea"], boom(2.6), 0.22, 0.0)
    place(buf, T["core"], boom(2.2), 0.20, 0.0)
    place(buf, T["alert"], boom(1.8), 0.20, 0.0)
    place(buf, T["alert"], ding(midi(93)), 0.07, 0.3)
    place(buf, T["benefits"], boom(2.0), 0.16, 0.0)
    place(buf, T["idea"] - 1.8, riser(1.8, 3), 0.10, 0.0)
    place(buf, T["core"] - 1.4, riser(1.4, 4), 0.09, 0.0)
    place(buf, T["benefits"] - 1.2, riser(1.2, 6), 0.08, 0.0)
    rd = T["brand_hit"] - T["brand_riser"]
    place(buf, T["brand_riser"], riser(rd, 7), 0.20, 0.0)
    place(buf, T["brand_riser"], reverse_cymbal(rd, 8), 0.10, 0.0)
    place(buf, T["brand_hit"], boom(4.5), 0.46, 0.0)
    place(buf, T["brand_hit"], hat(0.4, 99) * 3.0, 0.05, 0.0)
    # --- final major chord: A major, sustained
    fin = np.zeros((N, 2))
    for j, m in enumerate([45, 57, 61, 64, 69, 73]):
        place(fin, T["brand_hit"] - 0.1, pad_note(midi(m), total - T["brand_hit"] + 1.5, 0.5, 2.5), 0.5 if j else 0.9, -0.6 + 0.24 * j)
    buf += fin * 0.22
    for j, m in enumerate([81, 85, 88, 93, 88, 85, 81, 76]):
        place(buf, T["brand_hit"] + 0.5 + j * BEAT / 2, pluck(midi(m), 0.9), 0.05, -0.5 + 0.14 * j)
    # extra caller-provided one-shots: (time, kind, gain)
    for (ts, kind, g) in (extras or []):
        if kind == "pop": place(buf, ts, pluck(midi(84), 0.25), g, 0.2)
        if kind == "tick": place(buf, ts, pluck(midi(96), 0.12), g, -0.2)
        if kind == "whoosh": place(buf, ts, riser(0.8, int(ts * 10) % 50) * np.hanning(int(0.8 * SR)), g, 0.0)
        if kind == "ping": place(buf, ts, ding(midi(88), 1.2), g, 0.2)
    buf = reverb(buf, 2.6, 0.30)
    buf[: int(0.4 * SR)] *= np.linspace(0, 1, int(0.4 * SR))[:, None]
    # gentle limiter
    peak = np.abs(buf).max()
    buf = buf / peak * 0.92
    return buf.astype(np.float32)
