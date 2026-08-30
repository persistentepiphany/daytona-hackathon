"""Unit tests for the Snapshot web API paper resolver (no network)."""

import json
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
