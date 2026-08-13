"""LLM provider access for thinking tasks (US-1.16, US-3.17).

Multiple named providers per org with per-function routing. The backend
owns the registry of routable functions; each call resolves its route row
(falling back to the org's default provider), reads the API key from
Vault over the API's direct Postgres connection — key material never
crosses PostgREST or reaches a browser — and calls the provider through
litellm. A failed routed call is retried once on the default target.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import litellm
import psycopg

from .config import Settings
from .supabase import postgrest_get

logger = logging.getLogger(__name__)

# llm_providers.provider_type -> litellm model prefix
_LITELLM_PREFIX = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "gemini",
    "groq": "groq",
    "ollama": "ollama",
}

# Registry of routable thinking functions (US-3.17, US-5.17). Keys are what
# llm_function_routes.function_key stores; the settings UI renders this
# list verbatim, so a new entry here appears there automatically and
# runs on the default provider until mapped.
#
# US-5.17: each entry also carries its prompt metadata — `variables` (the
# only {placeholders} the code supplies at call time; composition stays
# code-owned) and `template` (the canonical factory default; None for
# prd_draft, which dispatches into the worker pool where its behavioral
# text is the us-5.14 worker-instruction template). Templates here use
# PLAIN braces for placeholders; literal braces (JSON examples) are left
# as-is — rendering substitutes known variables only, never str.format.
LLM_FUNCTIONS: dict[str, dict[str, Any]] = {
    "prd_draft": {
        "label": "PRD draft",
        "description": "Draft a PRD from a feature issue.",
        "variables": [],
        "template": None,
    },
    # US-18.1: a very short, summary-only digest of a work-item content block.
    "content_tldr": {
        "label": "Content TLDR",
        "description": "Summarize a story / PRD / plan / test plan in a headline + bullets.",
        "variables": ["kind_label", "content"],
        "template": (
            "You write an extremely short TLDR of the {kind_label} below. "
            "SUMMARIZE ONLY: restate what it already says, in far fewer words. "
            "Do NOT analyze, critique, judge, recommend, or add any information "
            "that is not present in the source. Bullets restate; they never "
            "extend. Respond with ONLY a JSON object of the form "
            '{"headline": "a few words", "bullets": ["...", "..."]} — a 2-6 word '
            "headline and 3-6 short bullet points, nothing else.\n\n"
            "--- {kind_label} ---\n{content}"
        ),
    },
    # US-25.3: a summary of a WHOLE work item, not of one content block.
    # The manager's question opening an item they have not read in a week is
    # "what is this", and answering it from the story text alone leaves out
    # the plan they are about to approve.
    "work_item_tldr": {
        "label": "Work item TLDR",
        "description": "Summarize a whole work item — a feature and its PRD, or a story with its plan.",
        "variables": ["type_label", "sources", "missing_note"],
        "template": (
            "You write a short TLDR of the {type_label} below, for a manager "
            "who has not read it in a week. SUMMARIZE ONLY: restate what the "
            "sources already say, in far fewer words. Do NOT analyze, "
            "critique, judge, recommend, or add anything not present in the "
            "sources.\n\n"
            "Several sources are given, each under its own heading. Cover the "
            "item as a whole rather than summarizing each source separately.\n"
            "{missing_note}"
            "Respond with ONLY a JSON object of the form "
            '{"headline": "a few words", "bullets": ["...", "..."]} — a 2-8 '
            "word headline and 3-7 short bullet points, nothing else.\n\n"
            "{sources}"
        ),
    },
    "story_breakdown": {
        "label": "Story breakdown",
        "description": "Split an approved PRD into engineering stories.",
        # breakdown_directives: the feature's standing mode + manager
        # instructions (US-2.28) — empty for 'automatic' with no notes.
        "variables": ["breakdown_directives"],
        "template": (
            "Split a PRD into self-contained engineering stories. Respond with ONLY "
            'a JSON array of objects: {"title","body","acceptance_criteria":[...]}'
            "{breakdown_directives}"
        ),
    },
    "test_case_elaborate": {
        "label": "Test-case elaboration",
        "description": "Expand a rough test description into a manual test case.",
        "variables": ["description", "context_block"],
        "template": """You write manual test cases for a software product.

Expand the rough test description below into a concrete manual test case.
Respond with ONLY a JSON object, no prose, no code fences:
{"title": "<concise test title>",
  "steps": "<numbered markdown steps a human tester follows>",
  "expected_result": "<what the tester should observe when it passes>"}

