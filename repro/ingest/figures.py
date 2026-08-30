"""Read the pictures: turn extracted figure regions into text the pipeline can use.

The planner sees a paper as text, so a diagram or a results table rendered as an
image is invisible to it. This module hands each figure crop to a vision model and
folds the readings back into `paper-extract.txt`, which is the only artifact the
downstream roles consume. Diagram readings are labelled as such in that file so a
number the pipeline later grades can always be traced to either the paper's own
text or a machine reading of one of its figures.

No key, no scan: figures keep their captions and the run proceeds. Nothing here
is load-bearing.
"""

from __future__ import annotations

import base64
from collections.abc import Callable

from ..env import env_key

SYSTEM = """You read one figure from a scientific paper for a reproduction
pipeline. Report only what is visibly in the image. Cover, in this order and in
at most 160 words:
1. what kind of figure it is (architecture diagram, plot, photograph grid, results table, algorithm box);
2. its content — for a diagram, the components and the direction of flow between them; for a plot, the axes with units and the trend of each series; for a table, its columns and rows;
3. every reported number that is legible, transcribed exactly, with the row and column it belongs to.
Do not infer values that are not printed. Do not speculate about method details
the figure does not show. If the crop is unreadable or contains no figure, reply
exactly: UNREADABLE."""

DEFAULT_ZAI_VISION = "glm-4.5v"
DEFAULT_ANTHROPIC_VISION = "claude-sonnet-5"
MAX_SCAN_BYTES = 4_500_000   # provider payload ceiling for one base64 image


class VisionUnavailable(RuntimeError):
    pass


class ZaiVision:
    """Z.AI's OpenAI-compatible endpoint, with an image part in the message."""

    URL = "https://api.z.ai/api/paas/v4/chat/completions"
    name = "zai"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or env_key("ZAI_VISION_MODEL") or DEFAULT_ZAI_VISION
        self.api_key = api_key or env_key("ZAI_API_KEY", "ZAI_API")
        if not self.api_key:
            raise VisionUnavailable("ZAI_API_KEY / ZAI_API not set")
        # GLM's vision models think by default and bill the thinking tokens; with a
        # figure-sized budget the whole completion is reasoning and `content` comes
        # back empty. Same fix as repro.roles.base.ZaiProvider.
        self.thinking = (env_key("ZAI_THINKING") or "disabled").lower()

    def read(self, png: bytes, prompt: str, max_tokens: int = 700) -> str:
        import httpx

        data_url = "data:image/png;base64," + base64.b64encode(png).decode()
        r = httpx.post(
            self.URL,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json={"model": self.model, "max_tokens": max_tokens,
                  "thinking": {"type": self.thinking},
                  "messages": [
                      {"role": "system", "content": SYSTEM},
                      {"role": "user", "content": [
                          {"type": "image_url", "image_url": {"url": data_url}},
                          {"type": "text", "text": prompt},
                      ]},
                  ]},
            timeout=180,
        )
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        return (msg.get("content") or msg.get("reasoning_content") or "").strip()


class AnthropicVision:
    name = "anthropic"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or env_key("ANTHROPIC_VISION_MODEL") or DEFAULT_ANTHROPIC_VISION
        self.api_key = api_key or env_key("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise VisionUnavailable("ANTHROPIC_API_KEY not set")

    def read(self, png: bytes, prompt: str, max_tokens: int = 700) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        msg = client.messages.create(
            model=self.model, max_tokens=max_tokens, system=SYSTEM,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/png",
                    "data": base64.b64encode(png).decode()}},
                {"type": "text", "text": prompt},
            ]}],
        )
        return "".join(b.text for b in msg.content if b.type == "text").strip()


def make_vision_provider():
    """Z.AI first — the autonomous path is pinned to it — Anthropic as backup."""
    for build in (ZaiVision, AnthropicVision):
        try:
            return build()
        except VisionUnavailable:
            continue
    raise VisionUnavailable(
        "no vision key set (ZAI_API_KEY or ANTHROPIC_API_KEY); figures keep captions only")


def scan_figures(figures, *, paper_title: str, provider=None,
                 log: Callable[[str], None] | None = None,
                 max_figures: int = 12) -> list[dict]:
    """Read each figure that carries pixels. Returns one record per figure."""
    log = log or (lambda _msg: None)
    records = [dict(fig.meta(), reading="", scanned=False, error=None)
               for fig in figures]
    if not figures:
        return records
    if provider is None:
        try:
            provider = make_vision_provider()
        except VisionUnavailable as exc:
            log(f"figure scan skipped: {exc}")
            for record in records:
                record["error"] = str(exc)
            return records

    scanned = 0
    for fig, record in zip(figures, records):
        if scanned >= max_figures:
            record["error"] = "beyond figure scan cap"
            continue
        if not fig.png:
            record["error"] = "no image for this caption"
            continue
        if len(fig.png) > MAX_SCAN_BYTES:
            record["error"] = f"figure too large to send ({len(fig.png)} bytes)"
            continue
        prompt = (f"Paper: {paper_title}\nFigure label: {fig.label}\n"
                  f"Caption as printed: {fig.caption or '(none)'}\n\n"
                  f"Read this figure.")
        try:
            reading = provider.read(fig.png, prompt)
        except Exception as exc:  # noqa: BLE001 - a failed read degrades, never fails
            record["error"] = f"{type(exc).__name__}: {exc}"
            log(f"figure scan failed for {fig.label}: {exc}")
            continue
        scanned += 1
        if not reading or reading.strip().upper().startswith("UNREADABLE"):
            # a caption whose artwork the extractor could not find gets a crop of
            # the surrounding prose; saying so is more use than "unreadable"
            record["error"] = ("no figure artwork found beside this caption"
                               if fig.source == "text-region"
                               else "model reported the crop as unreadable")
            log(f"figure scan: {fig.label} not read ({record['error']})")
            continue
        record["reading"] = reading
        record["scanned"] = True
        record["reader"] = getattr(provider, "name", "vision")
        record["reader_model"] = getattr(provider, "model", "")
        log(f"figure scan: {fig.label} read ({len(reading)} chars)")
    return records


def figures_appendix(records: list[dict]) -> str:
    """The block appended to paper-extract.txt so the roles see the figures.

    Machine readings are marked as machine readings: a number the grader later
    traces here must be attributable to a model reading a picture rather than to
    the paper's own prose.
    """
    if not records:
        return ""
    lines = ["", "=" * 72,
             "FIGURES AND DIAGRAMS",
             "Captions are the paper's own text. Lines marked [figure scan] are a "
             "vision model's",
             "reading of the rendered figure, not the authors' words.",
             "=" * 72, ""]
    for record in records:
        lines.append(f"[{record['label']} · page {record['page']}] "
                     f"{record.get('caption') or '(no caption text)'}")
        if record.get("reading"):
            body = " ".join(record["reading"].split())
            lines.append(f"  [figure scan] {body}")
        elif record.get("error"):
            lines.append(f"  [figure scan unavailable] {record['error']}")
        lines.append("")
    return "\n".join(lines)
