"""US-81.2: the JUnit parser — the one interface the suite pipeline speaks."""

import pytest

from app.junit import JUnitParseError, parse_junit, spec_ref

SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="4">
    <testcase classname="tests.test_api" name="test_health" time="0.123"/>
    <testcase classname="tests.test_api" name="test_login" time="1.5">
      <failure message="assert 401 == 200">traceback here</failure>
    </testcase>
    <testcase classname="tests.test_api" name="test_flaky">
      <error message="fixture blew up"/>
    </testcase>
    <testcase classname="tests.test_api" name="test_skipped">
      <skipped message="needs DATABASE_URL"/>
    </testcase>
  </testsuite>
</testsuites>
"""


def test_counts_and_refs():
    report = parse_junit(SAMPLE)
    assert report.total == 4
    assert report.passed == 1
    # An errored test is a failed test to the verdict.
    assert report.failed == 2
    assert report.skipped == 1
    refs = [t.spec_ref for t in report.tests]
    assert "tests.test_api::test_health" in refs
    assert "tests.test_api::test_login" in refs


def test_statuses_and_messages():
    by_ref = {t.spec_ref: t for t in parse_junit(SAMPLE).tests}
    assert by_ref["tests.test_api::test_health"].status == "pass"
    login = by_ref["tests.test_api::test_login"]
    assert login.status == "fail"
    assert "assert 401 == 200" in login.message
    assert by_ref["tests.test_api::test_flaky"].status == "error"
    assert by_ref["tests.test_api::test_skipped"].status == "skipped"


def test_time_becomes_milliseconds():
    by_ref = {t.spec_ref: t for t in parse_junit(SAMPLE).tests}
    assert by_ref["tests.test_api::test_health"].duration_ms == 123
    assert by_ref["tests.test_api::test_login"].duration_ms == 1500


def test_single_testsuite_root_is_accepted():
    report = parse_junit(
        '<testsuite tests="1"><testcase classname="a" name="b"/></testsuite>'
    )
    assert report.total == 1
    assert report.tests[0].spec_ref == "a::b"


def test_classname_optional():
    assert spec_ref(None, "just a name") == "just a name"
    assert spec_ref("Chrome > smoke.spec.ts", "loads") == "Chrome > smoke.spec.ts::loads"


def test_not_xml_raises():
    with pytest.raises(JUnitParseError):
        parse_junit("=== 3 passed in 0.5s ===")


def test_wrong_root_raises():
    # An HTML error page is not a test report, and must never read as one.
    with pytest.raises(JUnitParseError):
        parse_junit("<html><body>502 Bad Gateway</body></html>")


def test_failure_text_body_used_when_no_message_attr():
    report = parse_junit(
        "<testsuite><testcase classname='x' name='y'>"
        "<failure>the body is the message</failure></testcase></testsuite>"
    )
    assert report.tests[0].message == "the body is the message"
