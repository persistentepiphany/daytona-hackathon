"""Unit tests for paper ingest: arXiv resolution, PDF scan, paper-dir writing.

Everything here is offline. The arXiv HTTP calls are exercised by parsing a
recorded Atom document; the PDF scan runs against the committed paper.
"""

import json
from pathlib import Path

import pytest

from repro import ingest
from repro.ingest import arxiv, figures, pdf

COMMITTED_PDF = Path("papers/dnn-pattern-recognition/paper.pdf")

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/1708.07747v2</id>
    <published>2017-08-25T15:39:00Z</published>
    <updated>2017-09-15T11:00:00Z</updated>
    <title>Fashion-MNIST: a Novel Image Dataset for Benchmarking
      Machine Learning Algorithms</title>
    <summary>We present Fashion-MNIST, a new dataset.</summary>
    <author><name>Han Xiao</name></author>
    <author><name>Kashif Rasul</name></author>
    <category term="cs.LG"/>
    <category term="stat.ML"/>
    <arxiv:comment>Dataset is freely available</arxiv:comment>
  </entry>
</feed>"""


def has_pdf_backend() -> bool:
    try:
        pdf._backend()
    except pdf.PdfError:
        return False
    return True


needs_pdf = pytest.mark.skipif(not has_pdf_backend(),
                               reason="no PDF backend installed (pymupdf or pypdf)")


# ---- arXiv id resolution ---------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("1708.07747", "1708.07747"),
    ("arXiv:1708.07747v2", "1708.07747v2"),
    ("https://arxiv.org/abs/1905.11028", "1905.11028"),
    ("https://arxiv.org/pdf/1708.07747v2", "1708.07747v2"),
    ("math/0309136", "math/0309136"),
    ("cs.LG/0309136v3", "cs.LG/0309136v3"),
    ("attention is all you need", None),
    ("", None),
])
def test_parse_arxiv_id(text, expected):
    assert arxiv.parse_arxiv_id(text) == expected


def test_atom_parsing_yields_one_paper(monkeypatch):
    class Recorded:
        text = ATOM

        def raise_for_status(self):
            return None

    monkeypatch.setattr(arxiv, "_get", lambda *a, **k: Recorded())
    paper = arxiv.fetch_metadata("1708.07747")
    assert paper.arxiv_id == "1708.07747v2"
    assert paper.base_id == "1708.07747"
    assert paper.title.startswith("Fashion-MNIST")
    assert paper.authors == ["Han Xiao", "Kashif Rasul"]
    assert paper.categories == ["cs.LG", "stat.ML"]
    assert paper.pdf_url == "https://arxiv.org/pdf/1708.07747v2"
    assert paper.as_dict()["abs_url"].endswith("1708.07747v2")


def test_missing_entry_is_an_error(monkeypatch):
    class Empty:
        text = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"/>'

        def raise_for_status(self):
            return None

    monkeypatch.setattr(arxiv, "_get", lambda *a, **k: Empty())
    with pytest.raises(arxiv.ArxivError):
        arxiv.fetch_metadata("0000.00000")


# ---- caption detection -----------------------------------------------------

@pytest.mark.parametrize("line,label", [
    ("Figure 3: Class distribution", "Figure 3"),
    ("Fig. 2. Architecture of the network", "Figure 2"),
    ("Table 1: Files contained in the dataset", "Table 1"),
    ("Algorithm 1 Best-scored random forest", "Algorithm 1"),
])
def test_caption_lines_are_recognised(line, label):
    parsed = pdf.caption_of(line)
    assert parsed is not None and parsed[0] == label


@pytest.mark.parametrize("line", [
    "Table 3 shows the benchmark results for every classifier",
    "Figure 2 is reproduced from prior work",
    "Table 3 - continued from previous page",
    "We train the network for 50 epochs",
])
def test_prose_is_not_a_caption(line):
    assert pdf.caption_of(line) is None


# ---- PDF scan --------------------------------------------------------------

@needs_pdf
def test_extract_reads_text_and_figures():
    extract = pdf.extract(COMMITTED_PDF.read_bytes(), max_figures=6)
    assert extract.pages > 10
    assert len(extract.text) > 10_000
    assert "Deep Neural Networks" in extract.title_guess
    assert extract.figures, "the paper has captioned figures"
    assert all(f.label.startswith(("Figure", "Table", "Algorithm"))
               for f in extract.figures)
    assert len(extract.figures) <= 6


@needs_pdf
def test_figure_crops_carry_pixels():
    extract = pdf.extract(COMMITTED_PDF.read_bytes(), max_figures=3)
    with_art = [f for f in extract.figures if f.source in ("region", "next-page")]
    assert with_art, "at least one caption should resolve to artwork"
    for fig in with_art:
        assert fig.png.startswith(b"\x89PNG")
        assert fig.width > 100 and fig.height > 20


def test_extract_rejects_non_pdf():
    with pytest.raises(pdf.PdfError):
        pdf.extract(b"this is not a pdf")


# ---- figure readings -------------------------------------------------------

def test_scan_without_a_provider_keeps_captions(monkeypatch):
    monkeypatch.setattr(figures, "make_vision_provider",
                        lambda: (_ for _ in ()).throw(figures.VisionUnavailable("no key")))
    fig = pdf.Figure(index=1, page=2, label="Figure 1", caption="A diagram",
                     png=b"\x89PNG", width=200, height=200)
    records = figures.scan_figures([fig], paper_title="Paper")
    assert records[0]["scanned"] is False
    assert "no key" in records[0]["error"]


def test_scan_records_a_reading():
    class Reader:
        name = "test"
        model = "test-vision"

        def read(self, png, prompt, max_tokens=700):
            assert png and "Figure 1" in prompt
            return "This is an architecture diagram with three stages."

    fig = pdf.Figure(index=1, page=2, label="Figure 1", caption="A diagram",
                     png=b"\x89PNG", width=200, height=200)
    record = figures.scan_figures([fig], paper_title="Paper", provider=Reader())[0]
    assert record["scanned"] is True
    assert record["reader_model"] == "test-vision"
    assert "architecture diagram" in record["reading"]


def test_unreadable_text_region_says_so():
    class Reader:
        def read(self, png, prompt, max_tokens=700):
            return "UNREADABLE"

    fig = pdf.Figure(index=1, page=2, label="Table 4", caption="Results",
                     png=b"\x89PNG", source="text-region")
    record = figures.scan_figures([fig], paper_title="Paper", provider=Reader())[0]
    assert record["scanned"] is False
    assert "no figure artwork" in record["error"]


def test_a_failing_reader_degrades_rather_than_raises():
    class Reader:
        def read(self, png, prompt, max_tokens=700):
            raise RuntimeError("upstream 500")

    fig = pdf.Figure(index=1, page=1, label="Figure 1", caption="", png=b"\x89PNG")
    record = figures.scan_figures([fig], paper_title="Paper", provider=Reader())[0]
    assert record["scanned"] is False
    assert "upstream 500" in record["error"]


def test_appendix_marks_machine_readings():
    body = figures.figures_appendix([
        {"label": "Figure 1", "page": 2, "caption": "A diagram",
         "reading": "shows three stages"},
        {"label": "Table 2", "page": 3, "caption": "Results", "error": "no key"},
    ])
    assert "[figure scan] shows three stages" in body
    assert "[figure scan unavailable] no key" in body
    assert "vision model's" in body


# ---- slugs -----------------------------------------------------------------

def test_slugify_drops_stopwords_and_punctuation():
    assert ingest.slugify("Fashion-MNIST: a Novel Image Dataset") == \
        "fashion-mnist-novel-image-dataset"


def test_unique_slug_never_collides(tmp_path):
    (tmp_path / "paper").mkdir()
    assert ingest.unique_slug(tmp_path, "paper") == "paper-2"
    assert ingest.unique_slug(tmp_path, "paper", hint="1708.07747") == "paper-1708-07747"
    assert ingest.unique_slug(tmp_path, "fresh") == "fresh"


# ---- writing a paper directory ---------------------------------------------

@needs_pdf
def test_ingest_writes_a_pipeline_ready_directory(tmp_path):
    manifest = ingest.ingest_pdf(
        COMMITTED_PDF.read_bytes(), papers_dir=tmp_path, scan_figures=False,
        max_figures=3, title_hint="whatever.pdf")
    dest = tmp_path / manifest["slug"]

    # exactly the three files scripts/auto_run.py opens, plus the evidence trail
    assert (dest / "paper.json").is_file()
    assert (dest / "paper-extract.txt").is_file()
    assert (dest / "code_absence.json").is_file()
    assert (dest / "paper.pdf").is_file()
    assert (dest / "figures.json").is_file()

    meta = json.loads((dest / "paper.json").read_text())
    assert meta["paper_id"] == manifest["slug"]
    assert "Deep Neural Networks" in meta["title"]   # the scan beats the filename
    assert meta["source"] == "upload"
    assert len(meta["pdf_sha256"]) == 64
    assert meta["figures"]["scanned"] == 0

    # the certificate is left unsearched: certifying is the run's job, not intake's
    certificate = json.loads((dest / "code_absence.json").read_text())
    assert certificate["results"] == []
    assert certificate["status"].startswith("NOT_SEARCHED")

    body = (dest / "paper-extract.txt").read_text()
    assert "FIGURES AND DIAGRAMS" in body
    assert len(body) > 10_000


@needs_pdf
def test_explicit_title_wins_over_the_scan(tmp_path):
    manifest = ingest.ingest_pdf(COMMITTED_PDF.read_bytes(), papers_dir=tmp_path,
                                 title="My Own Title", scan_figures=False, max_figures=1)
    assert manifest["title"] == "My Own Title"
    assert manifest["slug"] == "my-own-title"


@needs_pdf
def test_figure_crops_land_next_to_the_paper(tmp_path):
    manifest = ingest.ingest_pdf(COMMITTED_PDF.read_bytes(), papers_dir=tmp_path,
                                 scan_figures=False, max_figures=2)
    dest = tmp_path / manifest["slug"]
    written = sorted(p.name for p in (dest / "figures").iterdir())
    assert written, "figure crops are written beside the paper"
    for figure in manifest["figures"]:
        if figure["file"]:
            assert (dest / figure["file"]).is_file()


def test_ingest_rejects_a_non_pdf(tmp_path):
    with pytest.raises(ingest.IngestError):
        ingest.ingest_pdf(b"not a pdf at all", papers_dir=tmp_path)


def test_ingest_rejects_an_oversized_pdf(tmp_path):
    with pytest.raises(ingest.IngestError, match="cap"):
        ingest.ingest_pdf(b"%PDF" + b"0" * (ingest.MAX_PDF_BYTES + 1),
                          papers_dir=tmp_path)


def test_find_by_arxiv_id_ignores_the_version(tmp_path):
    dest = tmp_path / "paper"
    dest.mkdir()
    (dest / "paper.json").write_text(json.dumps({"arxiv_id": "1708.07747v2"}))
    assert ingest.find_by_arxiv_id("1708.07747", papers_dir=tmp_path) == dest
    assert ingest.find_by_arxiv_id("1708.07747v9", papers_dir=tmp_path) == dest
    assert ingest.find_by_arxiv_id("1905.11028", papers_dir=tmp_path) is None


def test_list_papers_reports_the_committed_set():
    rows = ingest.list_papers("papers")
    slugs = {row["slug"] for row in rows}
    assert {"fashion-mnist", "dnn-pattern-recognition"} <= slugs
    fashion = next(r for r in rows if r["slug"] == "fashion-mnist")
    assert fashion["paper_dir"] == "papers/fashion-mnist"
    assert fashion["ready"] is True
    assert fashion["chars"] > 1000
