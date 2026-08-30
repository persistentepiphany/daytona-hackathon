"""PDF scan: page text plus the figure regions a diagram reader needs.

Two backends, in preference order. PyMuPDF renders the *region* around each
figure caption, so a vector diagram (TikZ, matplotlib PDF output) comes out as a
picture like everything else; pypdf can only hand back embedded rasters, which
misses vector figures but keeps the module working on a lean install. Text
extraction works under either.

Nothing here calls a model. `repro.ingest.figures` does the reading; this module
only decides what a figure is and hands over the pixels.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

# Caption lines an academic PDF actually uses. Tables and algorithms are figures
# for our purposes: they carry reported numbers, which is what the pipeline grades.
CAPTION = re.compile(
    r"^\s*(figure|fig\.?|table|algorithm|alg\.?|scheme|chart)\s*"
    r"([0-9]+|[ivxlcIVXLC]+)\s*[.:)—-]?\s+(?P<rest>\S.*)?",
    re.IGNORECASE,
)
MIN_REGION_PT = 48.0     # a caption with nothing but whitespace above it is prose
MAX_REGION_PT = 620.0    # never claim a whole page as one figure
MIN_RASTER_PX = 120      # icons, rules and logos are not diagrams
MIN_MARK_PT = 12.0       # smaller than a glyph in both axes: a bullet, not a figure
MAX_GAP_PT = 90.0        # further from the caption than this is the next thing on the page


class PdfError(RuntimeError):
    pass


@dataclass
class Figure:
    index: int
    page: int                 # 1-based
    label: str                # "Figure 3"
    caption: str
    png: bytes = b""
    width: int = 0
    height: int = 0
    # region: the artwork beside the caption · next-page: artwork the layout pushed
    # onto the following page · text-region: a caption with no artwork found beside
    # it, cropped from the surrounding text · embedded: a raster pulled by pypdf
    source: str = "region"

    def meta(self) -> dict:
        return {"index": self.index, "page": self.page, "label": self.label,
                "caption": self.caption, "width": self.width, "height": self.height,
                "source": self.source, "has_image": bool(self.png)}


@dataclass
class PdfExtract:
    text: str
    pages: int
    figures: list[Figure] = field(default_factory=list)
    backend: str = ""
    title_guess: str = ""
    notes: list[str] = field(default_factory=list)

    def meta(self) -> dict:
        return {"pages": self.pages, "chars": len(self.text), "backend": self.backend,
                "n_figures": len(self.figures),
                "n_figure_images": sum(1 for f in self.figures if f.png),
                "notes": self.notes}


def _backend() -> tuple[str, object]:
    try:
        import pymupdf  # type: ignore
        return "pymupdf", pymupdf
    except ImportError:
        pass
    try:  # older wheels only expose the deprecated name
        import fitz  # type: ignore
        return "pymupdf", fitz
    except ImportError:
        pass
    try:
        import pypdf  # type: ignore
        return "pypdf", pypdf
    except ImportError as exc:
        raise PdfError(
            "no PDF backend installed: pip install pymupdf (preferred) or pypdf"
        ) from exc


def caption_of(line: str) -> tuple[str, str] | None:
    """('Figure 3', 'Class distribution ...') for a caption line, else None."""
    m = CAPTION.match(line.strip())
    if not m:
        return None
    rest = (m.group("rest") or "").strip()
    # "Table 3 shows that ..." is a sentence, not a caption; a real caption's
    # remainder reads as a noun phrase and a sentence's starts with a verb.
    if re.match(r"^(shows?|lists?|reports?|gives?|summari[sz]es?|presents?|is|are|in|of|and)\b",
                rest, re.IGNORECASE):
        return None
    # a table spilling across pages repeats its caption with a continuation note;
    # the first occurrence is the figure, the rest are page furniture
    if re.match(r"^[\u2013\u2014-]?\s*continued\b", rest, re.IGNORECASE):
        return None
    kind = m.group(1).rstrip(".").title()
    kind = {"Fig": "Figure", "Alg": "Algorithm"}.get(kind, kind)
    return f"{kind} {m.group(2)}", rest


def title_from_layout(page) -> str:
    """A paper's title is its biggest horizontal text on page one.

    Reading it from font size beats reading it from line order, which on a
    preprint lands on the arXiv stamp or a corresponding-author footnote. Whole
    lines are taken rather than the largest spans alone, so a small-caps title
    does not come back as its capital letters.
    """
    try:
        blocks = page.get_text("dict")["blocks"]
    except Exception:  # noqa: BLE001
        return ""
    lines = []
    for block in blocks:
        for line in block.get("lines", []):
            direction = line.get("dir", (1, 0))
            if abs(direction[0]) < 0.99:
                continue    # the arXiv stamp runs up the margin, rotated
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            lines.append({
                # spans carry their own spacing; joining with one would split a
                # small-caps title into "D EEP N EURAL"
                "text": "".join(s["text"] for s in spans),
                "size": max(round(s.get("size", 0), 1) for s in spans),
                "top": line["bbox"][1],
                "bottom": line["bbox"][3],
            })
    if not lines:
        return ""
    lines.sort(key=lambda ln: ln["top"])
    body = _body_size(lines)
    head = [ln for ln in lines if ln["top"] <= page.rect.y0 + 0.45 * page.rect.height]
    if not head:
        return ""
    biggest = max(ln["size"] for ln in head)
    if biggest < 1.12 * body:
        return ""   # a uniform-font page has no title to find
    picked: list[dict] = []
    for line in head:
        if line["size"] < biggest - 0.6:
            if picked:
                break   # the title is one run of lines, not every large line
            continue
        if picked and line["top"] - picked[-1]["bottom"] > 1.6 * (
                picked[-1]["bottom"] - picked[-1]["top"]):
            break
        picked.append(line)
    title = " ".join(" ".join(ln["text"] for ln in picked).split())
    if len(title) < 8 or len(title) > 300 or not any(c.isalpha() for c in title):
        return ""
    if title == title.upper():
        title = title.title()   # a small-caps title reads as a shout otherwise
    return title


def _body_size(lines) -> float:
    """The page's most common font size — its body text."""
    counts: dict[float, int] = {}
    for line in lines:
        counts[line["size"]] = counts.get(line["size"], 0) + len(line["text"])
    return max(counts, key=lambda k: counts[k]) if counts else 0.0


