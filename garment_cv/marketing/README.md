# Marketing videos

Python scripts that render the SKIT AI Vision product videos (Pillow + numpy + an ffmpeg binary, voice by Windows
speech or `edge-tts`, music synthesized in code). Rendered `.mp4` files and stock footage are NOT in git.

- `explainer_video/` - the "how it works" explainer (`explainer.py` -> `explainer2.py` -> `explainer3.py`), plus `srikesh_logo.png`.
- `shelf_stock_ad/` - the shelf stock + Odoo warehouse advert. `shelf_ad5.py` is the latest (realistic, product-first version),
  `music.py` / `music2.py` the score, `tts_edge.py` the neural voice.

## Stock footage (free for commercial use, no attribution required - Pexels licence)

Download from `https://www.pexels.com/download/video/<id>/` into `shelf_stock_ad/footage/` as `pexels_<id>.mp4`:
20591258, 37208842, 4292300, 7018667, 4284182, 6169987, 6169086, 4294436, 4292902, 4477613, 14910119
(warehouse aisles, scanning, picking and shelf close-ups). The explainer used Pexels video 6766325 as its camera feed.

Do not present the footage as live product footage; the ads label the interface as illustrative.
