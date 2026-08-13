import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ec import (
    SECP256R1,
    generate_private_key,
)
from fastapi.testclient import TestClient

from app import auth as auth_module
from app.config import Settings, get_settings
from app.main import app
from app.routers import github as github_router

TEST_USER_ID = "5a51817d-97eb-4e37-a065-7aad12370c96"
TEST_ORG_ID = "654d7ff1-ab30-4812-a1ff-c9588d91ad50"


@pytest.fixture(scope="session")
def keypair():
    private_key = generate_private_key(SECP256R1())
    return private_key, private_key.public_key()


@pytest.fixture()
def settings_override():
    settings = Settings(
        supabase_url="https://test.supabase.co",
        supabase_publishable_key="sb_publishable_test",
        cors_origins="http://localhost:3000",
        database_url="postgresql://test",
        # US-87.6: the pool waits for a free slot, which is right when it is
        # SATURATED and wrong when the database is simply unreachable — and
        # in Essential it always is (`postgresql://test` is refused instantly
        # by the outbound-network guard below). Without a short wait here,
        # every reaper in the app's lifespan blocks for the production
        # timeout on each TestClient construction and a 30-second suite
        # becomes an hour. The pooled code path is still exactly the one
        # that ships; only how long it waits to give up differs.
        db_pool_timeout_s=0.1,
        # No eagerly-opened connection to a database that isn't there.
        db_pool_min_size=0,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture()
def client(keypair, settings_override, monkeypatch):
    _, public_key = keypair
    # Stand in for the JWKS fetch: hand back our test public key.
    monkeypatch.setattr(
        auth_module, "get_signing_key", lambda token, settings: public_key
    )
    # US-76.3: every /github endpoint now resolves the caller's ACTIVE org
    # before doing anything, which is two reads no pre-existing test knew to
    # fake — without this they fall through to a real network call. A test that
    # cares about the org patches `app.routers.github.postgrest_get` itself;
    # its setattr runs after this one and wins.
    async def _org_reads(settings, token, path, params):
        if path == "organization_members":
            return [{"org_id": "org-1"}]
        if path == "principals":
            return [{"active_org_id": "org-1"}]
        raise AssertionError(
            f"unfaked postgrest_get on app.routers.github: {path} {params}"
        )

    monkeypatch.setattr(github_router, "postgrest_get", _org_reads)
    return TestClient(app)


@pytest.fixture()
def make_token(keypair):
    private_key, _ = keypair

    def _make(sub: str = TEST_USER_ID, email: str = "kaushlesh@nikesh.llc", **claims):
        now = int(time.time())
        payload = {
            "sub": sub,
            "email": email,
            "aud": "authenticated",
            "role": "authenticated",
            "iat": now,
            "exp": now + 3600,
            **claims,
        }
        return jwt.encode(payload, private_key, algorithm="ES256")

    return _make


# ---------------------------------------------------------------------------
# US-80.1: two suites — the one you run, and the one you call for
# ---------------------------------------------------------------------------
# The full suite takes ~30 minutes on the development machine, which is long
# enough that it gets skipped — and this project has already paid for that: a
# console shipped that had never worked, because the test that would have caught
# it faked the database call and nobody was going to re-run half an hour to find
# out. A slow suite is not a thorough suite; it is one that runs less often.
#
# Essential is the default and the gate after coding. Full QA is `--full`, and
# runs everything. Nothing is deleted: `slow` only moves a test between the two.


def pytest_addoption(parser):
    parser.addoption(
        "--full",
        action="store_true",
        default=False,
        help="Full QA: include tests marked `slow` (the default suite skips them)",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: measured as expensive; skipped by Essential, run by Full QA (--full)",
    )
    config.addinivalue_line(
        "markers",
        "needs_db: reaches a real Postgres; Full QA only (--full). Applied "
        "automatically to *_sql.py, and by hand where the dependency is less "
        "obvious from the filename.",
    )


def is_full_qa_only(basename: str, keywords) -> bool:
    """The split rule, as one function so it can be tested without spawning a
    second pytest. `*_sql.py` by convention, `needs_db` where the filename does
    not say it, `slow` for anything else measured expensive."""
    return (
        basename.endswith("_sql.py")
        or "needs_db" in keywords
        or "slow" in keywords
    )


def pytest_collection_modifyitems(config, items):
    """Essential holds back exactly two things, both measured, neither arbitrary.

    `*_sql.py` connects to a real Postgres through `DATABASE_URL` to exercise
    RPCs, RLS and indexes. Those are the slowest tests in the suite by a wide
    margin (13s, 11s, 9s at the top of `--durations`) because every one is a
    round trip to hosted Supabase, and they are the only tests that need a
    network at all. They are also the ones that cannot run on a machine without
    the credential — so Essential is what everybody can always run, and Full QA
    is what the database layer needs.

    `slow` is the manual escape hatch for anything else measured expensive.
    """
    if config.getoption("--full"):
        return
    held = pytest.mark.skip(reason="Full QA only — run with --full")
    for item in items:
        if is_full_qa_only(item.fspath.basename, item.keywords):
            item.add_marker(held)


# ---------------------------------------------------------------------------
# US-80.1: the suite was slow because it was on the network
# ---------------------------------------------------------------------------
# `settings_override` points PostgREST at https://test.supabase.co, and a route
# whose read is not faked calls it FOR REAL — reaching the network and waiting
# on it before failing into the refusal the test asserts on. The assertion
# passes either way; it just costs seconds instead of milliseconds, once per
# such test, across two thousand tests. Measured: test_worker_pool.py went from
# ~9s on its slowest test to 1.04s for all 43 with this guard in place, and the
# whole suite from ~30 minutes to ~30 seconds. Which network layer eats the time
# was not isolated — the fix is the same either way: do not go out at all.
#
# So outbound name resolution fails IMMEDIATELY instead of after a timeout. The
# tests' behaviour is unchanged (the same exception reaches the same handler and
# the same response comes back); only the waiting is gone. Anything that wanted
# a real network in a unit test was already lying about being a unit test.
#
# `--full` lifts it, so a test that genuinely needs the network can still be
# written and run deliberately.

_LOCAL = ("127.0.0.1", "localhost", "::1", "testserver")


@pytest.fixture(autouse=True)
def _no_outbound_network(request, monkeypatch):
    if request.config.getoption("--full"):
        return
    import socket

    real = socket.getaddrinfo

    def guard(host, port, *args, **kwargs):
        if str(host) in _LOCAL:
            return real(host, port, *args, **kwargs)
        raise socket.gaierror(
            f"outbound network blocked in tests (host={host}); fake this read, "
            "or run the suite with --full"
        )

    monkeypatch.setattr(socket, "getaddrinfo", guard)
