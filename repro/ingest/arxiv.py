"""arXiv fetch: resolve an id/URL/title query to metadata plus the paper PDF.

Metadata comes from the public Atom API (no key, no account); the PDF comes from
arxiv.org itself. Both are read-only GETs, and neither is load-bearing for the
pipeline — a paper that is already on disk never touches this module.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

API_URL = "http://export.arxiv.org/api/query"
ABS_URL = "https://arxiv.org/abs/{id}"
PDF_URL = "https://arxiv.org/pdf/{id}"
UA = "snapshot-repro/0.1 (reproduction pipeline; contact via repository)"
ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

# 2007-and-later ids (1708.07747, optionally versioned) and the pre-2007 form
# (math/0309136, cs.LG/0309136).
NEW_ID = re.compile(r"\b(\d{4}\.\d{4,5})(v\d+)?\b")
OLD_ID = re.compile(r"\b([a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?\b")


class ArxivError(RuntimeError):
    pass


@dataclass
class ArxivPaper:
    arxiv_id: str          # versioned when the API reports a version
    title: str
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    categories: list[str] = field(default_factory=list)
    published: str = ""
    updated: str = ""
    comment: str = ""
    doi: str = ""

    @property
    def base_id(self) -> str:
        return re.sub(r"v\d+$", "", self.arxiv_id)

    @property
    def abs_url(self) -> str:
        return ABS_URL.format(id=self.arxiv_id)

    @property
    def pdf_url(self) -> str:
        return PDF_URL.format(id=self.arxiv_id)

    def as_dict(self) -> dict:
        return {
            "arxiv_id": self.arxiv_id,
            "title": self.title,
            "authors": self.authors,
            "abstract": self.abstract,
            "categories": self.categories,
            "published": self.published,
            "updated": self.updated,
            "comment": self.comment,
            "doi": self.doi,
            "abs_url": self.abs_url,
            "pdf_url": self.pdf_url,
        }


def parse_arxiv_id(text: str) -> str | None:
    """Pull an arXiv id out of an id, an abs/pdf URL, or a citation string."""
    if not text:
        return None
    s = text.strip()
    s = re.sub(r"(?i)^arxiv[:\s]+", "", s)
    m = NEW_ID.search(s) or OLD_ID.search(s)
    if not m:
        return None
    return m.group(1) + (m.group(2) or "")


def _get(url: str, *, params: dict | None = None, timeout: float = 60.0) -> httpx.Response:
    with httpx.Client(follow_redirects=True, timeout=timeout,
                      headers={"User-Agent": UA}) as client:
        r = client.get(url, params=params)
    r.raise_for_status()
    return r


def _entry_to_paper(entry: ET.Element) -> ArxivPaper:
    def text(path: str) -> str:
        node = entry.find(path, ATOM)
        return " ".join((node.text or "").split()) if node is not None else ""

    raw_id = text("a:id")
    arxiv_id = raw_id.rsplit("/abs/", 1)[-1] if "/abs/" in raw_id else raw_id
    authors = [" ".join((n.text or "").split())
               for n in entry.findall("a:author/a:name", ATOM)]
    categories = [n.attrib.get("term", "") for n in entry.findall("a:category", ATOM)]
    return ArxivPaper(
        arxiv_id=arxiv_id,
        title=text("a:title"),
        authors=[a for a in authors if a],
        abstract=text("a:summary"),
        categories=[c for c in categories if c],
        published=text("a:published"),
        updated=text("a:updated"),
        comment=text("arxiv:comment"),
        doi=text("arxiv:doi"),
    )


def _query(params: dict) -> list[ArxivPaper]:
    body = _get(API_URL, params=params).text
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ArxivError(f"arXiv API returned unparseable Atom: {exc}") from exc
    papers = [_entry_to_paper(e) for e in root.findall("a:entry", ATOM)]
    # a miss comes back as one entry whose id is the error document
    return [p for p in papers if p.arxiv_id and "api/errors" not in p.arxiv_id]


def fetch_metadata(arxiv_id: str) -> ArxivPaper:
    papers = _query({"id_list": arxiv_id, "max_results": 1})
    if not papers:
        raise ArxivError(f"arXiv has no entry for {arxiv_id!r}")
    return papers[0]


def search(query: str, max_results: int = 8) -> list[ArxivPaper]:
    """Title/author/abstract search, newest-relevant first."""
    safe = re.sub(r'[:"()]', " ", query).strip()
    if not safe:
        raise ArxivError("empty arXiv search query")
    return _query({"search_query": f"all:{safe}", "max_results": max_results,
                   "sortBy": "relevance", "sortOrder": "descending"})


def resolve(query: str) -> ArxivPaper:
    """An id, a URL, or free text — resolve to exactly one paper."""
    arxiv_id = parse_arxiv_id(query)
    if arxiv_id:
        return fetch_metadata(arxiv_id)
    hits = search(query, max_results=1)
    if not hits:
        raise ArxivError(f"no arXiv paper matched {query!r}")
    return hits[0]


def download_pdf(paper: ArxivPaper | str, *, timeout: float = 180.0) -> bytes:
    arxiv_id = paper.arxiv_id if isinstance(paper, ArxivPaper) else paper
    r = _get(PDF_URL.format(id=arxiv_id), timeout=timeout)
    data = r.content
    if not data.startswith(b"%PDF"):
        raise ArxivError(f"arXiv did not return a PDF for {arxiv_id} "
                         f"(got {r.headers.get('content-type', 'unknown')})")
    return data
