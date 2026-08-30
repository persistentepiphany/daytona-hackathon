from __future__ import annotations

import hashlib
import io
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx
from pypdf import PdfReader


ARXIV_ID_RE = re.compile(r"^(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?$", re.I)
ARXIV_URL_RE = re.compile(
    r"^https?://(?:www\.)?(?:arxiv\.org|export\.arxiv\.org)/(?:abs|pdf)/([^?#]+?)(?:\.pdf)?/?$",
    re.I,
)
ATOM = {"a": "http://www.w3.org/2005/Atom"}
USER_AGENT = os.environ.get("ARXIV_USER_AGENT", "Snapshot-Reproduction/1.0")


class ArxivInputError(ValueError):
    pass


class PdfValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ExtractedPdf:
    text: str
    pages: int
    pdf_sha256: str
    text_sha256: str
    needs_ocr: bool


def normalize_arxiv_id(value: str) -> str:
    candidate = value.strip()
    match = ARXIV_URL_RE.fullmatch(candidate)
    if match:
        candidate = match.group(1)
    candidate = candidate.removesuffix(".pdf").strip("/")
    if not ARXIV_ID_RE.fullmatch(candidate):
        raise ArxivInputError("expected an arXiv ID or an arxiv.org abs/pdf URL")
    return candidate


def fetch_metadata(arxiv_id: str, client: httpx.Client | None = None) -> dict:
    canonical = normalize_arxiv_id(arxiv_id)
    owned = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=True,
                                    headers={"User-Agent": USER_AGENT},
                                    transport=httpx.HTTPTransport(retries=3))
    try:
        response = client.get("https://export.arxiv.org/api/query", params={"id_list": canonical},
                              headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        root = ET.fromstring(response.content)
        entry = root.find("a:entry", ATOM)
        if entry is None:
            raise ArxivInputError(f"arXiv returned no paper for {canonical}")
        title = " ".join((entry.findtext("a:title", default="", namespaces=ATOM)).split())
        authors = [" ".join((node.findtext("a:name", default="", namespaces=ATOM)).split())
                   for node in entry.findall("a:author", ATOM)]
        return {"arxiv_id": canonical, "title": title, "authors": [a for a in authors if a],
                "abstract": " ".join(entry.findtext("a:summary", default="", namespaces=ATOM).split()),
                "published": entry.findtext("a:published", default=None, namespaces=ATOM),
                "source_url": f"https://arxiv.org/abs/{canonical}"}
    finally:
        if owned:
            client.close()


def fetch_pdf(arxiv_id: str, max_bytes: int, client: httpx.Client | None = None) -> bytes:
    canonical = normalize_arxiv_id(arxiv_id)
    owned = client is None
    client = client or httpx.Client(timeout=90, follow_redirects=True,
                                    headers={"User-Agent": USER_AGENT},
                                    transport=httpx.HTTPTransport(retries=3))
    try:
        with client.stream("GET", f"https://arxiv.org/pdf/{canonical}",
                           headers={"User-Agent": USER_AGENT}) as response:
            response.raise_for_status()
            declared = int(response.headers.get("content-length", "0") or 0)
            if declared > max_bytes:
                raise PdfValidationError(f"PDF exceeds the {max_bytes} byte limit")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise PdfValidationError(f"PDF exceeds the {max_bytes} byte limit")
                chunks.append(chunk)
        return b"".join(chunks)
    finally:
        if owned:
            client.close()


def extract_pdf(data: bytes, min_chars_per_page: int = 80) -> ExtractedPdf:
    if not data.startswith(b"%PDF-"):
        raise PdfValidationError("file is not a PDF")
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        parts = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as exc:
        raise PdfValidationError(f"cannot parse PDF: {exc}") from exc
    text = "\n\n".join(part for part in parts if part).strip()
    pages = len(reader.pages)
    needs_ocr = not text or len(text) < max(400, pages * min_chars_per_page)
    return ExtractedPdf(
        text=text,
        pages=pages,
        pdf_sha256=hashlib.sha256(data).hexdigest(),
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        needs_ocr=needs_ocr,
    )
