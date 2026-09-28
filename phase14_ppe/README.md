# Phase 14 — PPE / safety compliance

Checks people standing in **PPE zones** for the safety equipment each zone
requires (hard hat, hi-vis vest, gloves, boots, goggles), logs a violation with
an annotated evidence snapshot, and raises it on the dashboard's **Safety (PPE)**
page (plus a toast/sound on every page).

Runs inside the normal dashboard process — no separate app:

```
python -m phase5_dashboard
```

## Setting it up

1. **Model.** `models/ppe.pt` is committed (5.5 MB). To retrain it:
   ```
   # dataset: Ultralytics Construction-PPE (AGPL-3.0), 178 MB, unzip into phase14_ppe/datasets/
   #   https://github.com/ultralytics/assets/releases/download/v0.0.0/construction-ppe.zip
   python -m phase14_ppe.train          # ~40 epochs x ~2.2 min on a GTX 1660, then scores the test split
   python -m phase14_ppe.evaluate       # per-person decision accuracy (see Results)
   ```
   If the model file is missing, PPE monitoring switches itself off with the
   reason on the Safety page; the rest of the dashboard runs normally.
2. **Zone.** On **Zones**, draw a zone and turn on **PPE zone**. The items in
   `PPE_REQUIRED_ITEMS` are pre-ticked; change them per zone. Only people whose
   feet are in a PPE zone are checked.
3. **Settings** (`phase1_camera/.env`, or the **Settings → PPE** section):

   | Key | Default | Meaning |
   |---|---|---|
   | `PPE_MODEL` | `phase14_ppe/models/ppe.pt` | detector weights |
   | `PPE_REQUIRED_ITEMS` | `helmet,vest,gloves` | pre-ticked items for new PPE zones |
   | `PPE_CONF` | `0.4` | detector confidence |
   | `PPE_CHECK_INTERVAL_S` | `1.0` | seconds between checks (runs on its own thread; video never waits) |
   | `PPE_CONFIRM_CHECKS` | `3` | consecutive failing checks before a violation is logged |
   | `PPE_COOLDOWN_S` | `300` | don't re-log the same person/item within this — unless they complied in between |
   | `PPE_MIN_PERSON_PX` | `80` | people shorter than this (px) are too small to judge |
   | `PPE_ZONE_MARGIN` | `0.08` | zone tolerance, as in Phase 7 |
   | `PPE_DEVICE`, `PPE_IMGSZ` | auto, `640` | |

## How a decision is made

`compliance.py` is pure logic (no model/camera/DB) so it's tested on synthetic
boxes and scored on labelled images with the exact same code.

- **Attribution by body region.** A helmet counts only if its centre is in the
  person's head band, a vest only on the torso, gloves near the arms — so a
  neighbour's helmet, or one carried at the waist, doesn't count.
- **Explicit negatives.** The model also detects `no_helmet` (bare head),
  `no_gloves` (bare hands) and `none` — the dataset's bare-torso label, i.e.
  its "no vest" class.
- **Absence as evidence — for helmet and vest only.** They're large and visible
  on anyone in view, so "none detected" fails the check. Hands and feet are
  often hidden, so gloves/boots fail only on an explicit `no_*` detection.
- **"Can't tell" is not "missing".** Too small, head cut off by the frame top,
  or >40% covered by someone else's box → `unknown`, which neither advances nor
  resets confirmation. (Occlusion caused over half the false alarms before this
  rule.) A clearly visible bare head still counts even on a partly hidden person.
- **Confirmation.** A violation is logged after `PPE_CONFIRM_CHECKS` consecutive
  failing checks for the same tracked person, then held off for
  `PPE_COOLDOWN_S` — but wearing the item for `PPE_CONFIRM_CHECKS` checks ends
  the cooldown, so "put the helmet back on, took it off again" is a new
  incident. That also stops a reused tracker id from hiding a different person
  (found in the end-to-end test, below).

Each violation stores the track id, identity (Phase 3 name if the face is
known), zone, missing items, and a snapshot with the person boxed in red, the
PPE evidence around them (green = item, red = explicit `no_*`), and a caption.
Review with **ack** / **close** on the Safety page or `POST /api/ppe-violations/{id}`.

## API

| Route | |
|---|---|
| `GET /api/ppe/status` | enabled / disabled reason, checks run, inference ms, live per-person item status |
| `GET /api/ppe-violations?status=open\|ack\|closed&window=today&limit=` | `{count, violations}` |
| `GET /api/ppe-violations/summary` | today's `total`, `open`, `by_item` |
| `GET /api/ppe-violations/{id}/snapshot` | evidence JPEG |
| `POST /api/ppe-violations/{id}` | `{"status": "open"\|"ack"\|"closed"}` |
| Zones: `ppe_required` | comma list on `POST/PATCH /api/zones` (validated) |

## Results (measured 2026-09-24)

**Detector, held-out test split** (YOLO11n fine-tuned, 40 epochs, mAP50):
helmet 0.942 · vest 0.895 · gloves 0.791 · person 0.856 · no_helmet 0.289 ·
none (no-vest) 0.466 · no_gloves 0.126 (recall 0.05).

**Per-person decisions** (`evaluate.py`: detector + the rules above, against the
human labels, test split):

| item | violations | caught | false alarms | precision | recall | accuracy | judged / not judged |
|---|---|---|---|---|---|---|---|
| helmet | 32 | 32 | 5 | 86.5% | 100% | 97.4% | 189 / 47 |
| vest | 45 | 45 | 7 | 86.5% | 100% | 96.6% | 204 / 32 |
| gloves | 31 | 2 | 1 | — | 6.5% | — | — |

**End-to-end** — the real dashboard on `test_video.mp4` (8 labelled scenes,
alternating compliant / no-helmet-no-vest; build it with
`python -m phase14_ppe.make_test_video`), isolated DB, 3+ loops: every violation
scene logged a violation on every loop ~2.5 s after it appeared (3 checks), zero
violations in compliant scenes, 13 ms per check. Snapshot, ack/close, filters,
the zone PPE switch and the live toast verified in the browser.

## Known limitations — read before promising this to a client

- **Gloves effectively don't work.** The model rarely detects bare hands
  (`no_gloves` recall 5%), so a missing glove is almost never flagged. Keep
  gloves off the requirement list, or treat it as best-effort, until the model
  is retrained on better hand data.
- **Tuned on a public dataset, not your site.** Precision (~87%) will differ on
  your cameras, angles and PPE colours. Expect to review snapshots — that's why
  every violation keeps one — and fine-tune on your own footage for production.
- **Dataset quirks we measured:** several images show worn hi-vis vests with no
  vest label (so vest precision may be better than it looks); bicycle helmets
  are labelled `helmet` but the model calls them bare heads (3 of the 5 helmet
  "false alarms" — arguably correct on a construction site); no clear
  "helmet missing, vest worn" examples existed to test that combination alone.
- **Partly visible bystanders are judged too.** In the test clip a person cut off
  at the image edge (not in the dataset labels) was flagged: missing vest is
  correct, missing helmet is uncertain because the head isn't clearly visible.
- **Not yet tested on a live camera** with real people putting PPE on and off.
- The dataset and anything trained on it are AGPL-3.0 (Ultralytics).
