"""Z.AI provider with reasoning disabled.

GLM-4.6 reasons by default and bills those tokens against `max_tokens`, so a
role call with a modest budget returns finish_reason=length and an empty
`content`. The roles want structured JSON, not deliberation. Subclassing keeps
`repro/roles/base.py` untouched.
"""

from ..roles.base import RoleError, ZaiProvider
from ..env import env_key


class ThinkingOffZai(ZaiProvider):
    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        import httpx

        r = httpx.post(
            self.URL,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json={"model": self.model, "max_tokens": max_tokens,
                  "thinking": {"type": env_key("ZAI_THINKING") or "disabled"},
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
            timeout=240,
        )
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        return msg.get("content") or msg.get("reasoning_content") or ""


def make_auto_provider(role: str = "planner"):
    """Every role on this path is served by Z.AI; no Anthropic fallback."""
    if not env_key("ZAI_API_KEY", "ZAI_API"):
        raise RoleError("ZAI_API_KEY / ZAI_API not set; the autonomous driver needs it")
    return ThinkingOffZai()
