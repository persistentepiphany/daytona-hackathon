"""Unit tests for the Snapshot web API paper resolver (no network)."""

import json
import time
from pathlib import Path

import pytest

from repro import web_api as api


def test_list_papers_includes_fashion(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "PAPERS", Path("papers"))
    papers = api.list_papers()
    slugs = {p["slug"] for p in papers}
    assert "fashion-mnist" in slugs
    assert "dnn-pattern-recognition" in slugs


def test_match_paper_aliases():
    assert api._match_paper("please reproduce fashion-mnist") == "fashion-mnist"
    assert api._match_paper("check 1708.07747") == "fashion-mnist"


def test_resolve_known_slug():
    d, meta, title = api.resolve_paper_request({"paper_slug": "fashion-mnist"})
    assert d.name == "fashion-mnist"
    assert "Fashion-MNIST" in title
    assert meta["paper_id"] == "fashion-mnist"


def test_materialize_paste(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "INBOX", tmp_path / "_inbox")
    monkeypatch.setattr(api, "PAPERS", tmp_path)
    text = "Title Line About Accuracy\n\n" + ("We report accuracy 0.91 on a held-out set. " * 40)
    d, meta, title = api.resolve_paper_request({"paper_text": text, "title": "Paste Paper"})
    assert d.is_dir()
    assert (d / "paper-extract.txt").read_text().startswith("Title Line")
    assert json.loads((d / "code_absence.json").read_text())["results"] == []
    assert meta["title"] == "Paste Paper"
    assert title == "Paste Paper"


def test_resolve_rejects_short_message():
    with pytest.raises(ValueError):
        api.resolve_paper_request({"message": "hi"})


# ---- paper ingest ----------------------------------------------------------

def test_list_papers_carries_the_manifest_shape(monkeypatch):
    monkeypatch.setattr(api, "PAPERS", Path("papers"))
    fashion = next(p for p in api.list_papers() if p["slug"] == "fashion-mnist")
    assert fashion["paper_dir"] == "papers/fashion-mnist"
    assert "figures" in fashion and "code_absence" in fashion


def test_upload_rejects_what_is_not_a_pdf():
    with pytest.raises(ValueError, match="not a PDF"):
        api.start_pdf_upload(b"<html>nope</html>", title_hint="x.pdf")
    with pytest.raises(ValueError, match="empty"):
        api.start_pdf_upload(b"")


def test_upload_rejects_an_oversized_pdf():
    with pytest.raises(ValueError, match="cap"):
        api.start_pdf_upload(b"%PDF" + b"0" * (api.ingest.MAX_PDF_BYTES + 1))


def test_arxiv_fetch_needs_a_query():
    with pytest.raises(ValueError, match="query is required"):
        api.start_arxiv_fetch({})


def test_ingest_store_records_success_and_failure():
    store = api.IngestStore()
    started = store.start("upload", "ok.pdf", lambda log: (log("scanning"), {"slug": "x"})[1])
    for _ in range(100):
        record = store.get(started["ingest_id"])
        if record["status"] not in ("queued", "running"):
            break
        time.sleep(0.02)
    assert record["status"] == "succeeded"
    assert record["manifest"] == {"slug": "x"}
    assert any("scanning" in line for line in record["log"])

    def boom(log):
        raise api.ingest.IngestError("no such paper")

    failed = store.start("arxiv", "9999.99999", boom)
    for _ in range(100):
        record = store.get(failed["ingest_id"])
        if record["status"] not in ("queued", "running"):
            break
        time.sleep(0.02)
    assert record["status"] == "failed"
    assert record["error"] == "no such paper"
    assert len(store.list()) == 2


def test_read_figure_refuses_to_leave_the_paper_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "PAPERS", tmp_path)
    paper = tmp_path / "demo"
    (paper / "figures").mkdir(parents=True)
    (paper / "figures" / "01-figure-1.png").write_bytes(b"\x89PNG-bytes")
    (tmp_path / "secret.json").write_text("{}")

    assert api.read_figure("demo", "01-figure-1.png") == b"\x89PNG-bytes"
    assert api.read_figure("demo", "../../secret.json") is None
    assert api.read_figure("demo", "missing.png") is None
    assert api.read_figure("../..", "01-figure-1.png") is None
