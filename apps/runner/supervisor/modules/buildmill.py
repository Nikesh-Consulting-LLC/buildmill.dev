"""Buildmill Agent module (US-60.1).

Claude Code under a platform-billed name: identical CLI invocation, identical
declared settings, identical stream handling — every behavior here is
`ClaudeModule`'s, inherited unchanged. The only difference is which
credential the gateway resolves for it, decided server-side by
`runner_config.claude_billing == 'platform'`, never by anything this module
does — from the runner's own perspective, `buildmill` and `claude` are the
same agent under two names.
"""

from __future__ import annotations

from . import register
from .claude import ClaudeModule


class BuildmillAgentModule(ClaudeModule):
    name = "buildmill"


register(BuildmillAgentModule())
