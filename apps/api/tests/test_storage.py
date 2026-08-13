"""Storage helper not-found handling (US-1.28).

Supabase Storage reports a missing object as HTTP 400 with a body of
``{"statusCode":"404","error":"not_found",...}`` rather than a literal 404.
An optional credential (e.g. an SSH key with no passphrase) must therefore
read back as absent, not blow up — regression guard for that.
"""

import httpx

from app.storage import _is_not_found


def _resp(status: int, body: str) -> httpx.Response:
    return httpx.Response(status_code=status, text=body)


def test_literal_404_is_not_found():
    assert _is_not_found(_resp(404, "")) is True


def test_supabase_400_with_404_body_is_not_found():
    body = '{"statusCode":"404","error":"not_found","message":"Object not found"}'
    assert _is_not_found(_resp(400, body)) is True


def test_message_not_found_is_detected():
    assert _is_not_found(_resp(400, '{"message":"Object not found"}')) is True


def test_real_error_is_not_treated_as_missing():
    body = '{"statusCode":"403","error":"Unauthorized","message":"permission denied"}'
    assert _is_not_found(_resp(403, body)) is False


def test_non_json_body_is_not_missing():
    assert _is_not_found(_resp(500, "<html>boom</html>")) is False
