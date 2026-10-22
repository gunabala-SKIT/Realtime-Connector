# Setting up Realtime Connector on a new machine

This repo holds the code for every product in the project:

| Folder | What it is |
|---|---|
| `garment_cv/` | SKIT AI Vision, the garment manufacturing CV product, with the Odoo 19 Manufacturing bridge (port 8200) |
| `manufacturing_intel/` | Manufacturing Process Intelligence Platform (port 8100) |
| `phase1_camera/` ... `phase14_ppe/` | Reality Connector: camera, YOLO, faces, events, dashboard, rules, zones, shelf, queue, footfall, attendance, vehicles, PPE |

## 1. Install

Tested on Windows 10 with Python 3.12 and a GTX 1660 (CUDA 12.4 build of PyTorch).

```bash
git clone https://github.com/gunabala-SKIT/Realtime-Connector.git
cd Realtime-Connector
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements-freeze.txt
```

`requirements-freeze.txt` pins `torch==2.6.0+cu124`. If pip cannot find that build, install PyTorch first from
`https://download.pytorch.org/whl/cu124`, then the rest.

Always run anything that uses torch or ultralytics with the venv's Python (`.venv\Scripts\python.exe`). A system
Python often has a CPU-only torch, which makes training about 10x slower.

If you use face recognition, the known `onnxruntime` and `chromadb` conflict is explained in
`phase1_camera/README.md` and the project notes: `chromadb` reinstalls CPU `onnxruntime`, so re-install
`onnxruntime-gpu==1.20.2` afterwards.

## 2. Configuration (not in the repo)

Credentials are never committed. Copy the example files and fill them in:

```bash
copy garment_cv\configs\.env.example garment_cv\configs\.env
copy phase1_camera\.env.example phase1_camera\.env
```

Set the camera address and login, and for the Odoo bridge the Odoo URL, database name, API key and work center
code (see `garment_cv/docs/ODOO_RUNBOOK.md`). Keep the bridge in `shadow` mode until you have checked it.

## 3. What is NOT in the repo (copy it over by hand if you need it)

| Item | Why it is not in git | What to do |
|---|---|---|
| `.env` files | Camera passwords and Odoo API key | Recreate from `.env.example` |
| `garment_cv/configs/zones.json` | Your drawn zones | Re-draw at `/zones`, or copy the file |
| `garment_cv/configs/garment.db`, `*.db`, event snapshots | Runtime data | Created again on first run |
| Employee face photos (`img/`) and the face gallery (`phase3_identity/gallery_chroma/`) | Personal data | Re-enrol with `phase3_identity/enroll.py` |
| `garment_cv/training/datasets/` (about 1.6 GB, including 1.3 GB of raw capture frames) | Too large for git | Copy by hand only if you want to retrain. The hand-drawn labels ARE in git (`garment_cv/training/labels_new_framing.json`). |
| Other model weights (`*.pt`, `*.onnx`) | Auto-downloaded or rebuildable | Ultralytics downloads `yolo11n.pt` on first use |
| Marketing videos and stock footage (`garment_cv/marketing/**/*.mp4`) | Large binaries | Scripts are included. Stock clips come from Pexels (see the folder README). |

Included on purpose despite the rules above: `garment_cv/models/live_demo.pt` (the trained marking, cutting and pen
model) and `phase14_ppe/models/ppe.pt` (the trained PPE model).

## 4. Run and test

```bash
python -m garment_cv                       # SKIT AI Vision on http://127.0.0.1:8200
python -m unittest discover -s garment_cv/tests -t .      # about 35 s, 300 tests
python -m manufacturing_intel              # Manufacturing Intelligence on port 8100
python -m phase5_dashboard                 # Reality Connector dashboard on port 8000
```

`garment_cv/README.md` has the full run instructions, including how to try the product with a video file
instead of a live camera.
