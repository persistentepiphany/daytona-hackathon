import json
import socket
from dataclasses import replace

import httpx
import pytest
from pypdf import PdfWriter
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from repro.service.arxiv import ArxivInputError, extract_pdf, normalize_arxiv_id
from repro.service.config import settings
from repro.service.data_staging import DatasetUnavailable, validate_dataset_url
from repro.service.github_publish import GitHubPublishError, GitHubPublisher, repo_slug
from repro.service.packaging import UnsafeArtifact, collect_run_artifacts
from repro.service.models import Base, Paper
from repro.service.repository import seed_bundled_papers


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


def test_dataset_ssrf_guard_rejects_private_resolution(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ])
    with pytest.raises(DatasetUnavailable, match="private or reserved"):
        validate_dataset_url("https://data.example/dataset.zip")


def test_dataset_ssrf_guard_requires_https():
    with pytest.raises(DatasetUnavailable, match="public HTTPS"):
        validate_dataset_url("http://data.example/dataset.zip")


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
