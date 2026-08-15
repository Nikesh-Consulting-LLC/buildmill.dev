"""US-99.1: which `.buildmill/` file each instruction kind is published to.

Phase 96 spent three migrations establishing that the type-to-instruction
mapping lives in exactly ONE function so its two call sites cannot disagree.
The kind-to-file mapping gets the same treatment for the same reason: every
other story in Phase 99 reads this map, and a second copy of it is a second
answer to "which file steers a bug fix".

Sixteen kinds get files. Three deliberately do not — see EXCLUDED.

`Guidelines.md` is reserved for the project's own conventions (us-99.3) and
belongs to no instruction kind, which is why the refresh RUN's instruction is
`Guidelines_Refresh.md` rather than colliding with it.
"""

from __future__ import annotations

ROOT = ".buildmill"

#: instruction kind -> file name under ROOT
KIND_FILES: dict[str, str] = {
    # requirements
    "prd": "PRD.md",
    "breakdown": "Breakdown.md",
    "elaborate": "Elaborate.md",
    "wireframe": "Wireframe.md",
    # planning — one per type, because Phase 96 made them genuinely different
    "plan": "Plan.md",
    "standalone_plan": "Plan_Standalone.md",
    "bug_rca": "RCA.md",
    # building
    "code": "Code.md",
    "standalone_code": "Code_Standalone.md",
    "bug_fix": "Fix.md",
    "chore": "Chore.md",
    # integration
    "merge": "Merge.md",
    # verification and shipping
    "test": "Test.md",
    "deploy": "Deploy.md",
    "release": "Release_Prep.md",
    # the run that proposes changes to the conventions — NOT the conventions
    "guidelines": "Guidelines_Refresh.md",
}

#: The project's own conventions. Not an instruction kind: it is the assembled
#: output of `project_guidelines`, published alongside the instructions
#: (us-99.3). Named here so nothing else claims the filename.
CONVENTIONS_FILE = "Guidelines.md"

#: Instruction kinds that get NO file, with the reason.
#:
#: These are server-side LLM prompts driven by `llm.LLM_FUNCTIONS` — the
#: factory calls the model itself with them. No agent ever reads them, and
#: they are platform-global rather than per-project, so publishing them into
#: a repository would be publishing machinery the repository cannot use.
EXCLUDED: dict[str, str] = {
    "story_breakdown": "server-side LLM prompt (llm.LLM_FUNCTIONS)",
    "test_case_elaborate": "server-side LLM prompt (llm.LLM_FUNCTIONS)",
    "deploy_script_generate": "server-side LLM prompt (llm.LLM_FUNCTIONS)",
}


def path_for(kind: str) -> str | None:
    """The repo-relative path this kind publishes to, or None if it has no
    file (an excluded kind, or one this map has never heard of)."""
    name = KIND_FILES.get(kind)
    return f"{ROOT}/{name}" if name else None


def kind_for_path(path: str) -> str | None:
    """The inverse: which kind owns this path, or None if the factory does
    not own it. Used before overwriting anything, so a file the factory did
    not write is never treated as one it did."""
    # NOT lstrip("./") — that strips a character SET, so it eats the leading
    # dot of `.buildmill` and every path stops matching. Caught by
    # test_paths_round_trip, which is the whole reason it exists.
    clean = (path or "").strip()
    if clean.startswith("./"):
        clean = clean[2:]
    clean = clean.lstrip("/")
    if not clean.startswith(f"{ROOT}/"):
        return None
    name = clean[len(ROOT) + 1 :]
    for kind, file_name in KIND_FILES.items():
        if file_name == name:
            return kind
    return None


def all_paths() -> list[str]:
    """Every path the factory owns under ROOT, conventions included."""
    return sorted(
        [f"{ROOT}/{n}" for n in KIND_FILES.values()]
        + [f"{ROOT}/{CONVENTIONS_FILE}"]
    )