def guess_title(text: str) -> str:
    for raw in text.splitlines():
        line = raw.strip()
        if 12 <= len(line) <= 180 and not line.lower().startswith(("abstract", "arxiv:")):
            return line
    return ""


def extract(data: bytes, *, max_figures: int = 12, dpi: int = 150) -> PdfExtract:
    """Text plus figure regions from PDF bytes."""
    if not data.startswith(b"%PDF"):
        raise PdfError("not a PDF (missing %PDF header)")
    name, mod = _backend()
    out = _extract_pymupdf(data, mod, max_figures, dpi) if name == "pymupdf" \
        else _extract_pypdf(data, mod, max_figures)
    out.backend = name
    out.title_guess = out.title_guess or guess_title(out.text)
    return out


def _extract_pymupdf(data: bytes, pymupdf, max_figures: int, dpi: int) -> PdfExtract:
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        pages_text: list[str] = []
        figures: list[Figure] = []
        zoom = dpi / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)
        for pno in range(doc.page_count):
            page = doc[pno]
            pages_text.append(page.get_text("text"))
            if len(figures) >= max_figures:
                continue
            for label, caption, rect, artwork in _caption_regions(page, pymupdf):
                if len(figures) >= max_figures:
                    break
                target, source = page, "region" if artwork else "text-region"
                if rect is None:
                    if pno + 1 >= doc.page_count:
                        continue
                    target = doc[pno + 1]
                    rect = _page_head(target, pymupdf)
                    source = "next-page"
                png, w, h = b"", 0, 0
                try:
                    pix = target.get_pixmap(matrix=matrix, clip=rect)
                    png, w, h = pix.tobytes("png"), pix.width, pix.height
                except Exception:  # noqa: BLE001 - a bad region must not sink the scan
                    pass
                figures.append(Figure(index=len(figures) + 1, page=pno + 1, label=label,
                                      caption=caption, png=png, width=w, height=h,
                                      source=source))
        text = "\n".join(pages_text)
        extract_out = PdfExtract(text=text, pages=doc.page_count, figures=figures)
        if doc.page_count:
            extract_out.title_guess = title_from_layout(doc[0])
        if not figures:
            extract_out.notes.append("no captioned figure regions found")
        return extract_out
    finally:
        doc.close()


