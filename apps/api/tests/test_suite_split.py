"""US-80.1: the two suites partition the tests, and neither can lose one.

The danger in splitting a suite is that something falls between the halves and
stops running anywhere — quietly, because a test that is never selected reports
nothing at all. So the rule is asserted directly rather than trusted.
"""

import socket
from pathlib import Path

import pytest

from conftest import is_full_qa_only

API = Path(__file__).resolve().parents[1]


def test_the_split_line_is_needing_a_database_not_being_inconvenient():
    assert is_full_qa_only("test_worker_pool_sql.py", set())
    assert is_full_qa_only("test_factory_mcp.py", {"needs_db"})
    assert is_full_qa_only("test_anything.py", {"slow"})
    # everything else runs in Essential, including the guards that catch this
    # project's real failures
    for name in (
        "test_embed_ambiguity.py",
        "test_console_columns.py",
        "test_run_cancel.py",
        "test_agent_sessions.py",
        "test_self_reporting.py",
    ):
        assert not is_full_qa_only(name, set()), f"{name} must stay in Essential"


def test_the_sql_convention_the_rule_leans_on_still_exists():
    """If the *_sql.py naming ever moved, the rule would guard nothing and
    Essential would silently start doing round trips to Supabase again."""
    assert len(list((API / "tests").glob("*_sql.py"))) > 20


def test_the_network_guard_is_active_in_essential(request):
    """The half hour was the network, not computation: route tests whose reads
    were not faked called https://test.supabase.co for real and waited on it.
    If this guard stops working the suite gets slow again silently.

    Full QA lifts the guard on purpose, so this asserts nothing there."""
    if request.config.getoption("--full"):
        pytest.skip("the guard is lifted in Full QA by design")
    with pytest.raises(socket.gaierror) as caught:
        socket.getaddrinfo("test.supabase.co", 443)
    assert "outbound network blocked" in str(caught.value)


def test_localhost_still_resolves_for_the_tests_that_bind_a_port():
    """The guard blocks outbound, not everything."""
    assert socket.getaddrinfo("127.0.0.1", 0)
