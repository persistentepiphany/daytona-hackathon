from __future__ import annotations

import hashlib
import ipaddress
import io
import socket
import time
import zipfile
from pathlib import PurePosixPath
from urllib.parse import unquote_plus, urlparse

import httpx


class DatasetUnavailable(RuntimeError):
    pass


# UCI retired these `/ml/datasets/...` landing pages. The planner still emits
# them because papers and search indexes use the old citations. Resolve only
# this reviewed set to UCI's current official archives, then extract the exact
# planner-declared filename on the control plane.
UCI_ARCHIVES = {
    "monk's problems": "https://archive.ics.uci.edu/static/public/70/monk+s+problems.zip",
    "breast cancer wisconsin (original)":
        "https://archive.ics.uci.edu/static/public/15/breast+cancer+wisconsin+original.zip",
    "energy efficiency": "https://archive.ics.uci.edu/static/public/242/energy+efficiency.zip",
    "ilpd (indian liver patient dataset)":
        "https://archive.ics.uci.edu/static/public/225/ilpd+indian+liver+patient+dataset.zip",
    "ozone level detection": "https://archive.ics.uci.edu/static/public/172/ozone+level+detection.zip",
    "statlog (australian credit approval)":
        "https://archive.ics.uci.edu/static/public/143/statlog+australian+credit+approval.zip",
}


def resolve_dataset_source(url: str, filename: str | None = None) -> tuple[str, str | None]:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in {"archive.ics.uci.edu", "www.archive.ics.uci.edu"} and "/ml/datasets/" in parsed.path:
        dataset_name = unquote_plus(parsed.path.rsplit("/", 1)[-1]).lower()
        archive = UCI_ARCHIVES.get(dataset_name)
        if archive:
            if not filename or PurePosixPath(filename).name != filename:
                raise DatasetUnavailable("a plain filename is required for a reviewed UCI archive")
            return archive, filename
    return url, None


def _extract_zip_member(data: bytes, filename: str, max_bytes: int) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            matches = [item for item in archive.infolist()
                       if not item.is_dir() and PurePosixPath(item.filename).name == filename]
            if len(matches) != 1:
                raise DatasetUnavailable(
                    f"official dataset archive contains {len(matches)} files named {filename!r}"
                )
            member = matches[0]
            if member.file_size > max_bytes:
                raise DatasetUnavailable("dataset archive member exceeds configured size limit")
            if member.file_size > 10 * 1024 * 1024 and member.file_size > max(member.compress_size, 1) * 100:
                raise DatasetUnavailable("dataset archive member has an unsafe compression ratio")
            extracted = archive.read(member)
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise DatasetUnavailable(f"cannot read official dataset archive: {exc}") from exc
    if len(extracted) > max_bytes:
        raise DatasetUnavailable("dataset archive member exceeds configured size limit")
    return extracted


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


def fetch_dataset(url: str, *, filename: str | None = None, expected_sha256: str | None = None,
                  max_bytes: int = 2 * 1024 * 1024 * 1024,
                  client: httpx.Client | None = None) -> tuple[bytes, str]:
    resolved_url, archive_member = resolve_dataset_source(url, filename)
    validate_dataset_url(resolved_url)
    owned = client is None
    client = client or httpx.Client(timeout=120, follow_redirects=False,
                                    transport=httpx.HTTPTransport(retries=3))
    try:
        parts: list[bytes] = []
        for attempt in range(4):
            retry = False
            with client.stream("GET", resolved_url) as response:
                if response.status_code in {429, 502, 503, 504} and attempt < 3:
                    retry = True
                else:
                    response.raise_for_status()
                    if 300 <= response.status_code < 400:
                        raise DatasetUnavailable(
                            "dataset redirects must be reviewed and allowlisted explicitly"
                        )
                    declared = int(response.headers.get("content-length", "0") or 0)
                    if declared > max_bytes:
                        raise DatasetUnavailable("dataset exceeds configured size limit")
                    parts, total = [], 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise DatasetUnavailable("dataset exceeds configured size limit")
                        parts.append(chunk)
            if not retry:
                break
            time.sleep(2 ** attempt)
        data = b"".join(parts)
        if archive_member:
            data = _extract_zip_member(data, archive_member, max_bytes)
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
