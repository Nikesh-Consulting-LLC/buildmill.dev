"""Runner shell policy evaluation (US-10.7).

Pure function over a runner's `autonomy_policy` (stored in runner_config,
US-10.2). Default is allow-all-and-audit — full autonomy — but a runner can be
set to `deny` (a kill switch) or given deny/allow patterns, or `require-approval`
(held for a manager decision; the interactive approval UX lands with the UI,
US-10.9). The value of the default mode is the complete server-side audit trail,
not restriction.
"""

from __future__ import annotations

import re
from typing import Any


def evaluate(policy: dict[str, Any], argv: list[str]) -> tuple[bool, str | None]:
    """Return (allow, reason). `policy` shape:
    {mode: allow|require-approval|deny, deny_patterns: [regex], allow_patterns: [regex]}.
    """
    mode = (policy or {}).get("mode", "allow")
    line = " ".join(str(a) for a in argv or [])

    for pat in (policy or {}).get("deny_patterns", []) or []:
        try:
            if re.search(pat, line):
                return False, f"blocked by policy pattern: {pat}"
        except re.error:
            continue

    if mode == "deny":
        return False, "runner policy denies all commands"

    if mode == "require-approval":
        allow_patterns = (policy or {}).get("allow_patterns", []) or []
        for pat in allow_patterns:
            try:
                if re.search(pat, line):
                    return True, None
            except re.error:
                continue
        return False, "command requires manager approval"

    return True, None
