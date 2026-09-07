"""A real S3 server for the storage tests.

``moto`` in *server* mode, not its decorator. The decorator monkeypatches
botocore, which would leave the parts of ``S3ObjectStorage`` most likely to be
wrong - request signing, the addressing style, HTTP status handling, the
streaming body - completely unexercised, while still printing green.

The server speaks S3 over HTTP on a local port. That is the same relationship
the code has with MinIO in development and with AWS in production, so a bug in
endpoint or signing configuration fails here rather than the first time someone
runs ``make up``. It is the object-storage counterpart of the suite provisioning
a real Postgres rather than mocking SQLAlchemy.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass

from moto.server import ThreadedMotoServer


@dataclass(frozen=True)
class S3Server:
    """Where the test server is listening, and the credentials it accepts."""

    endpoint_url: str
    access_key_id: str = "test-access-key"
    secret_access_key: str = "test-secret-key"  # noqa: S105 - a local fake, not a secret
    region: str = "us-east-1"


def _free_port() -> int:
    """Bind port 0, read what the OS assigned, release it.

    A hard-coded port makes the suite fail when a developer happens to be
    running something else, and moto's default 5000 collides with AirPlay on
    macOS - a failure that costs an hour the first time it is met.
    """
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def run_s3_server() -> Iterator[S3Server]:
    """Start a local S3 server for the duration of the block."""
    port = _free_port()
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
    server.start()
    try:
        yield S3Server(endpoint_url=f"http://127.0.0.1:{port}")
    finally:
        server.stop()
