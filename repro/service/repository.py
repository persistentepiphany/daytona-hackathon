from __future__ import annotations

import json
import hashlib
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import REPO
from .models import Paper


def seed_bundled_papers(session: Session) -> None:
    root = REPO / "papers"
    if not root.is_dir():
        return
    for directory in root.iterdir():
        meta_path = directory / "paper.json"
        text_path = directory / "paper-extract.txt"
        if not directory.is_dir() or not meta_path.is_file() or not text_path.is_file():
            continue
        paper_id = f"bundled-{directory.name}"
        existing = session.get(Paper, paper_id)
        meta = json.loads(meta_path.read_text())
        arxiv_id = meta.get("arxiv_id")
        duplicate = (session.scalar(select(Paper).where(Paper.arxiv_id == arxiv_id,
                                                        Paper.id != paper_id))
                     if arxiv_id else None)
        detail = None
        if duplicate:
            # A bundled fixture may contain stale metadata. Do not let it claim
            # an arXiv identity already owned by a different title.
            detail = f"Bundled metadata arXiv ID conflicts with {duplicate.id}; identity omitted."
            duplicate.arxiv_id = None
            duplicate.status_detail = f"Bundled metadata arXiv ID conflicts with {paper_id}; identity omitted."
            arxiv_id = None
        status = "ready"
        pdf_path = directory / "paper.pdf"
        if pdf_path.is_file():
            observed = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
            declared = meta.get("pdf_sha256")
            if declared and observed != declared:
                status = "failed"
                detail = "Bundled PDF hash does not match paper.json; provenance must be repaired."
        paper = existing or Paper(id=paper_id, source="bundled", created_at=time.time())
        paper.source_ref = str(directory.relative_to(REPO))
        paper.arxiv_id = arxiv_id
        paper.title = meta.get("title", directory.name)
        paper.authors_json = json.dumps(meta.get("authors", []))
        paper.status = status
        paper.status_detail = detail
        paper.pdf_sha256 = meta.get("pdf_sha256")
        paper.chars = len(text_path.read_text())
        paper.updated_at = time.time()
        session.add(paper)


def paper_dict(paper: Paper) -> dict:
    return {"paper_id": paper.id, "slug": paper.id.removeprefix("bundled-"),
            "source": paper.source, "source_ref": paper.source_ref,
            "arxiv_id": paper.arxiv_id, "title": paper.title,
            "authors": json.loads(paper.authors_json or "[]"), "status": paper.status,
            "status_detail": paper.status_detail, "ready": paper.status == "ready",
            "chars": paper.chars, "pdf_sha256": paper.pdf_sha256,
            "text_sha256": paper.text_sha256, "created_at": paper.created_at,
            "updated_at": paper.updated_at}
