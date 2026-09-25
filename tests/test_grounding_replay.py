"""The offline grounding eval, as pytest cases so CI runs it on every push.

grounding_replay.py is the runnable harness with a readable report; this file
is the same cases wired into the test suite. Free, deterministic, no network -
which is what lets the grounding guarantee be checked on every commit rather
than only when someone remembers to spend money on evaluate.py.
"""

import pytest

from evals.grounding_replay import CASES, FIXTURE, load_source, run_case


@pytest.fixture(scope="module")
def source() -> str:
    return load_source()


def test_fixture_is_present_and_looks_like_the_filing():
    """If the pinned document goes missing, every case below is meaningless."""
    assert FIXTURE.exists(), f"missing pinned filing at {FIXTURE}"
    text = load_source()
    assert len(text) == 69079, (
        f"pinned filing is {len(text):,} chars, expected 69,079 - the fixture "
        "has changed, and the expected verdicts were derived from the original"
    )
    assert "RISK FACTORS" in text.upper()


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_grounding_verdict(case, source):
    """Each recorded reply must reach its expected grounding verdict."""
    row = run_case(case, source)
    assert row["ok"], f"{case['id']}: {'; '.join(row['problems'])}\nwhy: {case['why']}"


def test_every_case_documents_why_it_exists():
    """A case without a rationale is a case nobody can maintain."""
    for case in CASES:
        assert case.get("why"), f"{case['id']} has no 'why'"


def test_the_suite_covers_both_outcomes():
    """A replay that only contains passing quotes would prove nothing."""
    verdicts = [c["expect"]["grounding_rate"] for c in CASES]
    assert 1.0 in verdicts, "no fully-grounded case"
    assert 0.0 in verdicts, "no fully-ungrounded case"
    assert any(v not in (None, 0.0, 1.0) for v in verdicts), "no partial case"
    assert None in verdicts, "no abstention case"
