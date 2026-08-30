"""Paper intake: an arXiv id or an uploaded PDF in, a pipeline-ready paper dir out.

The autonomous driver reads exactly three files from a paper directory —
`paper.json`, `paper-extract.txt` and `code_absence.json` — so that is what this
package writes, alongside the PDF and the figure crops it took the text from. A
directory produced here is indistinguishable to `scripts/auto_run.py` from one
committed by hand; nothing downstream needs to know a paper arrived this way.

The code-existence certificate is deliberately left *unsearched* at intake time
(`status: NOT_SEARCHED`). Certification is a gate the run performs, and faking a
NOT_FOUND here would forge the wedge criterion. `certify_code_absence` performs
the real search when a Parallel key is present and the caller asks for it.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from pathlib import Path

from . import figures as figure_scan
from . import pdf as pdf_scan

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PAPERS = ROOT / "papers"
MAX_PDF_BYTES = 40 * 1024 * 1024
STOPWORDS = {"a", "an", "the", "of", "for", "and", "on", "in", "to", "with", "via"}

LogFn = Callable[[str], None]


class IngestError(RuntimeError):
    pass


def _noop(_msg: str) -> None:
    return None


def slugify(text: str, *, max_words: int = 6) -> str:
    words = [w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if w]
    kept = [w for w in words if w not in STOPWORDS][:max_words] or words[:max_words]
    return "-".join(kept)[:56].strip("-")


def unique_slug(papers_dir: Path, base: str, *, hint: str = "") -> str:
    base = base or "paper"
    if not (papers_dir / base).exists():
        return base
    if hint:
        salted = f"{base}-{slugify(hint, max_words=3)}"
        if not (papers_dir / salted).exists():
            return salted
    for n in range(2, 100):
        candidate = f"{base}-{n}"
        if not (papers_dir / candidate).exists():
            return candidate
    return f"{base}-{int(time.time())}"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ingest_pdf(
    data: bytes,
    *,
    papers_dir: Path | str = DEFAULT_PAPERS,
    title: str | None = None,
    title_hint: str | None = None,
    slug: str | None = None,
    source: str = "upload",
    arxiv: dict | None = None,
    scan_figures: bool = True,
    max_figures: int = 12,
    log: LogFn | None = None,
) -> dict:
    """Scan a PDF and write the paper directory the pipeline consumes.

    `title` overrides what the scan reads off the page; `title_hint` (a filename,
    say) is used only when the scan finds nothing.
    """
    log = log or _noop
    if len(data) > MAX_PDF_BYTES:
        raise IngestError(f"PDF is {len(data) // 1024 // 1024} MB; the cap is "
                          f"{MAX_PDF_BYTES // 1024 // 1024} MB")
    papers_dir = Path(papers_dir)
    papers_dir.mkdir(parents=True, exist_ok=True)

    log(f"scanning PDF ({len(data) // 1024} KB)")
    try:
        extract = pdf_scan.extract(data, max_figures=max_figures)
    except pdf_scan.PdfError as exc:
        raise IngestError(str(exc)) from exc
    if len(extract.text.strip()) < 500:
        raise IngestError(
            "extracted under 500 characters of text — the PDF is probably a scan; "
            "run OCR over it first")
    log(f"extracted {len(extract.text)} chars from {extract.pages} pages, "
        f"{len(extract.figures)} figures ({extract.backend})")

    # an explicit title wins; a filename is only a hint, and loses to the title
    # the scanner read off the first page
    resolved_title = (title or (arxiv or {}).get("title") or extract.title_guess
                      or title_hint or "Untitled paper").strip()
    slug = _safe_slug(slug) if slug else unique_slug(
        papers_dir, slugify(resolved_title),
        hint=(arxiv or {}).get("arxiv_id", ""))
    dest = papers_dir / slug
    dest.mkdir(parents=True, exist_ok=True)

    scans: list[dict] = []
    if scan_figures and extract.figures:
        scans = figure_scan.scan_figures(extract.figures, paper_title=resolved_title,
                                         log=log, max_figures=max_figures)
    else:
        scans = [dict(f.meta(), reading="", scanned=False,
                      error=None if extract.figures else "no figures found")
                 for f in extract.figures]

    fig_dir = dest / "figures"
    if extract.figures:
        fig_dir.mkdir(exist_ok=True)
    for fig, record in zip(extract.figures, scans):
        if not fig.png:
            continue
        name = f"{fig.index:02d}-{slugify(fig.label, max_words=3) or 'figure'}.png"
        (fig_dir / name).write_bytes(fig.png)
        record["file"] = f"figures/{name}"

    body = extract.text.rstrip() + "\n" + figure_scan.figures_appendix(scans)
    (dest / "paper-extract.txt").write_text(body)
    (dest / "paper.pdf").write_bytes(data)
    (dest / "figures.json").write_text(json.dumps(
        {"paper_id": slug, "backend": extract.backend, "figures": scans}, indent=2))

    meta = {
        "paper_id": slug,
        "title": resolved_title,
        "authors": (arxiv or {}).get("authors") or [],
        "pdf_sha256": hashlib.sha256(data).hexdigest(),
        "role": "user",
        "source": source,
        "ingested_at": _now(),
        "pages": extract.pages,
        "extract_chars": len(body),
        "pdf_backend": extract.backend,
        "figures": {
            "found": len(extract.figures),
            "scanned": sum(1 for s in scans if s.get("scanned")),
            "reader": next((s.get("reader_model") for s in scans if s.get("scanned")), ""),
        },
        "notes": ("Ingested by repro.ingest: text and figure regions extracted from the "
                  "PDF; figure readings, where present, are machine readings marked as "
                  "such in paper-extract.txt."),
    }
    if arxiv:
        meta.update({k: v for k, v in arxiv.items()
                     if k in ("arxiv_id", "abstract", "categories", "published",
                              "updated", "doi", "comment", "abs_url", "pdf_url")})
    (dest / "paper.json").write_text(json.dumps(meta, indent=2))

    if not (dest / "code_absence.json").is_file():
        (dest / "code_absence.json").write_text(json.dumps({
            "title": resolved_title,
            "objective": ("Determine whether any official or third-party source code "
                          f"release exists for the paper '{resolved_title}'."),
            "queries": [],
            "results": [],
            "status": "NOT_SEARCHED: certification not run at ingest",
        }, indent=2))

    log(f"paper dir ready: papers/{slug}")
    return manifest(dest, papers_dir=papers_dir)


def ingest_arxiv(
    query: str,
    *,
    papers_dir: Path | str = DEFAULT_PAPERS,
    scan_figures: bool = True,
    max_figures: int = 12,
    log: LogFn | None = None,
) -> dict:
    """Resolve an arXiv id, URL or title, download the PDF, and ingest it."""
    from . import arxiv as arxiv_api

    log = log or _noop
    query = (query or "").strip()
    if not query:
        raise IngestError("no arXiv id, URL or search text given")
    try:
        paper = arxiv_api.resolve(query)
        log(f"arXiv {paper.arxiv_id}: {paper.title}")
        data = arxiv_api.download_pdf(paper)
    except arxiv_api.ArxivError as exc:
        raise IngestError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - network shape varies by client
        raise IngestError(f"arXiv fetch failed: {type(exc).__name__}: {exc}") from exc

    existing = find_by_arxiv_id(paper.base_id, papers_dir=papers_dir)
    if existing:
        log(f"already ingested as papers/{existing.name}")
        return manifest(existing, papers_dir=papers_dir)

    return ingest_pdf(
        data, papers_dir=papers_dir, title=paper.title,
        slug=None, source="arxiv", arxiv=paper.as_dict(),
        scan_figures=scan_figures, max_figures=max_figures, log=log,
    )


def find_by_arxiv_id(arxiv_id: str, *, papers_dir: Path | str = DEFAULT_PAPERS) -> Path | None:
    papers_dir = Path(papers_dir)
    if not arxiv_id or not papers_dir.is_dir():
        return None
    wanted = re.sub(r"v\d+$", "", arxiv_id)
    for d in sorted(papers_dir.iterdir()):
        meta_path = d / "paper.json"
        if not d.is_dir() or not meta_path.is_file():
            continue
        try:
            got = json.loads(meta_path.read_text()).get("arxiv_id") or ""
        except (OSError, ValueError):
            continue
        if re.sub(r"v\d+$", "", got) == wanted:
            return d
    return None


def manifest(paper_dir: Path | str, *, papers_dir: Path | str = DEFAULT_PAPERS) -> dict:
    """The summary the API and the CLI both report."""
    paper_dir = Path(paper_dir)
    papers_dir = Path(papers_dir)
    meta = json.loads((paper_dir / "paper.json").read_text())
    extract = paper_dir / "paper-extract.txt"
    figures_path = paper_dir / "figures.json"
    scans = []
    if figures_path.is_file():
        try:
            scans = json.loads(figures_path.read_text()).get("figures", [])
        except ValueError:
            scans = []
    try:
        rel = paper_dir.relative_to(papers_dir.parent).as_posix()
    except ValueError:
        rel = f"papers/{paper_dir.name}"
    return {
        "slug": paper_dir.name,
        "paper_dir": rel,
        "title": meta.get("title") or paper_dir.name,
        "authors": meta.get("authors") or [],
        "arxiv_id": meta.get("arxiv_id"),
        "abs_url": meta.get("abs_url"),
        "abstract": meta.get("abstract", ""),
        "source": meta.get("source", "committed"),
        "pages": meta.get("pages"),
        "chars": extract.stat().st_size if extract.is_file() else 0,
        "ready": (paper_dir / "code_absence.json").is_file() and extract.is_file(),
        "has_pdf": (paper_dir / "paper.pdf").is_file(),
        "code_absence": _code_absence_status(paper_dir),
        "figures": [
            {k: s.get(k) for k in ("index", "page", "label", "caption", "file",
                                   "scanned", "reading", "error")}
            for s in scans
        ],
    }


def _code_absence_status(paper_dir: Path) -> str:
    path = paper_dir / "code_absence.json"
    if not path.is_file():
        return "MISSING"
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return "UNREADABLE"
    if doc.get("results"):
        return f"{len(doc['results'])} result(s) on record"
    return doc.get("status") or "no results on record"


def certify_code_absence(paper_dir: Path | str, *, log: LogFn | None = None) -> dict:
    """Run the real code-existence search for a paper and store the certificate.

    Uses the same Parallel client and the same ledger the intake gate uses, so the
    certificate a run later reads is one that was actually earned. Without a key
    the certificate keeps its NOT_SEARCHED status.
    """
    from ..orchestrator.budget import Budget
    from ..orchestrator.ledger import Ledger
    from ..orchestrator.parallel_client import ParallelClient

    log = log or _noop
    paper_dir = Path(paper_dir)
    meta = json.loads((paper_dir / "paper.json").read_text())
    run_id = f"ingest-{paper_dir.name}-{int(time.time())}"
    ledger_path = ROOT / "runs" / "auto" / "ledger.db"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(ledger_path)
    client = ParallelClient(ledger, run_id, Budget(ledger, run_id, {"parallel_calls": 3}))
    certificate = client.code_absence_certification(
        meta.get("title") or paper_dir.name, meta.get("authors") or ["unknown"])
    (paper_dir / "code_absence.json").write_text(json.dumps(certificate, indent=2))
    log(f"code-absence certification: {certificate.get('status')} "
        f"({len(certificate.get('results', []))} results)")
    return certificate


def _safe_slug(slug: str) -> str:
    cleaned = slugify(slug, max_words=8)
    if not cleaned:
        raise IngestError(f"unusable paper slug: {slug!r}")
    return cleaned


def list_papers(papers_dir: Path | str = DEFAULT_PAPERS) -> list[dict]:
    """Every ingested or committed paper the pipeline can be pointed at."""
    papers_dir = Path(papers_dir)
    if not papers_dir.is_dir():
        return []
    out = []
    for d in sorted(papers_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        if not (d / "paper.json").is_file() or not (d / "paper-extract.txt").is_file():
            continue
        try:
            out.append(manifest(d, papers_dir=papers_dir))
        except (OSError, ValueError):
            continue
    out.sort(key=lambda m: (m["source"] == "committed", m["title"].lower()))
    return out