def _page_head(page, pymupdf):
    """The top band of a page, down to whatever caption starts the next figure."""
    bottom = page.rect.y1 - 24
    for block in sorted(page.get_text("blocks"), key=lambda b: b[1]):
        if len(block) < 5 or not isinstance(block[4], str):
            continue
        body = block[4].strip()
        if body and caption_of(body.splitlines()[0]) and block[1] > page.rect.y0 + 80:
            bottom = min(bottom, block[1])
            break
    top = page.rect.y0 + 24
    return pymupdf.Rect(page.rect.x0 + 24, top, page.rect.x1 - 24,
                        min(bottom, top + MAX_REGION_PT))


def _page_graphics(page, pymupdf) -> list:
    """Every drawn thing on the page: raster images and vector paths alike.

    A booktabs rule is a rectangle of zero height. Dropping flat rectangles would
    drop every table in the paper, and PyMuPDF treats an empty rect as nothing to
    union, so hairlines are kept and given a sliver of thickness. Marks smaller
    than a glyph in both axes are dropped: those are bullets and tick marks.
    """
    rects = []
    try:
        for info in page.get_images(full=True):
            rects.extend(page.get_image_rects(info[0]))
    except Exception:  # noqa: BLE001
        pass
    try:
        for drawing in page.get_drawings():
            rect = drawing.get("rect")
            if rect is not None:
                rects.append(rect)
    except Exception:  # noqa: BLE001
        pass
    kept = []
    for r in rects:
        if r.width < MIN_MARK_PT and r.height < MIN_MARK_PT:
            continue
        kept.append(r if (r.width >= 1 and r.height >= 1)
                    else pymupdf.Rect(r.x0 - 0.5, r.y0 - 0.5, r.x1 + 0.5, r.y1 + 0.5))
    return kept


def _union(rects, pymupdf):
    out = pymupdf.Rect(rects[0])
    for r in rects[1:]:
        out |= r
    return out


def _overlaps_column(rect, x0: float, x1: float) -> bool:
    span = min(rect.x1, x1) - max(rect.x0, x0)
    return span > 0.15 * max(1.0, min(rect.width, x1 - x0))


