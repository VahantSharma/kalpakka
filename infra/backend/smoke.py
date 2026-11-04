# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Post-deploy smoke test (stdlib only): healthz + one real WebSocket handshake through Caddy.

    uv run --script infra/backend/smoke.py https://13-233-10-25.sslip.io --origin https://<web>.vercel.app

Checks, in order (exit 0 only if all pass):
  1. GET /healthz over HTTPS answers 200 with {"ok": true} (the certificate must verify)
  2. WS /call with the allowed Origin answers 101 with a correct Sec-WebSocket-Accept
  3. WS /call with a foreign Origin is refused (not 101); the backend closes it with 1008
It sends a close frame right after the handshake, so no audio flows and nothing is billed, BUT
the accepted handshake passes admission: during live hours it counts as one call against the
daily total and against this IP's daily cap (CALLS_PER_IP_PER_DAY) and shows in calls_today.
Run it from a network the phone testers do not share, and not more than needed on live day.
"""

import argparse
import base64
import hashlib
import json
import os
import socket
import ssl
import sys
import time
import urllib.request
from urllib.parse import urlsplit

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def health_problems(body: dict[str, object]) -> list[str]:
    """What stops a real call even though /healthz answers 200 (`ok` alone is always true)."""
    problems = []
    if body.get("ok") is not True:
        problems.append("healthz ok is not true")
    if body.get("gnani_key") is not True:
        problems.append("GNANI_API_KEY not set on the backend")
    lines = body.get("fixed_lines")
    if not isinstance(lines, dict) or not lines:
        problems.append("no fixed lines warmed")
    else:
        failed = sorted(k for k, v in lines.items() if v != "ok")
        if failed:
            problems.append(f"fixed-line warm-up failed: {', '.join(failed)}")
    return problems


def sslip_host(ip: str) -> str:
    parts = ip.split(".")
    if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        raise ValueError(f"not an IPv4 address: {ip!r}")
    return "-".join(parts) + ".sslip.io"


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


def handshake_request(host: str, path: str, *, origin: str, key: str) -> bytes:
    lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {host}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
        f"Origin: {origin}",
        "User-Agent: kalpakka-smoke/1",
    ]
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def parse_status(raw: bytes) -> tuple[int, dict[str, str]]:
    head = raw.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    status, *rest = head.split("\r\n")
    code = int(status.split(" ")[1])
    headers = {}
    for line in rest:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return code, headers


def ws_close_frame(mask: bytes | None = None) -> bytes:
    mask = mask or os.urandom(4)
    payload = (1000).to_bytes(2, "big")
    return (
        bytes([0x88, 0x80 | len(payload)])
        + mask
        + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    )


def ws_handshake(base: str, origin: str, timeout: float = 15) -> tuple[int, bool, float]:
    """(status, accept header correct, seconds) for one handshake to <base>/call."""
    url = urlsplit(base)
    host, port = url.hostname or "", url.port or (443 if url.scheme == "https" else 80)
    key = base64.b64encode(os.urandom(16)).decode()
    started = time.monotonic()
    sock: socket.socket = socket.create_connection((host, port), timeout=timeout)
    if url.scheme == "https":
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
    try:
        sock.sendall(handshake_request(url.netloc, "/call", origin=origin, key=key))
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
        elapsed = time.monotonic() - started
        code, headers = parse_status(raw)
        ok_accept = headers.get("sec-websocket-accept") == accept_key(key)
        if code == 101:
            sock.sendall(ws_close_frame())
        return code, ok_accept, elapsed
    finally:
        sock.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="KalPakka backend smoke test")
    parser.add_argument("base", help="https://<ip-with-dashes>.sslip.io")
    parser.add_argument("--origin", required=True, help="the allowed web origin (ALLOWED_ORIGINS)")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    failures = 0

    try:
        t = time.monotonic()
        with urllib.request.urlopen(f"{base}/healthz", timeout=15) as resp:
            body = json.loads(resp.read())
            problems = health_problems(body)
            ok = resp.status == 200 and not problems
        print(f"[smoke] healthz {resp.status} in {time.monotonic() - t:.3f} s: {body}")
        for problem in problems:
            print(f"[smoke]   problem: {problem}")
    except Exception as exc:  # report every failure, keep going
        ok = False
        print(f"[smoke] healthz FAILED: {exc}")
    failures += not ok

    for origin, want_101 in ((args.origin, True), ("https://evil.example", False)):
        try:
            code, accept_ok, secs = ws_handshake(base, origin)
            ok = (code == 101 and accept_ok) if want_101 else code != 101
            print(f"[smoke] ws /call origin={origin} -> {code} in {secs:.3f} s "
                  f"({'expected' if ok else 'UNEXPECTED'})")  # fmt: skip
        except Exception as exc:
            ok = False
            print(f"[smoke] ws /call origin={origin} FAILED: {exc}")
        failures += not ok
    print(f"[smoke] {'PASS' if not failures else f'FAIL ({failures} checks)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
