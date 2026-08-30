"""LLM provider interface for the four roles. Agents propose; the orchestrator disposes.

Each role gets its own provider instance (separate context by construction). The
Verifier is constructed with a different model than the Implementer so no shared
weights bias slips between building and judging. Keys are read at call time from the process environment, then from a `.env`
file; nothing here is required for the deterministic pipeline to run.
"""

import json
import re
from typing import Protocol

from ..env import env_key

IMPLEMENTER_MODEL = "claude-sonnet-5"
VERIFIER_MODEL = "claude-opus-5"


class LLMProvider(Protocol):
    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str: ...


class RoleError(RuntimeError):
    pass


class AnthropicProvider:
    def __init__(self, model: str, api_key: str | None = None):
        self.model = model
        self.api_key = api_key or env_key("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RoleError("ANTHROPIC_API_KEY not set; LLM roles are unavailable")

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        msg = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in msg.content if block.type == "text")


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a completion, tolerating code fences."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise RoleError(f"role returned unparseable JSON: {e}") from e
