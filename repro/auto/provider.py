"""Provider factory for the autonomous path.

The thinking-disable that GLM-4.6 needs lives in `repro/roles/base.ZaiProvider`
itself, so both the calibration and autonomous paths get it; this module only
pins the autonomous path to Z.AI with no Anthropic fallback.
"""

from ..env import env_key
from ..roles.base import RoleError, ZaiProvider

# kept as an alias so existing imports and run artifacts keep resolving
ThinkingOffZai = ZaiProvider


def make_auto_provider(role: str = "planner") -> ZaiProvider:
    """Every role on this path is served by Z.AI; no Anthropic fallback."""
    if not env_key("ZAI_API_KEY", "ZAI_API"):
        raise RoleError("ZAI_API_KEY / ZAI_API not set; the autonomous driver needs it")
    return ZaiProvider()
