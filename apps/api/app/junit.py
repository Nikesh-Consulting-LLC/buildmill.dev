"""US-81.2: JUnit XML parsing — the one interface the suite pipeline speaks.

pytest (--junitxml) and Playwright (the junit reporter) both emit this
natively, which is exactly why the pipeline asks for a report file instead of
scraping stdout. The parser is deliberately small: testsuites/testsuite trees,
one record per <testcase>, spec_ref = "classname::name" (a pytest nodeid in
spirit; Playwright's file::title once its reporter is configured the same way).

defusedxml when installed (requirements.txt), stdlib ElementTree otherwise —
the report crosses the factory's own authenticated SSH channel and is
size-capped by the caller, so the fallback is acceptable in dev.
"""

from __future__ import annotations

from dataclasses import dataclass, field

try:  # pragma: no cover - which import wins depends on the environment
    from defusedxml.ElementTree import fromstring as _fromstring
except ImportError:  # pragma: no cover
    from xml.etree.ElementTree import fromstring as _fromstring


class JUnitParseError(Exception):
    pass


@dataclass
class JUnitTest:
    spec_ref: str
    status: str  # pass | fail | skipped | error
    duration_ms: int | None = None
    message: str | None = None


@dataclass
class JUnitReport:
    tests: list[JUnitTest] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.tests)

    @property
    def passed(self) -> int:
        return sum(1 for t in self.tests if t.status == "pass")

    @property
    def failed(self) -> int:
        # An errored test is a failed test to the verdict; the per-test
        # status keeps the distinction for display.
        return sum(1 for t in self.tests if t.status in ("fail", "error"))

    @property
    def skipped(self) -> int:
        return sum(1 for t in self.tests if t.status == "skipped")


def spec_ref(classname: str | None, name: str | None) -> str:
    classname = (classname or "").strip()
    name = (name or "").strip()
    return f"{classname}::{name}" if classname else name


def parse_junit(text: str) -> JUnitReport:
    """Parse a JUnit XML document. Raises JUnitParseError on anything that is
    not one — the pipeline treats that as 'could not test', never 'passed'."""
    try:
        root = _fromstring(text)
    except Exception as e:  # ElementTree raises ParseError; defused others
        raise JUnitParseError(f"not valid XML: {e}")

    if root.tag == "testsuites":
        suites = list(root.iter("testsuite"))
    elif root.tag == "testsuite":
        suites = [root]
    else:
        raise JUnitParseError(
            f"root element is <{root.tag}>, expected <testsuites> or <testsuite>"
        )

    report = JUnitReport()
    for suite in suites:
        for case in suite.findall("testcase"):
            ref = spec_ref(case.get("classname"), case.get("name"))
            if not ref:
                continue
            status = "pass"
            message = None
            failure = case.find("failure")
            error = case.find("error")
            skipped = case.find("skipped")
            if failure is not None:
                status = "fail"
                message = failure.get("message") or (failure.text or "").strip() or None
            elif error is not None:
                status = "error"
                message = error.get("message") or (error.text or "").strip() or None
            elif skipped is not None:
                status = "skipped"
                message = skipped.get("message") or None
            duration_ms = None
            raw_time = case.get("time")
            if raw_time:
                try:
                    duration_ms = int(float(raw_time) * 1000)
                except ValueError:
                    duration_ms = None
            if message:
                message = message[:2000]
            report.tests.append(
                JUnitTest(
                    spec_ref=ref,
                    status=status,
                    duration_ms=duration_ms,
                    message=message,
                )
            )
    return report
