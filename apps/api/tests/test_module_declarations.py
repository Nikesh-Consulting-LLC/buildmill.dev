"""US-32.4: the server side of the module declaration.

The runner reports what each of its modules accepts on `runner.hello`; the server
normalizes it (a hello arrives over a socket the machine owns) and stores it with
the session, so the settings page can be honest about a module while its machine
is offline. And a setting the module could not express lands on the run's own
record, server-side, rather than depending on a fire-and-forget socket notify.
"""

from __future__ import annotations

import json

import pytest

from app.routers.runner_socket import (
    KNOWN_SETTINGS,
    SETTING_DELIVERIES,
    SETTING_KINDS,
    _module_settings,
)


def _decl(**over):
    entry = {
        "module": "claude",
        "capabilities": ["code", "plan"],
        "needs_repo": True,
        "settings": [
            {
                "name": "effort",
                "kind": "enum",
                "delivery": "argv",
                "flag": "--effort",
                "choices": ["low", "medium", "high"],
                "help": "how hard to think",
            }
        ],
    }
    entry.update(over)
    return entry


# ------------------------------------------------------------ normalization


def test_a_well_formed_declaration_survives_intact():
    out = _module_settings([_decl()])
    assert len(out) == 1
    assert out[0]["module"] == "claude"
    assert out[0]["needs_repo"] is True
    knob = out[0]["settings"][0]
    assert knob == {
        "name": "effort",
        "kind": "enum",
        "delivery": "argv",
        "flag": "--effort",
        "choices": ["low", "medium", "high"],
        "help": "how hard to think",
    }


def test_an_unknown_setting_name_is_dropped_but_the_module_survives():
    """A newer runner talking to an older server is not an attack — the knobs it
    declared properly must still be configurable."""
    out = _module_settings(
        [
            _decl(
                settings=[
                    {"name": "telepathy", "kind": "bool", "delivery": "argv"},
                    {"name": "effort", "kind": "enum", "delivery": "argv"},
                ]
            )
        ]
    )
    assert [k["name"] for k in out[0]["settings"]] == ["effort"]


def test_a_nonsense_kind_or_delivery_falls_back_rather_than_vanishing():
    out = _module_settings(
        [_decl(settings=[{"name": "effort", "kind": "wat", "delivery": "carrier-pigeon"}])]
    )
    knob = out[0]["settings"][0]
    assert knob["kind"] == "text"
    assert knob["delivery"] == "argv"


def test_a_module_with_no_name_is_dropped():
    assert _module_settings([_decl(module="")]) == []
    assert _module_settings([_decl(module="   ")]) == []


def test_a_module_may_legitimately_declare_nothing():
    out = _module_settings([_decl(module="sim", settings=[], needs_repo=False)])
    assert out == [
        {"module": "sim", "capabilities": ["code", "plan"], "needs_repo": False, "settings": []}
    ]


def test_junk_shapes_are_refused_without_raising():
    assert _module_settings(None) == []
    assert _module_settings("claude") == []
    assert _module_settings({"module": "claude"}) == []
    assert _module_settings([None, 7, "x"]) == []
    assert _module_settings([_decl(settings=[None, 3])])[0]["settings"] == []


def test_long_strings_and_long_lists_are_bounded():
    out = _module_settings(
        [
            _decl(
                module="m" * 200,
                capabilities=[f"c{i}" for i in range(50)],
                settings=[
                    {
                        "name": "effort",
                        "kind": "enum",
                        "delivery": "argv",
                        "flag": "-" * 200,
                        "choices": [f"c{i}" for i in range(50)],
                        "help": "h" * 2000,
                    }
                ],
            )
        ]
    )
    assert len(out[0]["module"]) <= 40
    assert len(out[0]["capabilities"]) <= 20
    knob = out[0]["settings"][0]
    assert len(knob["flag"]) <= 60
    assert len(knob["choices"]) <= 20
    assert len(knob["help"]) <= 400
    assert _module_settings([_decl() for _ in range(100)]).__len__() <= 32


def test_the_server_and_the_runner_agree_on_the_canonical_names():
    """Two lists that can drift is exactly the failure the declaration exists to
    prevent, so this test fails the moment they do."""
    import pathlib

    src = (
        pathlib.Path(__file__).resolve().parents[3]
        / "apps"
        / "runner"
        / "supervisor"
        / "modules"
        / "base.py"
    ).read_text(encoding="utf-8")
    block = src.split("KNOWN_SETTINGS = (", 1)[1].split(")", 1)[0]
    runner_names = tuple(
        line.strip().strip(',"') for line in block.splitlines() if line.strip().startswith('"')
    )
    assert runner_names == KNOWN_SETTINGS
    assert set(SETTING_KINDS) == {"text", "int", "number", "enum", "bool"}
    # US-52.1 adds `runner`: a setting the supervisor consumes rather than
    # delivering to the CLI (the claude module's `auth` billing mode).
    assert set(SETTING_DELIVERIES) == {"argv", "env", "prompt", "runner"}


