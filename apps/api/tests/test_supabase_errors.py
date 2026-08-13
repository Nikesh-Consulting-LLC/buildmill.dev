"""BUG-1.1: what the PostgREST client does with an answer that is not 2xx.

The bug was a whole class of failure nobody had named. `raise_for_status()`
flagged the `300 Multiple Choices` PostgREST answers an ambiguous embed with,
but as a bare httpx error carrying a URL and no reason — so it reached the
browser as an unhandled 500 and the operator as a traceback. These pin the
three properties that were missing: a redirect counts as a failure, the
PostgREST code survives, and the error is still an `httpx.HTTPStatusError` for
the call sites that read a status off it.
"""

import httpx
import pytest

from app.supabase import (
    PostgrestError,
    PostgrestQueryError,
    raise_for_postgrest,
)

AMBIGUOUS = {
    "code": "PGRST201",
    "details": [],
    "hint": "Try changing 'projects' to one of the following:"
    " 'projects!deployments_project_id_org_id_fkey', 'projects!app_issues'.",
    "message": "Could not embed because more than one relationship was found"
    " for 'deployments' and 'projects'",
}


def _response(status: int, **kwargs) -> httpx.Response:
    request = httpx.Request("GET", "https://test.supabase.co/rest/v1/deployments")
    return httpx.Response(status, request=request, **kwargs)


def test_a_redirect_is_a_failure():
    """The whole bug in one assertion. A `>= 400` check would have handed the
    error body back as if it were a row of deployments."""
    with pytest.raises(PostgrestQueryError) as caught:
        raise_for_postgrest(_response(300, json=AMBIGUOUS))
    assert caught.value.code == "PGRST201"
    assert caught.value.status_code == 300


def test_the_hint_comes_with_it():
    """For PGRST201 the hint names the relationships to choose between, which
    is the fix — worth carrying to whoever reads the message."""
    with pytest.raises(PostgrestQueryError) as caught:
        raise_for_postgrest(_response(300, json=AMBIGUOUS))
    assert "more than one relationship" in caught.value.message
    assert "projects!deployments_project_id_org_id_fkey" in caught.value.message


@pytest.mark.parametrize("status", [400, 401, 409, 500, 503])
def test_every_error_status_still_raises(status):
    with pytest.raises(PostgrestQueryError):
        raise_for_postgrest(_response(status, json={"message": "no", "code": "42501"}))


@pytest.mark.parametrize("status", [200, 201, 204, 206])
def test_success_passes_through(status):
    raise_for_postgrest(_response(status, json=[]))


def test_the_existing_status_code_handlers_still_work():
    """`servers` and `deployments` translate their own 409s by reading the
    response off an `httpx.HTTPStatusError`. That has to keep working, or
    "server is used by deployment(s)" becomes a 502."""
    with pytest.raises(httpx.HTTPStatusError) as caught:
        raise_for_postgrest(_response(409, json={"code": "23503"}))
    assert caught.value.response.status_code == 409
    assert isinstance(caught.value, PostgrestError)


def test_a_body_that_is_not_json_still_produces_a_message():
    with pytest.raises(PostgrestQueryError) as caught:
        raise_for_postgrest(_response(502, text="<html>upstream down</html>"))
    assert "upstream down" in caught.value.message
    assert caught.value.code is None


def test_a_long_message_is_capped():
    with pytest.raises(PostgrestQueryError) as caught:
        raise_for_postgrest(
            _response(300, json={"code": "PGRST201", "message": "x" * 5000})
        )
    assert len(caught.value.message) <= 600
