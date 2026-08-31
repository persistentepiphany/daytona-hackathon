import json
import socket
import time
import io
import zipfile
from dataclasses import replace

import httpx
import pytest
from pypdf import PdfWriter
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from repro.service.arxiv import ArxivInputError, extract_pdf, fetch_metadata, normalize_arxiv_id
from repro.service.config import settings
from repro.service.code_search import code_release_candidates
from repro.service.data_staging import (DatasetUnavailable, fetch_dataset,
                                        resolve_dataset_source, validate_dataset_url)
from repro.service.github_publish import GitHubPublishError, GitHubPublisher, repo_slug
from repro.service.packaging import UnsafeArtifact, collect_run_artifacts
from repro.service.models import Base, EphemeralBlob, Paper
from repro.service.object_store import ObjectStore, ObjectStoreError
from repro.service.repository import seed_bundled_papers
from repro.service.tasks import _with_paper_identity
from repro.auto.contract import required_data_requirements


@pytest.mark.parametrize("value,expected", [
    ("1708.07747", "1708.07747"),
    ("1708.07747v2", "1708.07747v2"),
    ("https://arxiv.org/abs/1708.07747", "1708.07747"),
    ("https://arxiv.org/pdf/1708.07747.pdf", "1708.07747"),
    ("hep-th/9901001", "hep-th/9901001"),
])
def test_arxiv_inputs_are_canonicalized(value, expected):
    assert normalize_arxiv_id(value) == expected


@pytest.mark.parametrize("value", [
    "https://example.com/abs/1708.07747",
    "http://127.0.0.1/paper.pdf",
    "../../etc/passwd",
    "1708.07747?download=1",
])
def test_arxiv_input_rejects_arbitrary_urls_and_paths(value):
    with pytest.raises(ArxivInputError):
        normalize_arxiv_id(value)


def test_arxiv_metadata_is_fetched_from_official_api():
    atom = b'''<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title> A useful paper </title><summary> Abstract text. </summary>
      <author><name>Ada Researcher</name></author><published>2026-01-01T00:00:00Z</published>
    </entry></feed>'''

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "export.arxiv.org"
        assert request.url.path == "/api/query"
        assert request.url.params["id_list"] == "1708.07747"
        assert request.headers["user-agent"].startswith("Snapshot-Reproduction/")
        return httpx.Response(200, content=atom)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        metadata = fetch_metadata("https://arxiv.org/abs/1708.07747", client)
    assert metadata["title"] == "A useful paper"
    assert metadata["authors"] == ["Ada Researcher"]


def test_code_gate_ignores_paper_pages_and_unrelated_repositories():
    results = [
        {"url": "https://arxiv.org/abs/1905.11028", "title": "Best-scored Random Forest Classification"},
        {"url": "https://github.com/newton-physics/newton/blob/main/LICENSE.md",
         "title": "newton/LICENSE.md"},
        {"url": "https://github.com/example/random-forest",
         "title": "Random Forest Classification tutorial"},
    ]
    assert code_release_candidates(
        results, "A New Modified Newton Method use of Haar wavelet", ["Bijaya Mishra"]
    ) == []


def test_code_gate_promotes_relevant_code_host_results():
    result = {"url": "https://github.com/zalandoresearch/fashion-mnist",
              "title": "Fashion-MNIST benchmark implementation"}
    assert code_release_candidates(
        [result], "Fashion-MNIST: a Novel Image Dataset", ["Han Xiao"]
    ) == [result]


def test_sparse_pdf_is_preserved_but_requires_ocr(tmp_path):
    path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with path.open("wb") as handle:
        writer.write(handle)
    result = extract_pdf(path.read_bytes())
    assert result.pages == 1
    assert result.needs_ocr is True
    assert len(result.pdf_sha256) == len(result.text_sha256) == 64
    assert result.pdf_sha256 != result.text_sha256


def test_non_pdf_is_rejected():
    with pytest.raises(ValueError, match="not a PDF"):
        extract_pdf(b"definitely not a pdf")


def test_temporary_database_objects_are_shared_and_expiring(tmp_path):
    config = replace(
        settings,
        database_url=f"sqlite:///{tmp_path / 'shared-blobs.db'}",
        object_backend="database",
        ephemeral_blob_ttl_hours=1,
        max_database_blob_bytes=1024,
    )
    EphemeralBlob.__table__.create(create_engine(config.database_url))
    web_store = ObjectStore(config)
    worker_store = ObjectStore(config)
    assert web_store.is_shared and web_store.is_ephemeral
    digest = web_store.put_bytes("papers/paper-1/paper.pdf", b"%PDF-test", "application/pdf")

    assert worker_store.get_bytes("papers/paper-1/paper.pdf") == b"%PDF-test"
    head = worker_store.head("papers/paper-1/paper.pdf")
    assert head["size"] == 9
    assert head["content_type"] == "application/pdf"
    assert head["expires_at"] > time.time()
    assert len(digest) == 64

    worker_store.delete("papers/paper-1/paper.pdf")
    assert not web_store.exists("papers/paper-1/paper.pdf")


def test_temporary_database_objects_enforce_size_and_ttl(tmp_path):
    base = replace(
        settings,
        database_url=f"sqlite:///{tmp_path / 'bounded-blobs.db'}",
        object_backend="database",
        ephemeral_blob_ttl_hours=1,
        max_database_blob_bytes=3,
    )
    EphemeralBlob.__table__.create(create_engine(base.database_url))
    bounded = ObjectStore(base)
    with pytest.raises(ObjectStoreError, match="temporary database limit"):
        bounded.put_bytes("too-large", b"four")

    immediately_expiring = ObjectStore(replace(base, ephemeral_blob_ttl_hours=0,
                                               max_database_blob_bytes=1024))
    immediately_expiring.put_bytes("expired", b"data")
    assert not immediately_expiring.exists("expired")