Rough description:
{description}
{context_block}""",
    },
    "learnings_merge": {
        "label": "Learnings merge",
        "description": "Merge new context into the project learnings document.",
        "variables": ["existing", "context"],
        "template": """You maintain a running "lessons learned" markdown \
document for a software project — a single freeform doc, not a section catalog.

Merge the new context below into the existing document: append genuinely new
points, update anything that's now stale, and avoid duplicating what's
already there. Keep your own structure (headings/bullets) as you see fit.

Respond with ONLY the full updated markdown document — no prose, no code
fences, no commentary before or after it.

Existing document:
{existing}

New context to incorporate:
{context}""",
    },
    "deploy_script_generate": {
        "label": "Deploy-script generation",
        "description": "Draft a deployment script from the project and deployment config.",
        "variables": [
            "env_var_names",
            "project_name",
            "project_description",
            "repo_full_name",
            "default_branch",
            "guidelines",
            "deployment_name",
            "branch",
            "target_folder",
            "source_folder",
            "strategy",
            "keep_releases",
            "run_timeout_minutes",
            "health_check_url",
        ],
        "template": """You write POSIX shell deployment scripts for a \
software factory that ships a project's files to a server, then runs the \
script.

Execution contract (the script MUST fit this — do not invent a different \
runner):
- Invoked as `sh -e` (errexit) with stdin = the script body.
- Working directory is the deploy target folder (or the new release folder \
when strategy is "releases").
- In releases mode these are already exported: SF_RELEASE_PATH (this \
release's directory) and SF_TARGET (the long-lived target folder). \
Long-lived config/symlinks should point at `$SF_TARGET/current`, not a \
specific release path.
- The following environment variable NAMES (values are injected at run \
time — never invent secrets) are exported before your script runs: \
{env_var_names}.
- Prefer portable POSIX `sh`. No interactive prompts. Fail fast on errors.

Respond with ONLY the shell script body — no markdown fences, no prose \
before or after.

Project overview:
- Name: {project_name}
- Description: {project_description}
- Repo: {repo_full_name}
- Default branch: {default_branch}

Project guidelines:
{guidelines}

Deployment configuration (in-form draft — may be unsaved):
- Name: {deployment_name}
- Branch: {branch}
- Target folder: {target_folder}
- Source folder (repo subfolder, empty = whole repo): {source_folder}
- Strategy: {strategy}
- Keep releases: {keep_releases}
- Run timeout (minutes): {run_timeout_minutes}
- Health check URL (optional): {health_check_url}
""",
    },
    "story_complexity_score": {
        "label": "Complexity score — from the story",
        "description": "Estimate a work item's complexity from its title and spec (advisory).",
        "variables": ["item_type", "title", "details"],
        "template": """You estimate the complexity of a software work item for \
triage. You are estimating from INTENT ALONE — the title and spec below, no \
implementation plan yet — so infer conservatively.

Work item:
- Type: {item_type}
- Title: {title}
- Spec:
{details}

Respond with ONLY a JSON object, no prose, no code fences:
{"complexity": "<trivial|low|medium|high>", "touches_critical": <true|false>, \
"data_model_impact": "<none|backward_compatible|needs_migration>", \
"rationale": "<one sentence>"}

Vocabulary:
- complexity: trivial = a cosmetic/one-spot change (a CSS tweak, a copy \
change, a single button); low = a small localized change; medium = several \
files or a non-trivial feature; high = cross-cutting or subtle-invariant work.
- touches_critical: true when the work touches RLS, auth, secrets/Vault, or \
security-definer SQL.
- data_model_impact: none = no schema change; backward_compatible = \
ADDITIVE ONLY (a new nullable column or new table); needs_migration = \
transforms existing data (rename, backfill, drop, type/constraint change). \
Additive-only changes ARE backward_compatible — do not collapse everything to \
needs_migration. But on genuine ambiguity about existing data, pick \
needs_migration (fail-safe).""",
    },
    "plan_complexity_score": {
        "label": "Complexity score — from the plan",
        "description": "Refine a work item's complexity estimate from its implementation plan (advisory).",
        "variables": [
            "item_type",
            "title",
            "details",
            "implementation_plan",
            "test_plan",
        ],
        "template": """You estimate the complexity of a software work item for \
triage. A concrete implementation plan exists — read it; the plan states which \
files/services change, sequencing, dependencies, and risks, so this is a read, \
not a guess.

Work item:
- Type: {item_type}
- Title: {title}
- Spec:
{details}

Implementation plan:
{implementation_plan}

Test plan:
{test_plan}

Respond with ONLY a JSON object, no prose, no code fences:
{"complexity": "<trivial|low|medium|high>", "touches_critical": <true|false>, \
"data_model_impact": "<none|backward_compatible|needs_migration>", \
"rationale": "<one sentence>"}

Vocabulary:
- complexity: trivial = cosmetic/one-spot; low = small localized; medium = \
several files or a non-trivial feature; high = cross-cutting or \
subtle-invariant work.
- touches_critical: true when the work touches RLS, auth, secrets/Vault, or \
security-definer SQL.
- data_model_impact: none = no schema change; backward_compatible = ADDITIVE \
ONLY (new nullable column/table); needs_migration = transforms existing data \
(rename, backfill, drop, type/constraint change). Additive-only IS \
backward_compatible; on genuine ambiguity about existing data pick \
needs_migration (fail-safe).""",
    },
}


# ------------------------------------------------- prompt templates (US-5.17)
# The superadmin can override any registry template; absence of a row (or
# blank content) means factory default. Rendering substitutes ONLY the
# entry's declared variables — other {braces} (JSON examples) stay literal —
# and a bad override never breaks a feature: it logs and falls back.

PLACEHOLDER_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def extract_placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(text))


def render_template(text: str, variables: dict[str, Any]) -> str:
    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(variables[name]) if name in variables else match.group(0)

    return PLACEHOLDER_RE.sub(_sub, text)


# Phase 67: these three functions are project-shaped — a project's own
# template-seeded worker_instructions row (US-67.1) is the base text when a
# project_id is given, taking the place of the superadmin's global override
# for that one call. Falls back to the global override, then the registry
# default, exactly as before when no project-specific content exists.
PROJECT_SCOPED_PROMPT_FUNCTIONS = {
    "test_case_elaborate",
    "deploy_script_generate",
}


def resolve_prompt(
    settings: Settings,
    function_key: str,
    variables: dict[str, Any] | None = None,
    project_id: str | None = None,
) -> str:
    """The effective prompt text for a thinking function — a project's own
    template-seeded content when project_id is given (US-67.1), else the
    superadmin's global override, else the registry default. A live read: an
    edit is visible on the very next call, no redeploy."""
    from . import db  # local import — db.py must stay import-light

    variables = variables or {}
    entry = LLM_FUNCTIONS.get(function_key)
    default = entry.get("template") if entry else None
    if default is None:
        raise KeyError(f"no prompt template registered for '{function_key}'")

    override: str | None = None
    if project_id and function_key in PROJECT_SCOPED_PROMPT_FUNCTIONS:
        try:
            override = db.get_worker_instruction(settings, project_id, function_key)
        except Exception as e:  # noqa: BLE001 — template lookup must never break a call
            logger.warning(
                "project template lookup failed for %s/%s: %s", function_key, project_id, e
            )
    if not override:
        try:
            override = db.get_prompt_override(settings, function_key)
        except Exception as e:  # noqa: BLE001 — template lookup must never break a call
            logger.warning("prompt override lookup failed for %s: %s", function_key, e)
    if override:
        unknown = extract_placeholders(override) - set(variables)
        if unknown:
            logger.warning(
                "prompt override for %s uses unsupplied placeholders %s — "
                "falling back to the factory default",
                function_key,
                sorted(unknown),
            )
        else:
            return render_template(override, variables)
    return render_template(default, variables)


class LlmNotConfigured(Exception):
    pass


class LlmCallError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class LlmResult:
    text: str
    provider_name: str
    provider_type: str
    model: str
    used_fallback: bool


def read_vault_secret(settings: Settings, secret_id: str) -> str | None:
    with psycopg.connect(settings.database_url) as conn:
        row = conn.execute(
            "select decrypted_secret from vault.decrypted_secrets where id = %s",
            (secret_id,),
        ).fetchone()
    return row[0] if row else None


def read_claude_subscription_token(settings: Settings, org_id: str) -> str | None:
    """US-52.2: the org's factory-held Claude subscription token, or None.

    Read server-side only, and handed to a runner over its control socket for a
    subscription-mode Claude run. The reply is the only place it transits —
    callers must never log or echo it, and a lookup failure's message never
    contains it (there is nothing to contain: the failure is the lookup).
    """
    with psycopg.connect(settings.database_url) as conn:
        row = conn.execute(
            "select vault_secret_id from public.claude_subscriptions where org_id = %s",
            (org_id,),
        ).fetchone()
    if not row or not row[0]:
        return None
    return read_vault_secret(settings, str(row[0]))


async def _fetch_org_llm(
    settings: Settings, user_token: str
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    providers = await postgrest_get(
        settings,
        user_token,
        "llm_providers",
        {
            "select": "id,org_id,name,provider_type,base_url,models,"
            "is_default,default_model,vault_secret_id"
        },
    )
    routes = await postgrest_get(
        settings,
        user_token,
        "llm_function_routes",
        {"select": "function_key,provider_id,model"},
    )
    return providers, {r["function_key"]: r for r in routes}


def _targets_for(
    function_key: str,
    providers: list[dict[str, Any]],
    routes: dict[str, dict[str, Any]],
) -> list[tuple[dict[str, Any], str]]:
    """Ordered call targets: the function's route when still valid, then the
    org default when it's a different (provider, model) pair."""
    by_id = {p["id"]: p for p in providers}
    targets: list[tuple[dict[str, Any], str]] = []

    route = routes.get(function_key)
    if route:
        provider = by_id.get(route["provider_id"])
        if provider and route["model"] in (provider.get("models") or []):
            targets.append((provider, route["model"]))

    default = next((p for p in providers if p.get("is_default")), None)
    if default and default.get("default_model"):
        default_pair = (default["id"], default["default_model"])
        routed_pair = (targets[0][0]["id"], targets[0][1]) if targets else None
        if routed_pair != default_pair:
            targets.append((default, default["default_model"]))

    if not targets:
        raise LlmNotConfigured()
    return targets


async def complete(
    settings: Settings,
    user_token: str,
    function_key: str,
    *,
    messages: list[dict[str, str]],
    temperature: float | None = None,
    timeout: int | None = None,
) -> LlmResult:
    """Single choke point for thinking calls: resolve the function's route,
    call it, and fail over once to the default target."""
    providers, routes = await _fetch_org_llm(settings, user_token)
    return await _complete_with(
        settings,
        function_key,
        providers,
        routes,
        messages=messages,
        temperature=temperature,
        timeout=timeout,
    )


async def complete_as_org(
    settings: Settings,
    org_id: str,
    function_key: str,
    *,
    messages: list[dict[str, str]],
    temperature: float | None = None,
    timeout: int | None = None,
) -> LlmResult:
    """The same choke point for worker-context calls (US-5.6): MCP workers
    carry no user JWT, so the org's provider/route rows come over the
    API's direct Postgres connection instead of PostgREST."""
    from . import db  # local import — db.py must stay import-light

    providers, routes = db.get_org_llm_config(settings, org_id)
    return await _complete_with(
        settings,
        function_key,
        providers,
        routes,
        messages=messages,
        temperature=temperature,
        timeout=timeout,
    )


async def _complete_with(
    settings: Settings,
    function_key: str,
    providers: list[dict[str, Any]],
    routes: dict[str, dict[str, Any]],
    *,
    messages: list[dict[str, str]],
    temperature: float | None = None,
    timeout: int | None = None,
) -> LlmResult:
    targets = _targets_for(function_key, providers, routes)

    attempted = False
    errors: list[str] = []
    for i, (provider, model) in enumerate(targets):
        prefix = _LITELLM_PREFIX.get(provider["provider_type"])
        if not prefix:
            errors.append(
                f'{provider["name"]}: unknown provider type "{provider["provider_type"]}"'
            )
            continue

        api_key = None
        if provider.get("vault_secret_id"):
            api_key = read_vault_secret(settings, provider["vault_secret_id"])
        if not api_key and provider["provider_type"] != "ollama":
            errors.append(f'{provider["name"]}: no API key stored')
            continue

        kwargs: dict[str, Any] = {
            "model": f"{prefix}/{model}",
            "messages": messages,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if timeout is not None:
            kwargs["timeout"] = timeout
        if api_key:
            kwargs["api_key"] = api_key
        if provider.get("base_url"):
            kwargs["api_base"] = provider["base_url"]

        attempted = True
        try:
            resp = await litellm.acompletion(**kwargs)
        except Exception as e:  # litellm raises provider-specific exceptions
            errors.append(f'{provider["name"]}/{model}: {e}')
            if i + 1 < len(targets):
                logger.warning(
                    "llm %s: %s/%s failed (%s) — falling back to the default target",
                    function_key,
                    provider["name"],
                    model,
                    e,
                )
            continue

        if i > 0:
            logger.warning(
                "llm %s: served by fallback %s/%s",
                function_key,
                provider["name"],
                model,
            )
        return LlmResult(
            text=resp.choices[0].message.content or "",
            provider_name=provider["name"],
            provider_type=provider["provider_type"],
            model=model,
            used_fallback=i > 0,
        )

    if not attempted:
        # Nothing was callable at all (e.g. the default has no key): the
        # org is effectively unconfigured, not mid-call failing.
        raise LlmNotConfigured()
    raise LlmCallError("; ".join(errors))


def _parse_json_reply(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise LlmCallError("provider returned no JSON object")
    return json.loads(cleaned[start : end + 1])


def _learnings_prompt(settings: Settings, existing_content: str, context: str) -> str:
    return resolve_prompt(
        settings,
        "learnings_merge",
        {
            "existing": existing_content.strip() or "(nothing yet)",
            "context": context.strip(),
        },
    )


def _clean_learnings_reply(text: str) -> str:
    cleaned = re.sub(r"^```(markdown)?|```$", "", text, flags=re.MULTILINE).strip()
    if not cleaned:
        raise LlmCallError("provider returned an empty document")
    return cleaned


async def merge_learnings(
    settings: Settings,
    user_token: str,
    existing_content: str,
    context: str,
) -> str:
    prompt = _learnings_prompt(settings, existing_content, context)
    result = await complete(
        settings,
        user_token,
        "learnings_merge",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        timeout=60,
    )
    return _clean_learnings_reply(result.text)


async def merge_learnings_as_org(
    settings: Settings,
    org_id: str,
    existing_content: str,
    context: str,
) -> str:
    """US-5.6: the same merge pipeline, entered from a worker context (no
    user JWT) — MCP submit_learning flows through here."""
    prompt = _learnings_prompt(settings, existing_content, context)
    result = await complete_as_org(
        settings,
        org_id,
        "learnings_merge",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        timeout=60,
    )
    return _clean_learnings_reply(result.text)


async def elaborate_test_case(
    settings: Settings,
    user_token: str,
    description: str,
    context: str | None = None,
    project_id: str | None = None,
) -> dict[str, str]:
    prompt = resolve_prompt(
        settings,
        "test_case_elaborate",
        {
            "description": description.strip(),
            "context_block": (
                f"\nRelated story/context:\n{context.strip()}" if context else ""
            ),
        },
        project_id=project_id,
    )
    result = await complete(
        settings,
        user_token,
        "test_case_elaborate",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        timeout=60,
    )

    try:
        parsed = _parse_json_reply(result.text)
    except json.JSONDecodeError as e:
        raise LlmCallError(f"provider returned invalid JSON: {e}")

    parsed_result = {
        "title": str(parsed.get("title", "")).strip(),
        "steps": str(parsed.get("steps", "")).strip(),
        "expected_result": str(parsed.get("expected_result", "")).strip(),
    }
    if not parsed_result["steps"]:
        raise LlmCallError("provider reply had no steps")
    return parsed_result


_TLDR_LABELS = {
    "story": "story",
    "prd": "PRD",
    "plan": "implementation plan",
    "test_plan": "test plan",
}


async def summarize_content(
    settings: Settings,
    user_token: str,
    content: str,
    kind: str,
) -> dict[str, Any]:
    """US-18.1: a very short, summary-only TLDR of a work-item content block —
    a headline plus a few bullets, produced by the org's configured LLM. Raises
    LlmNotConfigured / LlmCallError, handled by the endpoint."""
    prompt = resolve_prompt(
        settings,
        "content_tldr",
        {
            "kind_label": _TLDR_LABELS.get(kind, "content"),
            # Bound the payload so a huge plan can't blow the context window.
            "content": content.strip()[:24000],
        },
    )
    result = await complete(
        settings,
        user_token,
        "content_tldr",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        timeout=60,
    )
    try:
        parsed = _parse_json_reply(result.text)
    except json.JSONDecodeError as e:
        raise LlmCallError(f"provider returned invalid JSON: {e}")
    headline = str(parsed.get("headline", "")).strip()
    raw = parsed.get("bullets", [])
    bullets = (
        [str(b).strip() for b in raw if str(b).strip()]
        if isinstance(raw, list)
        else []
    )
    if not headline and not bullets:
        raise LlmCallError("provider reply had no summary")
    return {"headline": headline, "bullets": bullets}


# US-25.3: how much of each source the summary prompt may carry. Generous per
# source but bounded overall, so a feature with a 40-page PRD still leaves room
# for its description.
_WORK_ITEM_SOURCE_BUDGET = 12000


async def summarize_work_item(
    settings: Settings,
    org_id: str,
    type_label: str,
    sources: list[tuple[str, str]],
    missing: list[str],
) -> dict[str, Any]:
    """US-25.3: summarize a whole work item from its several sources.

    `sources` is (heading, text) in the order they should be read; `missing` names
    the sources that do not exist yet. The missing ones are named IN the summary
    rather than silently dropped — a story with no approved plan would otherwise
    produce a thinner summary the manager cannot tell apart from a complete one.

    Runs as the org rather than as a user: this is called from a background task
    that outlives its request, and must not depend on the caller's JWT still
    being alive when the model answers.
    """
    blocks = "\n\n".join(
        f"--- {heading} ---\n{text.strip()[:_WORK_ITEM_SOURCE_BUDGET]}"
        for heading, text in sources
        if text and text.strip()
    )
    missing_note = (
        "The following are not written yet: "
        + ", ".join(missing)
        + ". Say so plainly in a final bullet — do not pretend the item is "
        "more complete than it is.\n\n"
        if missing
        else ""
    )
    prompt = resolve_prompt(
        settings,
        "work_item_tldr",
        {
            "type_label": type_label,
            "sources": blocks,
            "missing_note": missing_note,
        },
    )
    result = await complete_as_org(
        settings,
        org_id,
        "work_item_tldr",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        timeout=90,
    )
    try:
        parsed = _parse_json_reply(result.text)
    except json.JSONDecodeError as e:
        raise LlmCallError(f"provider returned invalid JSON: {e}")
    headline = str(parsed.get("headline", "")).strip()
    raw = parsed.get("bullets", [])
    bullets = (
        [str(b).strip() for b in raw if str(b).strip()]
        if isinstance(raw, list)
        else []
    )
    if not headline and not bullets:
        raise LlmCallError("provider reply had no summary")
    return {"headline": headline, "bullets": bullets}


def _strip_script_fences(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:sh|bash|shell)?\s*\n?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n?```$", "", cleaned)
    return cleaned.strip()


async def generate_deploy_script(
    settings: Settings,
    user_token: str,
    *,
    project_name: str,
    project_description: str | None,
    repo_full_name: str,
    default_branch: str,
    guidelines: str,
    deployment_name: str,
    branch: str,
    target_folder: str,
    source_folder: str,
    strategy: str,
    keep_releases: int,
    run_timeout_minutes: int,
    health_check_url: str | None,
    env_var_names: list[str],
    project_id: str | None = None,
) -> dict[str, str]:
    """Draft a deployment script. Returns {script, model, provider} — the
    model/provider that actually served the call, never secrets."""
    names = [n.strip() for n in env_var_names if isinstance(n, str) and n.strip()]
    # Defense in depth: never accept values-shaped pairs.
    names = [n for n in names if "=" not in n and "\n" not in n][:64]

    prompt = resolve_prompt(
        settings,
        "deploy_script_generate",
        {
            "env_var_names": ", ".join(names) if names else "(none)",
            "project_name": project_name or "(unnamed)",
            "project_description": (project_description or "").strip() or "(none)",
            "repo_full_name": repo_full_name or "(unknown)",
            "default_branch": default_branch or "main",
            "guidelines": (guidelines or "").strip() or "(no guidelines yet)",
            "deployment_name": deployment_name or "(unnamed)",
            "branch": branch or default_branch or "main",
            "target_folder": target_folder or "(unset)",
            "source_folder": source_folder or "(whole repo)",
            "strategy": strategy if strategy in ("releases", "inplace") else "releases",
            "keep_releases": keep_releases,
            "run_timeout_minutes": run_timeout_minutes,
            "health_check_url": (health_check_url or "").strip() or "(none)",
        },
        project_id=project_id,
    )

    result = await complete(
        settings,
        user_token,
        "deploy_script_generate",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        timeout=90,
    )

    text = _strip_script_fences(result.text)
    if not text:
        raise LlmCallError("provider returned an empty script")
    return {"script": text, "model": result.model, "provider": result.provider_name}


# Mirrors apps/web/src/lib/project-guidelines-catalog.ts (US-1.18). Kept as a
# plain literal here — no cross-language import exists in this codebase, and
# only the title/guidance are needed to ground the chatbot's prompt.
_GUIDELINE_CATALOG = [
    ("tech-stack", "Tech stack", "Languages, frameworks, key libraries; versions where they matter."),
    ("commands", "Commands", "Exact commands for build, test, lint, dev server, migrations."),
    ("run-commands", "Run commands", "How an agent verifies its work before submitting: build, test, and lint commands (plus setup/install)."),
    ("code-style", "Code style and conventions", "Naming, formatting, preferred patterns."),
    ("things-to-avoid", "Things to avoid", "Known footguns, deprecated patterns, files not to touch."),
    ("overview", "Project overview", "A few sentences on what the project is and does."),
    ("architecture", "Architecture notes", "How the pieces fit, where core logic lives."),
    ("file-structure", "File/directory structure", "Only if non-standard or large."),
    ("testing", "Testing expectations", "How tests are run, what should be tested."),
    ("environment", "Environment setup", "Env vars, secrets handling, local quirks."),
    ("git-pr", "Git/PR conventions", "Branch naming, commit format, PRs vs direct push."),
    ("monorepo", "Monorepo/multi-package notes", "Which commands run at root vs inside a package."),
    ("doc-links", "Links to other docs", "Point to ADRs, API specs, design docs."),
    ("known-issues", "Known issues or WIP areas", "Modules mid-refactor or intentionally messy."),
    ("boundaries", "Permissions or boundaries", "e.g. never modify /generated, ask before adding deps."),
    ("preferred-libs", "Preferred libraries", "Explicit picks over alternatives."),
    ("good-patterns", "Examples of good patterns", "Point at specific files as reference implementations."),
    ("agent-workflows", "Subagent or workflow notes", "Custom slash commands, subagents, workflows."),
    ("release", "Versioning & Release", "The V<epic>.<seq> version scheme, tagging, release notes, UAT→Production promotion."),
    ("buildmill-workflow", "Working with Build Mill", "How development flows through Build Mill (factory-seeded)."),
]

_COMPLEXITY_VALUES = {"trivial", "low", "medium", "high"}
_DATA_MODEL_VALUES = {"none", "backward_compatible", "needs_migration"}


async def score_complexity(
    settings: Settings,
    org_id: str,
    *,
    basis: str,
    item_type: str,
    title: str,
    details: str,
    implementation_plan: str = "",
    test_plan: str = "",
) -> dict[str, Any]:
    """US-7.1: score a work item's complexity (advisory). basis 'plan' reads
    the implementation plan; anything else scores from the story. Runs through
    complete_as_org (no user JWT). Returns the validated fields plus 'model';
    raises LlmNotConfigured / LlmCallError on failure (the caller is
    best-effort and swallows these)."""
    if basis == "plan":
        fn = "plan_complexity_score"
        variables = {
            "item_type": item_type,
            "title": title,
            "details": details or "(none)",
            "implementation_plan": implementation_plan or "(none)",
            "test_plan": test_plan or "(none)",
        }
    else:
        fn = "story_complexity_score"
        variables = {
            "item_type": item_type,
            "title": title,
            "details": details or "(none)",
        }
    system = resolve_prompt(settings, fn, variables)
    result = await complete_as_org(
        settings,
        org_id,
        fn,
        messages=[{"role": "system", "content": system}],
        temperature=0.0,
        timeout=60,
    )
    try:
        parsed = _parse_json_reply(result.text)
    except json.JSONDecodeError as e:
        raise LlmCallError(f"provider returned invalid JSON: {e}")
    complexity = str(parsed.get("complexity", "")).strip().lower()
    dmi = str(parsed.get("data_model_impact", "")).strip().lower()
    if complexity not in _COMPLEXITY_VALUES or dmi not in _DATA_MODEL_VALUES:
        raise LlmCallError("complexity reply failed enum validation")
    return {
        "complexity": complexity,
        "touches_critical": bool(parsed.get("touches_critical")),
        "data_model_impact": dmi,
        "rationale": str(parsed.get("rationale", "")).strip()[:2000],
        "model": result.model,
    }
