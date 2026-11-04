import base64
import hashlib

import pytest
from smoke import (
    accept_key,
    handshake_request,
    health_problems,
    parse_status,
    sslip_host,
    ws_close_frame,
)


def test_sslip_host_from_an_ipv4() -> None:
    assert sslip_host("13.233.10.25") == "13-233-10-25.sslip.io"


@pytest.mark.parametrize("bad", ["13.233.10", "300.1.1.1", "abc", "13.233.10.25.1"])
def test_sslip_host_rejects_non_ipv4(bad: str) -> None:
    with pytest.raises(ValueError):
        sslip_host(bad)


def test_accept_key_matches_rfc6455_example() -> None:
    # RFC 6455 section 1.3 worked example.
    assert accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_handshake_carries_origin_and_upgrade_headers() -> None:
    req = handshake_request("13-233-10-25.sslip.io", "/call", origin="https://kalpakka.vercel.app",
                            key="dGhlIHNhbXBsZSBub25jZQ==")  # fmt: skip
    text = req.decode()
    assert text.startswith("GET /call HTTP/1.1\r\n")
    assert "Host: 13-233-10-25.sslip.io\r\n" in text
    assert "Origin: https://kalpakka.vercel.app\r\n" in text
    assert "Upgrade: websocket\r\n" in text and "Connection: Upgrade\r\n" in text
    assert "Sec-WebSocket-Version: 13\r\n" in text
    assert text.endswith("\r\n\r\n")


def test_parse_status_reads_code_and_headers() -> None:
    raw = (b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
           b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n\r\n")  # fmt: skip
    code, headers = parse_status(raw)
    assert code == 101
    assert headers["sec-websocket-accept"] == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_parse_status_403() -> None:
    assert parse_status(b"HTTP/1.1 403 Forbidden\r\ncontent-length: 0\r\n\r\n")[0] == 403


def test_close_frame_is_masked_with_code_1000() -> None:
    frame = ws_close_frame(mask=b"\x01\x02\x03\x04")
    assert frame[0] == 0x88  # FIN + close opcode
    assert frame[1] == 0x80 | 2  # masked, 2-byte payload
    payload = bytes(b ^ m for b, m in zip(frame[6:], b"\x01\x02", strict=True))
    assert payload == (1000).to_bytes(2, "big")


def test_accept_key_is_sha1_base64() -> None:
    key = "abc"
    expected = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    ).decode()
    assert accept_key(key) == expected


def test_healthy_backend_has_no_problems() -> None:
    body = {"ok": True, "live": True, "gnani_key": True, "fixed_lines": {"ramesh": "ok"}}
    assert health_problems(body) == []


def test_missing_gnani_key_is_a_problem_even_though_ok_is_true() -> None:
    body = {"ok": True, "gnani_key": False, "fixed_lines": {}}
    assert "GNANI_API_KEY not set on the backend" in health_problems(body)


def test_failed_fixed_line_warmup_is_a_problem() -> None:
    body = {"ok": True, "gnani_key": True, "fixed_lines": {"ramesh": "failed", "lakshmi": "ok"}}
    assert health_problems(body) == ["fixed-line warm-up failed: ramesh"]


def test_no_fixed_lines_reported_is_a_problem() -> None:
    assert health_problems({"ok": True, "gnani_key": True}) == ["no fixed lines warmed"]


def test_not_ok_is_a_problem() -> None:
    assert "healthz ok is not true" in health_problems({"ok": False})