def _caption_regions(page, pymupdf) -> list[tuple[str, str, object, bool]]:
    """Caption blocks on a page, each paired with the region it captions.

    A caption alone is a sentence about a picture; what the reader needs is the
    picture. The region is the graphics — rasters and vector paths both — that sit
    against the caption, unioned with the caption itself so axis labels, legends
    and the caption text land in one crop. Papers caption figures below and tables
    above, so both sides are tried; a table drawn with no rules at all falls back
    to the text band next to the caption.
    """
    blocks = [b for b in page.get_text("blocks") if len(b) >= 5 and isinstance(b[4], str)]
    blocks.sort(key=lambda b: (round(b[1], 1), round(b[0], 1)))
    graphics = _page_graphics(page, pymupdf)
    page_rect = page.rect
    found: list[tuple[str, str, object, bool]] = []
    # artwork belongs to exactly one caption: the first, reading down the page,
    # that sits against it. Without this a table captioned at the page foot
    # annexes the figure above it, which belongs to the caption above that.
    claimed: set[int] = set()

    for i, block in enumerate(blocks):
        x0, y0, x1, y1, body = block[0], block[1], block[2], block[3], block[4]
        stripped = body.strip()
        if not stripped:
            continue
        parsed = caption_of(stripped.splitlines()[0])
        if not parsed:
            continue
        label, rest = parsed
        caption = " ".join(body.split())
        cap_rect = pymupdf.Rect(x0, y0, x1, y1)

        # another caption marks the edge of another figure; never cross it
        limit_up, limit_down = _neighbour_captions(blocks, i, page_rect)
        free = [(n, r) for n, r in enumerate(graphics) if n not in claimed]
        above = _contiguous(
            [(n, r) for n, r in free
             if r.y1 <= y0 + 2 and r.y1 >= max(y0 - MAX_REGION_PT, limit_up)
             and _overlaps_column(r, x0, x1)], y0, -1)
        below = _contiguous(
            [(n, r) for n, r in free
             if r.y0 >= y1 - 2 and r.y0 <= min(y1 + MAX_REGION_PT, limit_down)
             and _overlaps_column(r, x0, x1)], y1, 1)
        # figures are captioned below and tables above, so the side that wins is
        # the one whose artwork hugs the caption, not a fixed direction
        pick = _closer_side(above, below, y0, y1)
        if not pick:
            # a caption typeset over its own artwork is neither above nor below it
            touching = [(n, r) for n, r in free if (r & cap_rect).get_area() > 0]
            if _extent(touching) >= MIN_REGION_PT:
                pick = touching

        if not pick and y1 > page_rect.y1 - 0.22 * page_rect.height:
            # a caption at the foot of the page belongs to artwork the layout
            # pushed onto the next one — a long table, usually
            found.append((label, caption or rest, None, True))
            continue

        artwork = True
        if pick:
            claimed.update(n for n, _ in pick)
            region = _union([r for _, r in pick] + [cap_rect], pymupdf)
            # text sitting inside the graphics band (tick labels, legends, panel
            # letters) belongs to the figure, so pull it into the crop
            for other in blocks:
                other_rect = pymupdf.Rect(other[0], other[1], other[2], other[3])
                if other_rect in region or (region & other_rect).get_area() > 0.5 * other_rect.get_area():
                    region |= other_rect
        else:
            band = _text_band(blocks, i, cap_rect, pymupdf)
            region = band if band is not None else cap_rect
            artwork = False

        region = region & page_rect
        if region.height > MAX_REGION_PT:
            # keep the end nearest the caption; the far end is another figure
            region = pymupdf.Rect(region.x0, max(region.y0, y1 - MAX_REGION_PT),
                                  region.x1, min(region.y1, y0 + MAX_REGION_PT))
        found.append((label, caption or rest, region, artwork))
    return found


def _neighbour_captions(blocks, index, page_rect) -> tuple[float, float]:
    """The vertical span this caption owns: from the caption above it to the one
    below it."""
    up, down = page_rect.y0, page_rect.y1
    for j, other in enumerate(blocks):
        if j == index:
            continue
        body = other[4].strip()
        if not body or not caption_of(body.splitlines()[0]):
            continue
        if other[3] <= blocks[index][1] + 1:
            up = max(up, other[3])
        elif other[1] >= blocks[index][3] - 1:
            down = min(down, other[1])
    return up, down


def _contiguous(stack: list, edge: float, direction: int) -> list:
    """Walk out from the caption, stopping at the first real gap.

    A page footer rule and a table's own rules both sit below a caption; only the
    run of artwork that actually touches it belongs to the figure.
    """
    ordered = sorted(stack, key=lambda pair: (pair[1].y0 if direction > 0 else -pair[1].y1))
    kept = []
    for n, rect in ordered:
        gap = (rect.y0 - edge) if direction > 0 else (edge - rect.y1)
        if gap > MAX_GAP_PT:
            break
        kept.append((n, rect))
        edge = max(edge, rect.y1) if direction > 0 else min(edge, rect.y0)
    return kept


