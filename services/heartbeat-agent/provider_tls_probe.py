"""Prepare an explicitly authorized CONNECT + TLS probe without a Ping URL."""

from __future__ import annotations

import argparse
import socket
import ssl
import sys


PROXY_HOST = "172.31.255.250"
PROXY_PORT = 3128
PROVIDER_HOST = "hc-ping.com"
MAX_HEADER_BYTES = 8192


class ProbeError(RuntimeError):
    """A sanitized failure suitable for local output."""


def _read_connect_response(connection: socket.socket) -> bytes:
    response = bytearray()
    while b"\r\n\r\n" not in response:
        if len(response) >= MAX_HEADER_BYTES:
            raise ProbeError("proxy response headers exceed the local safety limit")
        chunk = connection.recv(min(1024, MAX_HEADER_BYTES - len(response)))
        if not chunk:
            raise ProbeError("proxy closed before completing CONNECT")
        response.extend(chunk)
    return bytes(response)


def probe_provider_tls(
    create_connection=socket.create_connection,
    create_tls_context=ssl.create_default_context,
) -> None:
    """Establish CONNECT then verify TLS, sending no HTTP request or Ping URL."""
    try:
        connection = create_connection((PROXY_HOST, PROXY_PORT), timeout=5)
        with connection:
            request = (
                f"CONNECT {PROVIDER_HOST}:443 HTTP/1.1\r\n"
                f"Host: {PROVIDER_HOST}:443\r\n"
                "Proxy-Connection: keep-alive\r\n\r\n"
            ).encode("ascii")
            connection.sendall(request)
            response = _read_connect_response(connection)
            status_line = response.split(b"\r\n", maxsplit=1)[0]
            if status_line not in (b"HTTP/1.0 200 Connection established", b"HTTP/1.1 200 Connection established"):
                raise ProbeError("proxy did not authorize the fixed CONNECT destination")
            context = create_tls_context()
            with context.wrap_socket(connection, server_hostname=PROVIDER_HOST) as tls:
                tls.do_handshake()
    except ProbeError:
        raise
    except (OSError, ssl.SSLError, TimeoutError) as error:
        raise ProbeError("authorized proxy CONNECT/TLS probe failed") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authorize-provider-contact",
        action="store_true",
        help="explicitly authorize a TCP CONNECT and TLS handshake to hc-ping.com:443",
    )
    args = parser.parse_args()
    if not args.authorize_provider_contact:
        print(
            "Refusing provider contact; rerun only after explicit owner authorization.",
            file=sys.stderr,
        )
        return 2
    try:
        probe_provider_tls()
    except ProbeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("PASS: CONNECT and TLS verified; no HTTP request or Ping URL was sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
