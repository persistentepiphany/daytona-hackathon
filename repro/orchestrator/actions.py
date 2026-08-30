"""The single validation choke point for agent-proposed actions.

Agents never touch a sandbox or the web directly: they return structured actions
— {"action": "run"|"write"|"search", ...} — and every one passes through
validate_action before the orchestrator applies it. Search actions additionally
go through the capped, stage-gated Parallel client, which enforces the
mechanics-vs-semantics boundary caps; run/write actions are applied through the
archaeology session so they land in RECIPE.sh.
"""

ALLOWED_ACTIONS = ("run", "write", "search")

REQUIRED_FIELDS = {
    "run": ("cmd",),
    "write": ("path", "content"),
    "search": ("objective", "queries"),
}


class ActionError(ValueError):
    pass


def validate_action(action: dict) -> dict:
    if not isinstance(action, dict):
        raise ActionError(f"action must be an object, got {type(action).__name__}")
    kind = action.get("action")
    if kind not in ALLOWED_ACTIONS:
        raise ActionError(f"action {kind!r} not in {ALLOWED_ACTIONS}")
    for field in REQUIRED_FIELDS[kind]:
        value = action.get(field)
        if value in (None, "", []):
            raise ActionError(f"{kind} action missing {field}")
    if kind == "run" and not isinstance(action["cmd"], str):
        raise ActionError("run.cmd must be a string")
    if kind == "write" and not isinstance(action["content"], str):
        raise ActionError("write.content must be a string")
    if kind == "search":
        if not isinstance(action["queries"], list) or not all(
                isinstance(q, str) for q in action["queries"]):
            raise ActionError("search.queries must be a list of strings")
    return action


def apply_action(session, action: dict, parallel=None, stage: str = "archaeology"):
    """Apply one validated action. run/write go through the session (recorded in
    RECIPE.sh); search goes through the Parallel client's caps and stage gates.

    This is also where the live feed observes the agents: every action the orchestrator
    applies passes through here, so the feed's producer sits at the same choke point as
    the validator rather than being sprinkled across call sites."""
    from ..telemetry import action_tap

    a = validate_action(action)
    tap = action_tap()
    if tap is not None:
        return tap.around(a, lambda: _dispatch(session, a, parallel, stage))
    return _dispatch(session, a, parallel, stage)


def _dispatch(session, a: dict, parallel, stage: str):
    kind = a["action"]
    if kind == "run":
        return session.sh(a["cmd"], check=bool(a.get("check", True)))
    if kind == "write":
        return session.put_file(a["path"], a["content"])
    if parallel is None:
        raise ActionError("search requested but no Parallel client is configured")
    return parallel.search(stage, a["objective"], a["queries"],
                           max_results=int(a.get("max_results", 5)))