def _closer_side(above: list, below: list, y0: float, y1: float) -> list:
    """Whichever of the two candidate stacks sits nearer the caption, if either
    is tall enough to be a figure at all. Stacks are (index, rect) pairs."""
    ok_above = _extent(above) >= MIN_REGION_PT
    ok_below = _extent(below) >= MIN_REGION_PT
    if ok_above and ok_below:
        gap_above = y0 - max(r.y1 for _, r in above)
        gap_below = min(r.y0 for _, r in below) - y1
        return above if gap_above <= gap_below else below
    if ok_above:
        return above
    return below if ok_below else []


def _extent(rects) -> float:
    """Vertical span of an (index, rect) stack."""
    if not rects:
        return 0.0
    return max(r.y1 for _, r in rects) - min(r.y0 for _, r in rects)


def _text_band(blocks, index, cap_rect, pymupdf):
    """A rule-less table: the run of text blocks butting against the caption."""
    for direction in (1, -1):
        region = None
        edge = cap_rect.y1 if direction == 1 else cap_rect.y0
        walk = blocks[index + 1:] if direction == 1 else list(reversed(blocks[:index]))
        for other in walk:
            rect = pymupdf.Rect(other[0], other[1], other[2], other[3])
            gap = (rect.y0 - edge) if direction == 1 else (edge - rect.y1)
            if gap < -2 or gap > 26:
                break
            if caption_of(other[4].strip().splitlines()[0] if other[4].strip() else ""):
                break
            region = rect if region is None else (region | rect)
            edge = rect.y1 if direction == 1 else rect.y0
        if region is not None and region.height >= MIN_REGION_PT:
            return region | cap_rect
    return None


def _extract_pypdf(data: bytes, pypdf, max_figures: int) -> PdfExtract:
    reader = pypdf.PdfReader(io.BytesIO(data))
    pages_text: list[str] = []
    captions: list[tuple[int, str, str]] = []
    for pno, page in enumerate(reader.pages):
        try:
            body = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - a malformed page must not sink the scan
            body = ""
        pages_text.append(body)
        for line in body.splitlines():
            parsed = caption_of(line)
            if parsed:
                captions.append((pno + 1, parsed[0], line.strip()))

    figures: list[Figure] = []
    for pno, page in enumerate(reader.pages):
        if len(figures) >= max_figures:
            break
        try:
            images = list(page.images)
        except Exception:  # noqa: BLE001
            images = []
        for image in images:
            if len(figures) >= max_figures:
                break
            png, w, h = _as_png(image)
            if not png:
                continue
            here = [c for c in captions if c[0] == pno + 1]
            label, caption = (here[0][1], here[0][2]) if here else (f"Image p{pno + 1}", "")
            figures.append(Figure(index=len(figures) + 1, page=pno + 1, label=label,
                                  caption=caption, png=png, width=w, height=h,
                                  source="embedded"))
    # captions without a matching raster still tell the reader a figure exists
    for pno, label, caption in captions:
        if len(figures) >= max_figures:
            break
        if any(f.page == pno and f.label == label for f in figures):
            continue
        figures.append(Figure(index=len(figures) + 1, page=pno, label=label,
                              caption=caption, source="caption-only"))

    out = PdfExtract(text="\n".join(pages_text), pages=len(reader.pages), figures=figures)
    out.notes.append("pypdf backend: vector diagrams are not rendered; "
                     "install pymupdf for figure-region images")
    return out


def _as_png(image) -> tuple[bytes, int, int]:
    """Normalise an embedded raster to PNG, skipping anything icon-sized."""
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        data = getattr(image, "data", b"")
        return (data, 0, 0) if data.startswith(b"\x89PNG") else (b"", 0, 0)
    try:
        img = Image.open(io.BytesIO(image.data))
        if img.width < MIN_RASTER_PX or img.height < MIN_RASTER_PX:
            return b"", 0, 0
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return buf.getvalue(), img.width, img.height
    except Exception:  # noqa: BLE001
        return b"", 0, 0