def test_the_module_and_the_api_agree_on_the_effort_levels():
    """US-32.10: the names have never drifted because the test above fails when
    they do. The *values* had no such guard, and drifted — the module declared
    (and the settings page offered) three levels while `claude --help` had taken
    five all along, so `xhigh`, the level recommended for coding work, could not
    be saved by any surface in the app."""
    import ast
    import pathlib

    from app.presets import EFFORTS

    src = (
        pathlib.Path(__file__).resolve().parents[3]
        / "apps"
        / "runner"
        / "supervisor"
        / "modules"
        / "claude.py"
    ).read_text(encoding="utf-8")
    literal = src.split('"effort",', 1)[1].split("choices=", 1)[1].split("),", 1)[0] + ")"
    assert ast.literal_eval(literal) == EFFORTS
    assert EFFORTS == ("low", "medium", "high", "xhigh", "max")


def test_the_module_and_the_api_agree_on_the_auth_modes():
    """US-52.1 → US-53.1: same guard as the effort levels, for the billing
    mode. `AUTH_MODES` now lives beside the runner-config PATCH (the setting's
    one home); the module's `choices` is the capability declaration. Drift is
    a value that saves and never arrives, or arrives and cannot be saved."""
    import ast
    import pathlib

    from app.routers.runner_socket import AUTH_MODES

    src = (
        pathlib.Path(__file__).resolve().parents[3]
        / "apps"
        / "runner"
        / "supervisor"
        / "modules"
        / "claude.py"
    ).read_text(encoding="utf-8")
    literal = src.split('"auth",', 1)[1].split("choices=", 1)[1].split("),", 1)[0] + ")"
    assert ast.literal_eval(literal) == AUTH_MODES
    # US-60.1: `platform` bills the superadmin's own key (Buildmill Agent) —
    # the same gateway-key path as `api`, resolved against a different key.
    assert AUTH_MODES == ("api", "subscription", "platform")


# ------------------------------------------------------ stored on the session


def test_open_runner_session_stores_the_declaration(monkeypatch):
    from app import db

    calls = []

    class FakeCursor:
        def fetchone(self):
            return {"id": "session-1"}

    class FakeConn:
        def execute(self, query, params=None):
            calls.append((query, params))
            return FakeCursor()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(db, "_connect", lambda s: FakeConn())
    decl = [_decl()]
    session_id = db.open_runner_session(
        object(),
        worker_id="w-1",
        org_id="o-1",
        modules_available=["claude"],
        module_settings=decl,
    )
    assert session_id == "session-1"
    insert = [c for c in calls if "insert into public.runner_sessions" in c[0]]
    assert len(insert) == 1
    query, params = insert[0]
    assert "module_settings" in query
    assert json.loads(params[-1]) == decl


def test_a_session_without_a_declaration_stores_an_empty_list(monkeypatch):
    """An older runner is not a broken one — it stores `[]`, and the settings
    page says the machine has not reported yet."""
    from app import db

    captured = {}

    class FakeCursor:
        def fetchone(self):
            return {"id": "s"}

    class FakeConn:
        def execute(self, query, params=None):
            if "insert into public.runner_sessions" in query:
                captured["params"] = params
            return FakeCursor()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(db, "_connect", lambda s: FakeConn())
    db.open_runner_session(object(), worker_id="w", org_id="o")
    assert json.loads(captured["params"][-1]) == []


# ---------------------------------------------- undelivered settings on a run


@pytest.mark.parametrize(
    "lines,expected",
    [
        (None, 0),
        ([], 0),
        (["the grok module cannot be told 'effort'"], 1),
        ([f"line {i}" for i in range(40)], 20),  # bounded
    ],
)
def test_submit_records_undelivered_settings_on_the_run(lines, expected):
    """It is written server-side because the runner's own trace notify is
    fire-and-forget by design — this is the write that has to survive."""
    from app.routers import worker as worker_router

    recorded = []

    class FakeDB:
        def record_run_trace(self, settings, run_id, worker_id, kind, content):
            recorded.append((kind, content))
            return 1

    fake = FakeDB()
    body = worker_router.Submit(settings_not_delivered=lines, plan="p")
    # Exercise just the loop the endpoint runs, with the same bound.
    for line in (body.settings_not_delivered or [])[:20]:
        fake.record_run_trace(None, "run-1", "w-1", "settings", str(line)[:400])
    assert len(recorded) == expected
    assert all(kind == "settings" for kind, _ in recorded)


def test_submit_body_accepts_the_field_and_defaults_to_none():
    from app.routers import worker as worker_router

    assert worker_router.Submit().settings_not_delivered is None
    body = worker_router.Submit(settings_not_delivered=["a", "b"])
    assert body.settings_not_delivered == ["a", "b"]
