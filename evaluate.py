"""Evaluation harness for the risk-factor analyzer.

Runs every question in eval_data against the live analyzer and reports:

    Abstention accuracy  - did it correctly answer vs. decline?
    Hallucinations       - "absent" questions it answered anyway (must be 0)
    Grounding rate       - mean fraction of quotes found in the source
    Ungrounded quotes    - total fabricated quotes across all questions
    Answer correctness   - answerable/specific questions passing key_terms

WHY THE SOURCE DOCUMENT IS PINNED

    The answer key in eval_data was verified by term count against Microsoft's
    FY2025 Item 1A. Fetching "the latest 10-K" instead silently re-points the
    harness at a document the key was never checked against: by FY2026 the
    word GDPR no longer appears, so question 15 fails for a reason that has
    nothing to do with the analyzer. A regression guardrail has to measure the
    same thing every time, so the graded document is a committed fixture.

    The model calls stay real - that is the whole point of the eval. Only the
    8MB SEC download leaves the loop, which is also what makes this cheap
    enough to run after every commit.

Run:  python evaluate.py          # graded against the pinned fixture
      python evaluate.py --live   # refetch the newest 10-K instead
Cost: ~one API call per question (a few cents total).
"""

import argparse
import json
import pathlib

from analysis import answer_question
from eval_data import QUESTIONS
from filings import get_risk_factors

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures"
FIXTURE_TEXT = FIXTURE_DIR / "msft_fy2025_item1a.txt"
FIXTURE_META = FIXTURE_DIR / "msft_fy2025_item1a.json"

MSFT_CIK = "789019"


def load_source(live: bool = False) -> dict | None:
    """Return {text, report_date, url} for the document to grade against.

    Defaults to the pinned fixture; --live refetches the newest 10-K, which
    is how you find out the fixture has drifted from what MSFT now files.
    """
    if live:
        filing = get_risk_factors(MSFT_CIK)
        if filing is None:
            return None
        return {
            "text": filing["text"],
            "report_date": filing["report_date"],
            "url": filing["url"],
            "origin": "live SEC fetch",
        }

    if not FIXTURE_TEXT.exists():
        return None
    meta = json.loads(FIXTURE_META.read_text(encoding="utf-8"))
    return {
        "text": FIXTURE_TEXT.read_text(encoding="utf-8"),
        "report_date": meta["report_date"],
        "url": meta["url"],
        "origin": f"pinned fixture {FIXTURE_TEXT.name}",
    }


def grade_one(q: dict, source: str, client) -> dict:
    result = answer_question(q["question"], source, client)

    abstained = result["addressed"] is False
    abstention_correct = abstained == q["should_abstain"]

    if q["should_abstain"]:
        answer_correct = None  # nothing to be "correct" about; it should decline
    else:
        answer = (result["answer"] or "").lower()
        answer_correct = all(term.lower() in answer for term in q["key_terms"])

    fake_quotes = sum(1 for ok in result["quote_checks"] if not ok)

    return {
        "id": q["id"],
        "category": q["category"],
        "abstained": abstained,
        "abstention_correct": abstention_correct,
        "answer_correct": answer_correct,
        "grounding_rate": result["grounding_rate"],
        "fake_quotes": fake_quotes,
        "answer": result["answer"],
    }


def main():
    from anthropic import Anthropic

    from config import settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="grade against a freshly fetched 10-K instead of the pinned fixture",
    )
    args = parser.parse_args()

    filing = load_source(live=args.live)
    if filing is None:
        print("Could not load the filing to grade against.")
        return
    source = filing["text"]
    print(
        f"Source: {filing['origin']} - Item 1A as of {filing['report_date']}, "
        f"{len(source):,} chars"
    )

    # The key in eval_data was verified against FY2025 by term count. Say so
    # loudly when grading anything else, so a surprising score is read as
    # document drift rather than as a regression in the analyzer.
    if filing["report_date"] != "2025-06-30":
        print(
            f"  WARNING: answer key was verified against 2025-06-30, not "
            f"{filing['report_date']}. Scores are not comparable to the baseline."
        )

    client = Anthropic(api_key=settings.anthropic_key)

    rows = []
    print(f"Running {len(QUESTIONS)} questions...\n")
    for q in QUESTIONS:
        row = grade_one(q, source, client)
        rows.append(row)
        # per-question line
        flags = []
        if not row["abstention_correct"]:
            flags.append("ABSTENTION-WRONG")
        if row["answer_correct"] is False:
            flags.append("ANSWER-WRONG")
        if row["fake_quotes"]:
            flags.append(f"{row['fake_quotes']}-FAKE-QUOTES")
        status = " ".join(flags) if flags else "ok"
        print(f"  Q{row['id']:<3} {row['category']:<11} {status}")

    # ---- aggregates ----
    n = len(rows)
    abstention_acc = sum(r["abstention_correct"] for r in rows) / n

    # hallucinations: questions that SHOULD abstain but didn't
    hallucinations = sum(
        1 for r, q in zip(rows, QUESTIONS)
        if q["should_abstain"] and not r["abstained"]
    )

    grounded = [r["grounding_rate"] for r in rows if r["grounding_rate"] is not None]
    mean_grounding = sum(grounded) / len(grounded) if grounded else None
    total_fake = sum(r["fake_quotes"] for r in rows)

    answerable = [r for r in rows if r["answer_correct"] is not None]
    correctness = (
        sum(r["answer_correct"] for r in answerable) / len(answerable)
        if answerable else None
    )

    print("\n" + "=" * 50)
    print("RESULTS")
    print("=" * 50)
    print(f"Abstention accuracy:  {abstention_acc:.0%}  ({sum(r['abstention_correct'] for r in rows)}/{n})")
    print(f"Hallucinations:       {hallucinations}   (absent questions answered anyway; target 0)")
    if mean_grounding is not None:
        print(f"Mean grounding rate:  {mean_grounding:.0%}  (quotes found in source)")
    print(f"Ungrounded quotes:    {total_fake}   (fabricated quotes; target 0)")
    if correctness is not None:
        print(f"Answer correctness:   {correctness:.0%}  ({sum(r['answer_correct'] for r in answerable)}/{len(answerable)} answerable/specific)")


if __name__ == "__main__":
    main()