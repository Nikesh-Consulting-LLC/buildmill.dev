"""Agent module registry (US-10.4).

Modules register themselves on import; `available()` / `all_modules()` autoload
the built-ins and any third-party modules published under the
`buildmill.runner.modules` entry-point group — so adding an agent is a drop-in
with no change to the runner core.
"""

from __future__ import annotations

from .base import (  # noqa: F401 — re-exported for module authors
    KNOWN_SETTINGS,
    AgentModule,
    Knob,
    ModuleResult,
    Primitives,
    RunContext,
    ShellResult,
    declaration,
    supports,
)

_REGISTRY: dict[str, "AgentModule"] = {}
_loaded = False


def register(module: "AgentModule") -> "AgentModule":
    """Register a module instance (idempotent by name)."""
    _REGISTRY[module.name] = module
    return module


def get(name: str) -> "AgentModule | None":
    _autoload()
    return _REGISTRY.get(name)


def available() -> list[str]:
    _autoload()
    return sorted(_REGISTRY)


def all_modules() -> dict[str, "AgentModule"]:
    _autoload()
    return dict(_REGISTRY)


def declarations() -> list[dict]:
    """US-32.4: what every module this runner has can be told, for the hello.

    Reported per module rather than as one merged shape: the whole point is
    that Grok and Claude are different, and a merged answer would put the union
    of their knobs on both.
    """
    _autoload()
    return [declaration(m) for _name, m in sorted(_REGISTRY.items())]


def _autoload() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    from . import sim  # noqa: F401 — registers on import
    from . import claude, grok, opencode  # noqa: F401 — built-in CLI modules (US-10.5)
    from . import buildmill  # noqa: F401 — Claude Code, platform-billed (US-60.1)
    from . import interactive  # noqa: F401 — ACP session agent (US-78.3)
    try:
        from importlib.metadata import entry_points

        for ep in entry_points(group="buildmill.runner.modules"):
            ep.load()
    except Exception:  # noqa: BLE001 — a bad third-party module never breaks core
        pass
