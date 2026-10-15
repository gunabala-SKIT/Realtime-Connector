"""The shared look of every Garment CV page (dashboard, status page, zone editor).

One small design system so the three pages read as one product:

* warm light canvas, white flat cards with a 1px border, almost no shadow
* system fonts only - no web fonts - because a factory network may have no internet. The stack
  prefers the platform's best UI face (Segoe UI Variable on Windows, SF Pro on macOS)
* one near-black accent for actions; colour is kept for meaning (muted pastel status badges)
* tabular numbers for every figure, visible focus rings, reduced-motion support

Pages are plain templates with `__TOKEN__` placeholders, filled by `fill()`. That keeps CSS and
JavaScript written with ordinary braces (the old `str.format` templates needed every brace doubled).
"""
from __future__ import annotations

import html
import json
import re

FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
           "%3Crect width='32' height='32' rx='8' fill='%23111111'/%3E"
           "%3Cpath d='M10 22V10h12M10 16h8' fill='none' stroke='%23ffffff' stroke-width='3' "
           "stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E")

BASE_CSS = """
:root {
  --canvas: #F7F6F3; --surface: #FFFFFF; --sunken: #F1F0EC; --hover: #FBFBFA;
  --line: #EAEAEA; --line-strong: #D9D8D3;
  --ink: #111111; --text: #2F3437; --muted: #6B6A66; --faint: #A9A8A3;
  --red-bg: #FDEBEC; --red-fg: #9F2F2D;
  --blue-bg: #E1F3FE; --blue-fg: #1F6C9F;
  --green-bg: #EDF3EC; --green-fg: #346538;
  --yellow-bg: #FBF3DB; --yellow-fg: #956400;
  --violet-bg: #EFEAF7; --violet-fg: #5B4B8A;
  --st-marking: #6C9AC2; --st-cutting: #D3A04B; --st-stitching: #9A8BBF;
  --st-handling: #7FAA89; --st-idle: #DAD9D4;
  --radius: 12px; --radius-sm: 8px; --radius-xs: 6px;
  --sans: "Segoe UI Variable Display", "SF Pro Display", "Geist Sans", "Segoe UI", "Helvetica Neue", system-ui, sans-serif;
  --serif: "Newsreader", "Instrument Serif", "Iowan Old Style", "Palatino Linotype", Georgia, serif;
  --mono: "Cascadia Mono", "Geist Mono", "SF Mono", "JetBrains Mono", Consolas, monospace;
  --ease: cubic-bezier(.16, 1, .3, 1);
  color-scheme: light;
}
*, *::before, *::after { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; scroll-behavior: smooth; }
body {
  margin: 0; background: var(--canvas); color: var(--text);
  font: 15px/1.55 var(--sans); -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
}
h1, h2, h3, p { margin: 0; }
h1, h2, h3 { color: var(--ink); text-wrap: balance; }
p { text-wrap: pretty; }
a { color: var(--ink); text-decoration-color: var(--line-strong); text-underline-offset: 3px; }
a:hover { text-decoration-color: var(--ink); }
button, input, select { font: inherit; color: inherit; }
::placeholder { color: var(--muted); opacity: 1; }
.num, td, th, .stat-value { font-variant-numeric: tabular-nums; }
.mono { font-family: var(--mono); font-size: .92em; }
.muted { color: var(--muted); }
.sr-only { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
.skip-link { position: absolute; left: 16px; top: -48px; background: var(--ink); color: #fff; padding: 10px 14px; border-radius: var(--radius-xs); z-index: 60; }
.skip-link:focus { top: 12px; }
:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; border-radius: 4px; }

/* ---- layout ------------------------------------------------------------------------------- */
.shell { max-width: 1360px; margin: 0 auto; padding: 0 32px 72px; }
.top { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding: 28px 0 22px; flex-wrap: wrap; }
.brand { display: flex; align-items: center; gap: 14px; min-width: 0; }
.brand-mark { width: 36px; height: 36px; border-radius: 9px; background: var(--ink); display: grid; place-items: center; flex: none; }
.brand-mark svg { width: 20px; height: 20px; }
.brand h1 { font-family: var(--serif); font-weight: 500; font-size: 28px; line-height: 1.1; letter-spacing: -.02em; }
.brand .where { color: var(--muted); font-size: 13.5px; margin-top: 2px; }
.chips { display: flex; gap: 8px; flex-wrap: wrap; }
.chip {
  display: inline-flex; align-items: center; gap: 8px; padding: 6px 12px; background: var(--surface);
  border: 1px solid var(--line); border-radius: 999px; font-size: 13px; color: var(--text); white-space: nowrap;
}
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--faint); flex: none; }
.dot.ok { background: #4F9A5B; } .dot.warn { background: #D3A04B; } .dot.bad { background: #C8504D; }

.tabs { display: flex; gap: 28px; border-bottom: 1px solid var(--line-strong); overflow-x: auto; scrollbar-width: none; }
.tabs::-webkit-scrollbar { display: none; }
.tab {
  appearance: none; background: none; border: 0; padding: 12px 2px; margin-bottom: -1px; color: var(--muted);
  font-size: 15px; font-weight: 500; cursor: pointer; border-bottom: 2px solid transparent; white-space: nowrap;
  transition: color .2s var(--ease), border-color .2s var(--ease);
}
.tab:hover { color: var(--ink); }
.tab[aria-selected="true"] { color: var(--ink); border-bottom-color: var(--ink); }
main { margin-top: 28px; }
.view { display: none; }
.view.active { display: block; }
.grid { display: grid; gap: 20px; }
.g-live { grid-template-columns: minmax(0, 1.85fr) minmax(320px, 1fr); align-items: start; }
.g-two { grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; }
.g-stats { grid-template-columns: repeat(6, minmax(0, 1fr)); }
.stack { display: grid; gap: 20px; }

/* ---- cards ------------------------------------------------------------------------------- */
.card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 24px; min-width: 0; }
.card.flush { padding: 0; overflow: hidden; }
.card-head { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; margin-bottom: 18px; flex-wrap: wrap; }
.card-head h2 { font-size: 16px; font-weight: 600; letter-spacing: -.005em; }
.card-head .sub { color: var(--muted); font-size: 13.5px; }
.card.flush .card-head { padding: 22px 24px 0; margin-bottom: 14px; }
.stat { padding: 20px 22px; }
.stat-value { font-size: 36px; font-weight: 600; letter-spacing: -.03em; color: var(--ink); line-height: 1.1; }
.stat-label { color: var(--muted); font-size: 13.5px; margin-top: 6px; }

/* ---- controls ---------------------------------------------------------------------------- */
.btn {
  appearance: none; display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  background: var(--surface); color: var(--ink); border: 1px solid var(--line-strong); border-radius: var(--radius-xs);
  padding: 9px 15px; font-size: 14px; font-weight: 500; cursor: pointer; line-height: 1.2;
  transition: background .2s var(--ease), border-color .2s var(--ease), transform .12s var(--ease), color .2s var(--ease);
}
.btn:hover { background: var(--sunken); }
.btn:active { transform: scale(.98); }
.btn[disabled] { opacity: .45; cursor: not-allowed; transform: none; }
.btn-primary { background: var(--ink); border-color: var(--ink); color: #fff; }
.btn-primary:hover { background: #333; }
.btn-danger { color: var(--red-fg); border-color: #EBC9CA; background: var(--surface); }
.btn-danger:hover { background: var(--red-bg); }
.btn-sm { padding: 6px 11px; font-size: 13px; }
.btn.is-confirm { background: var(--yellow-bg); border-color: #E6D29A; color: var(--yellow-fg); }
.btn-row { display: flex; gap: 8px; flex-wrap: wrap; }
.seg { display: inline-flex; padding: 3px; background: var(--sunken); border-radius: var(--radius-sm); gap: 2px; flex-wrap: wrap; }
.seg button {
  appearance: none; border: 0; background: transparent; padding: 7px 14px; border-radius: 6px; font-size: 14px; font-weight: 500;
  color: var(--muted); cursor: pointer; transition: background .2s var(--ease), color .2s var(--ease), transform .12s var(--ease);
}
.seg button:hover { color: var(--ink); }
.seg button:active { transform: scale(.98); }
.seg button[aria-pressed="true"] { background: var(--surface); color: var(--ink); box-shadow: 0 0 0 1px var(--line); }
.seg button.is-confirm { background: var(--yellow-bg); color: var(--yellow-fg); }
.input, .select {
  height: 38px; padding: 0 12px; background: var(--surface); border: 1px solid var(--line-strong); border-radius: var(--radius-xs);
  transition: border-color .2s var(--ease); min-width: 0;
}
.input:hover, .select:hover { border-color: var(--faint); }
.input:focus-visible, .select:focus-visible { outline: 2px solid var(--ink); outline-offset: 1px; border-color: var(--ink); }
.toggle-chip { display: inline-flex; align-items: center; gap: 8px; padding: 6px 12px 6px 10px; border: 1px solid var(--line); background: var(--surface); border-radius: 999px; font-size: 13px; cursor: pointer; user-select: none; }
.toggle-chip:hover { border-color: var(--line-strong); }
.toggle-chip input { accent-color: var(--ink); margin: 0; width: 15px; height: 15px; }
.toggle-chip:has(input:focus-visible) { outline: 2px solid var(--ink); outline-offset: 2px; }

/* ---- picture frame (camera view) ---------------------------------------------------------- */
.frame { position: relative; background: #151515; line-height: 0; }
.frame img { display: block; width: 100%; height: auto; }
.frame canvas { position: absolute; inset: 0; width: 100%; height: 100%; }
.frame-badge { position: absolute; left: 14px; bottom: 14px; line-height: 1; display: inline-flex; align-items: center; gap: 8px; background: rgba(255,255,255,.92); color: var(--ink); font-size: 12px; font-weight: 600; padding: 7px 11px; border-radius: 999px; }
.frame-badge .dot { background: #C8504D; }
.frame-badge.off .dot { background: var(--faint); }
.frame-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 16px 20px; border-top: 1px solid var(--line); }
.frame-bar .label { color: var(--muted); font-size: 13px; margin-right: 6px; }

.help { color: var(--muted); font-size: 13.5px; }

/* ---- badges ------------------------------------------------------------------------------ */
.badge {
  display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 600;
  letter-spacing: .05em; text-transform: uppercase; white-space: nowrap; background: var(--sunken); color: var(--muted);
}
.b-blue { background: var(--blue-bg); color: var(--blue-fg); } .b-green { background: var(--green-bg); color: var(--green-fg); }
.b-yellow { background: var(--yellow-bg); color: var(--yellow-fg); } .b-red { background: var(--red-bg); color: var(--red-fg); }
.b-violet { background: var(--violet-bg); color: var(--violet-fg); } .b-gray { background: var(--sunken); color: var(--muted); }

/* ---- tables ------------------------------------------------------------------------------ */
.table-wrap { overflow-x: auto; }
.tbl { width: 100%; border-collapse: collapse; font-size: 14px; }
.tbl th { text-align: left; padding: 10px 24px; color: var(--muted); font-weight: 500; font-size: 13px; border-bottom: 1px solid var(--line); white-space: nowrap; }
.tbl td { padding: 13px 24px; border-bottom: 1px solid var(--line); color: var(--text); vertical-align: middle; }
.tbl tr:last-child td { border-bottom: 0; }
.tbl tbody tr:hover td { background: var(--hover); }
.tbl .r { text-align: right; }
.tbl td.k { color: var(--muted); width: 38%; }

/* ---- feedback ---------------------------------------------------------------------------- */
.empty { text-align: center; padding: 44px 24px; color: var(--muted); }
.empty strong { display: block; color: var(--ink); font-weight: 600; margin-bottom: 4px; font-size: 15px; }
.empty.compact { padding: 22px 8px; }
.banner { display: flex; gap: 12px; align-items: flex-start; padding: 13px 16px; border-radius: var(--radius-sm); font-size: 14px; }
.banner + .banner { margin-top: 8px; }
.banner.warn { background: var(--yellow-bg); color: var(--yellow-fg); }
.banner.bad { background: var(--red-bg); color: var(--red-fg); }
.banner.info { background: var(--blue-bg); color: var(--blue-fg); }
.banner .dot { margin-top: 7px; background: currentColor; }
.skel { background: var(--sunken); border-radius: 6px; height: 14px; animation: pulse 1.4s ease-in-out infinite; }
.skel + .skel { margin-top: 12px; }
.toast-zone { position: fixed; left: 50%; bottom: 28px; transform: translateX(-50%); z-index: 50; display: grid; gap: 8px; pointer-events: none; width: max-content; max-width: calc(100vw - 32px); }
.toast { background: var(--ink); color: #fff; padding: 11px 16px; border-radius: var(--radius-sm); font-size: 14px; animation: rise .35s var(--ease); }
.toast.bad { background: var(--red-fg); }
.meter { height: 6px; background: var(--sunken); border-radius: 3px; overflow: hidden; }
.meter > i { display: block; height: 100%; width: 100%; background: var(--ink); transform-origin: left center; transform: scaleX(0); transition: transform .5s var(--ease); }
.kbd { font-family: var(--mono); font-size: 12px; padding: 1px 6px; border: 1px solid var(--line-strong); border-radius: 4px; background: var(--canvas); }

@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .45; } }
@keyframes rise { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: no-preference) {
  .view.active { animation: rise .45s var(--ease); }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; scroll-behavior: auto !important; }
}
@media (max-width: 1100px) {
  .g-live, .g-two { grid-template-columns: minmax(0, 1fr); }
  .g-stats { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 640px) {
  .shell { padding: 0 16px 56px; }
  .top { padding: 20px 0 16px; }
  .brand h1 { font-size: 24px; }
  .g-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .card { padding: 18px; }
  .tbl th, .tbl td { padding-left: 14px; padding-right: 14px; }
}
"""


def fill(template: str, **values: str) -> str:
    """Replace `__NAME__` tokens in ONE pass (a value that itself contains `__X__` is never re-expanded).
    Values are inserted as given - escape them first (`esc` for HTML, `js` for script)."""
    lookup = {k.upper(): v for k, v in values.items()}
    return re.sub(r"__([A-Z0-9_]+)__", lambda m: lookup.get(m.group(1), m.group(0)), template)


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def js(value: object) -> str:
    """A JSON literal that is safe inside a <script> element."""
    return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
