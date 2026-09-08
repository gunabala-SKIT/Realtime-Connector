# Reality Connector — Phase 1: Hikvision Camera Connection

Connect the Hikvision DVR/NVR stream to a local Python application and view it live,
with automatic reconnect, credential-safe logging and stream metrics.

**In scope:** RTSP connection, live display, reconnect, logging, metrics, stability.
**Out of scope (Phase 2+):** YOLO, tracking, face detection/recognition, enrollment, alerts, dashboard, database.

---

## 1. Collect DVR information (doc §6)

| Item | Value |
|---|---|
| DVR/NVR IP address | |
| RTSP port (usually 554 — confirm) | |
| Username / password | (put in `.env`, never here) |
| Channel id (101 = ch1 main, 102 = ch1 sub, …) | |
| Main vs sub stream | start with **sub** |
| Codec (H.264 / H.265) | prefer **H.264** first |

## 2. Network pre-flight (doc §7)

```bash
ping <DVR_IP>
```
```bash
powershell Test-NetConnection <DVR_IP> -Port 554
```

Or, once `.env` is filled:

```bash
python -m phase1_camera.check_network
```

## 3. Set up the environment

```bash
py -m venv .venv
.venv\Scripts\activate
pip install -r phase1_camera/requirements.txt
python -c "import cv2; print(cv2.__version__)"
```

## 4. Configure credentials

```bash
copy phase1_camera\.env.example phase1_camera\.env
```

Edit `phase1_camera/.env` with your DVR IP, port, credentials and channel.
`.env` is git-ignored — never commit it.

## 5. Test the RTSP URL outside Python first (doc §18)

VLC → *Media ▸ Open Network Stream* →
`rtsp://USER:PASS@DVR_IP:554/Streaming/Channels/102`

If VLC works but Python does not, it is a decoder/backend issue, not the camera.

## 6. Run

```bash
python -m phase1_camera.probe            # find the right RTSP path if 101/102 fail
```
```bash
python -m phase1_camera.test_rtsp        # minimal smoke test
```
```bash
python -m phase1_camera.app              # full live viewer (q / Esc to quit)
```
```bash
python -m phase1_camera.app --no-display --duration 3600   # 1-hour soak test
```
```bash
python -m phase1_camera.app --snapshot frame.jpg           # one frame, then exit
```

---

## Module layout (doc §10)

| File | Responsibility |
|---|---|
| `config.py` | Load `.env`; build & mask the RTSP URL; reconnect/logging policy |
| `logger.py` | Console + rotating file logs; **redacts credentials from every record** |
| `camera.py` | `CameraStream`: `connect`, `read_frame`, `is_connected`, `reconnect`, `release`, `get_fps`, `get_resolution`, `get_status` + metrics. No AI logic. |
| `app.py` | Live viewer / soak-test runner: read loop, overlay, reconnect orchestration, clean shutdown |
| `check_network.py` | ICMP ping + RTSP port reachability (TC-001, TC-002) |
| `test_rtsp.py` | Smallest possible OpenCV open+decode check (doc §13) |

## Metrics recorded (doc §16)

`frames_received`, `frames_failed`, `connection_attempts`, `reconnections`,
`stream_uptime_s`, `actual_fps`, `resolution`, `consecutive_failures` —
logged every `--metrics-interval` seconds and on exit.

## Reconnection flow (doc §15)

`Connected → frame failure → log → release → wait RECONNECT_WAIT_SECONDS → reconnect → resume`,
after `READ_RETRY_LIMIT` consecutive failed reads. `RECONNECT_MAX_ATTEMPTS=0` retries forever
once the stream has been up. The **initial** connect is bounded by `STARTUP_MAX_ATTEMPTS`
(default 3) so the app exits with an error instead of hanging when the DVR is unreachable.

## Troubleshooting (doc §22)

| Symptom | Check |
|---|---|
| `ping` fails | IP / subnet / cabling / firewall (ICMP block is not conclusive) |
| RTSP port closed | RTSP enabled on DVR? correct port? firewall? |
| VLC fails too | URL, credentials, channel id |
| VLC works, Python fails | decoder / FFmpeg backend / H.265 support — try a sub stream / H.264 |
| `method OPTIONS failed: 404` | wrong RTSP path/channel — run `python -m phase1_camera.probe` to find the right one |
| Video freezes | network bandwidth, bitrate, decoder load — try sub stream |
| Auth fails | credentials; URL-encode special characters (handled automatically here) |
| High CPU | use the sub stream; H.264 over H.265 for now |
| Green/grey mush, only motion visible, never clears; `PPS id out of range` / `SPS 0 does not exist` in the log | **H.264+ / H.265+ / "Smart Codec" is enabled on the DVR.** It emits a non-standard stream (one I-frame, then only P-frames) that no third-party decoder can follow. Fix: on the DVR, per camera, set `Enable H.264+ = Disabled` and Video Encoding = plain `H.264` for main **and** sub stream, then **reboot the DVR**. Confirm the cause first with iVMS-4200 / Hik-Connect (private protocol, port 8000 — decodes `+` fine): clean there but garbage in VLC = the `+` codec. |

### Notes for the DS-7204HQHI-K1 (this project's DVR)

4 analog cameras on channels A1–A4; RTSP only from the DVR (no per-camera IP).
H.264+ is on by default — disable it via `Menu → Record → Parameters → Record` tab,
per camera, then `Menu → Maintenance → Reboot`. Set I-frame interval to 25 while there.

## Definition of Done (doc §25)

- [ ] DVR reachable; RTSP enabled & verified; channel confirmed
- [ ] Python authenticates; OpenCV receives live frames
- [ ] Live video is stable; resolution & FPS measured and recorded
- [ ] Reconnect works after a temporary network / DVR interruption
- [ ] Credentials never in source, logs, or git
- [ ] Multi-hour stability test passes with no uncontrolled memory growth
- [ ] This README + measured performance baseline completed
