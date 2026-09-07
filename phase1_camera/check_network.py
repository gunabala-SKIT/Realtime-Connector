"""Network pre-flight checks (Phase 1 doc section 7, TC-001 / TC-002).

    python -m phase1_camera.check_network
"""
from __future__ import annotations

import platform
import socket
import subprocess
import sys

from .config import Config


def ping(host: str, count: int = 2) -> bool:
    flag = "-n" if platform.system().lower().startswith("win") else "-c"
    try:
        result = subprocess.run(
            ["ping", flag, str(count), host],
            capture_output=True, text=True, timeout=20,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def port_open(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main() -> int:
    cfg = Config.load()

    if not cfg.uses_rtsp:
        print(f"CAMERA_SOURCE is set ({cfg.camera_source}); network checks do not apply.")
        return 0

    host, port = cfg.camera_ip, cfg.rtsp_port
    if not host:
        print("CAMERA_IP is not set. Copy .env.example to .env and fill it in first.")
        return 2

    print(f"Target DVR/NVR : {host}:{port}\n")

    icmp = ping(host)
    print(f"TC-001  ICMP ping to {host} ......... "
          + ("PASS" if icmp else "FAIL  (ICMP may be firewalled - not conclusive)"))

    rtsp = port_open(host, port)
    print(f"TC-002  TCP {port} (RTSP) reachable ... " + ("PASS" if rtsp else "FAIL"))

    print()
    if not rtsp:
        print("RTSP port is not reachable. Check:")
        print("  - RTSP is enabled on the DVR (Network > Advanced > Integration Protocol / RTSP)")
        print("  - the configured RTSP port number matches RTSP_PORT")
        print("  - firewall / VLAN between this PC and the DVR")
        return 1

    print("Network path OK. Next: verify the RTSP URL in VLC (Media > Open Network Stream),")
    print("then run:  python -m phase1_camera.app")
    return 0


if __name__ == "__main__":
    sys.exit(main())