def test_dataset_ssrf_guard_rejects_private_resolution(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ])
    with pytest.raises(DatasetUnavailable, match="private or reserved"):
        validate_dataset_url("https://data.example/dataset.zip")


def test_dataset_ssrf_guard_requires_https():
    with pytest.raises(DatasetUnavailable, match="public HTTPS"):
        validate_dataset_url("http://data.example/dataset.zip")


def test_reviewed_uci_landing_page_resolves_to_exact_archive_member():
    url, member = resolve_dataset_source(
        "https://archive.ics.uci.edu/ml/datasets/MONK%27s+Problems", "monks-2.train"
    )
    assert url == "https://archive.ics.uci.edu/static/public/70/monk+s+problems.zip"
    assert member == "monks-2.train"


def test_uci_archive_extracts_only_declared_file(monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("nested/monks-2.train", b"verified rows")
        archive.writestr("nested/other.txt", b"not selected")

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/static/public/70/monk+s+problems.zip"
        return httpx.Response(200, content=buffer.getvalue())

    monkeypatch.setattr("repro.service.data_staging.validate_dataset_url", lambda _url: None)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        data, digest = fetch_dataset(
            "https://archive.ics.uci.edu/ml/datasets/MONK%27s+Problems",
            filename="monks-2.train", client=client,
        )
    assert data == b"verified rows"
    assert len(digest) == 64


def test_dataset_download_retries_transient_gateway_failure(monkeypatch):
    attempts = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return (httpx.Response(502) if attempts == 1
                else httpx.Response(200, content=b"verified dataset"))

    monkeypatch.setattr("repro.service.data_staging.validate_dataset_url", lambda _url: None)
    monkeypatch.setattr("repro.service.data_staging.time.sleep", lambda _seconds: None)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        data, _digest = fetch_dataset("https://data.example/dataset.csv", client=client)
    assert attempts == 2
    assert data == b"verified dataset"


def test_artifact_package_excludes_pdf_env_and_binary(tmp_path):
    (tmp_path / "report.md").write_text("safe")
    (tmp_path / "paper.pdf").write_bytes(b"%PDF-secret")
    (tmp_path / ".env").write_text("TOKEN=value")
    (tmp_path / "weights.bin").write_bytes(b"weights")
    files = collect_run_artifacts(tmp_path)
    assert set(files) == {"report.md", "artifact-manifest.json"}
    manifest = json.loads(files["artifact-manifest.json"])
    assert manifest["report.md"]["size"] == 4


def test_artifact_package_refuses_secret_like_content(tmp_path):
    (tmp_path / "report.md").write_text("token ghp_abcdefghijklmnopqrstuvwxyz123456")
    with pytest.raises(UnsafeArtifact, match="secret-like"):
        collect_run_artifacts(tmp_path)


def test_github_repo_name_is_stable_and_safe():
    assert repo_slug("A Paper: With Symbols!", "1708.07747") == \
        "snapshot-1708.07747-a-paper-with-symbols"


def test_github_publisher_refuses_existing_public_repository():
    config = replace(settings, github_token="app-user-token", github_owner="persistentepiphany")

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/persistentepiphany/evidence"
        return httpx.Response(200, json={
            "id": 1, "private": False, "owner": {"login": "persistentepiphany"},
            "html_url": "https://github.com/persistentepiphany/evidence",
        })

    client = httpx.Client(base_url="https://api.github.test", transport=httpx.MockTransport(respond))
    publisher = GitHubPublisher(config, client)
    with pytest.raises(GitHubPublishError, match="public repository"):
        publisher.ensure_private_repo("evidence", "test")


def test_stage_catalog_names_every_public_gate():
    from repro.service.events import STAGE_DESCRIPTIONS

    assert list(STAGE_DESCRIPTIONS) == [
        "INGEST", "EXTRACT", "PREFLIGHT", "G1", "P1", "P2", "P3", "P4",
        "PACKAGE", "G3", "GITHUB_PUBLISH",
    ]
    assert "Daytona" in STAGE_DESCRIPTIONS["P1"] or "environment" in STAGE_DESCRIPTIONS["P1"]


def test_bundled_paper_with_mismatched_pdf_hash_is_not_runnable():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_bundled_papers(session)
        session.commit()
        paper = session.get(Paper, "bundled-dnn-pattern-recognition")
        assert paper.status == "failed"
        assert "hash does not match" in paper.status_detail
        arxiv_ids = [value for value in session.scalars(select(Paper.arxiv_id)).all() if value]
        assert len(arxiv_ids) == len(set(arxiv_ids))


def test_service_paper_metadata_gets_preregistration_identity():
    paper = Paper(id="paper-live", source="arxiv", title="A paper",
                  pdf_sha256="a" * 64)
    metadata = _with_paper_identity(paper, {"title": "A paper"})
    assert metadata["paper_id"] == "paper-live"
    assert metadata["pdf_sha256"] == "a" * 64


def test_optional_dataset_placeholder_is_not_staged():
    proposal = {"data_requirements": [
        {"id": "none", "url": None, "filename": "na", "required": False},
        {"id": "real", "url": "https://data.example/real.csv", "required": True},
    ]}
    assert required_data_requirements(proposal) == [
        {"id": "real", "url": "https://data.example/real.csv", "required": True}
    ]
