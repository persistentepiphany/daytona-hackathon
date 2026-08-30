from __future__ import annotations

import hashlib
import ipaddress
import socket
from pathlib import PurePosixPath
from urllib.parse import urlparse

import httpx


class DatasetUnavailable(RuntimeError):
    pass


def validate_dataset_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise DatasetUnavailable("dataset URL must be public HTTPS without embedded credentials")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise DatasetUnavailable(f"dataset host cannot be resolved: {parsed.hostname}") from exc
    for info in addresses:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise DatasetUnavailable("dataset URL resolves to a private or reserved address")


def fetch_dataset(url: str, *, expected_sha256: str | None = None,
                  max_bytes: int = 2 * 1024 * 1024 * 1024,
                  client: httpx.Client | None = None) -> tuple[bytes, str]:
    validate_dataset_url(url)
    owned = client is None
    client = client or httpx.Client(timeout=120, follow_redirects=False)
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            if 300 <= response.status_code < 400:
                raise DatasetUnavailable("dataset redirects must be reviewed and allowlisted explicitly")
            declared = int(response.headers.get("content-length", "0") or 0)
            if declared > max_bytes:
                raise DatasetUnavailable("dataset exceeds configured size limit")
            parts, total = [], 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise DatasetUnavailable("dataset exceeds configured size limit")
                parts.append(chunk)
        data = b"".join(parts)
        digest = hashlib.sha256(data).hexdigest()
        if expected_sha256 and digest.lower() != expected_sha256.lower():
            raise DatasetUnavailable("dataset checksum does not match the declared sha256")
        return data, digest
    finally:
        if owned:
            client.close()


def upload_dataset_to_daytona(adapter, sandbox_id: str, name: str, data: bytes,
                              root: str = "/home/daytona/data") -> str:
    safe_name = PurePosixPath(name).name
    if not safe_name or safe_name in {".", ".."}:
        raise DatasetUnavailable("invalid dataset filename")
    path = f"{root}/{safe_name}"
    result = adapter.exec(sandbox_id, f"mkdir -p '{root}'")
    if result.exit_code != 0:
        raise DatasetUnavailable(f"cannot prepare Daytona data directory: {result.output[-300:]}")
    adapter.write_file(sandbox_id, path, data)
    digest = hashlib.sha256(data).hexdigest()
    check = adapter.exec(sandbox_id, f"printf '%s  %s\\n' '{digest}' '{path}' | sha256sum -c --strict -")
    if check.exit_code != 0:
        raise DatasetUnavailable("Daytona dataset checksum verification failed")
    return path
