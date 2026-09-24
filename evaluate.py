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

WHICH NUMBERS ARE GATES AND WHICH ARE ADVISORY

    The model is sampled, and temperature is deprecated on this model, so the
    output cannot be pinned. Only the numbers that do not depend on sampling
    can serve as a gate.

    HARD GATES - ungrounded quotes, and the mean grounding rate. Whether a
    quote appears in the document is decided by string matching, not by the
    model, so these are facts about the output rather than judgments in it.

    They are the most stable numbers here, but not perfectly stable: across
    roughly eight runs one produced a single ungrounded quote, for a 98% rate.
    The floor stays at 100% anyway. Relaxing it to 95% would buy green runs by
    giving up the only thing being measured - a quote is in the document or it
    is not, and there is no principled reason to accept a fabricated one. A
    breach is a prompt to look at the quote, which is why failures now print
    the offending text in full rather than only a count.

    ADVISORY - answer correctness and abstention accuracy. Answer correctness
    substring-matches key terms against free-text phrasing and scored 100%,
    80% and 93% on three consecutive unchanged runs. Abstention accuracy, and
    the hallucination count derived from it, turn on whether the model set
    "addressed" to false, which is a framing choice on some questions rather
    than a safety property.

    Q16 is the clearest case: "Does the filing name specific competitor
    companies such as Google or Amazon?" The filing does not, and both
    available framings are correct - declining as not-addressed, or answering
    "no, it does not name them" with a grounded quote about competition. One
    run in five picks the second, which registered as a "hallucination" even
    though ungrounded quotes stayed at zero and nothing was invented.

    That is the distinction the gates now draw. Hallucination in the sense
    this project cares about means asserting content the document does not
    contain, and the grounding check is what detects it. Declining to answer
    is a style; fabricating a quote is a defect.

Run:  python evaluate.py          # graded against the pinned fixture
      python evaluate.py --live   # refetch the newest 10-K instead
Exit: 0 if every hard gate held, 1 otherwise.
Cost: ~one API call per question (a few cents total).
"""

import argparse
import json
import pathlib

from anthropic import APIError

from analysis import answer_question
from eval_data import QUESTIONS
from filings import get_risk_factors

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures"
FIXTURE_TEXT = FIXTURE_DIR / "msft_fy2025_item1a.txt"
FIXTURE_META = FIXTURE_DIR / "msft_fy2025_item1a.json"

MSFT_CIK = "789019"

# A quote is either in the document or it is not, so the grounding gate is an
# absolute rather than a target. Deliberately not relaxed to 95% to absorb the
# occasional mangled quote: a floor below 100% accepts fabrication by policy,
# and this is the number the whole project exists to defend. When it breaches,
# the offending quote is printed so the cause can be identified rather than
# re-rolled.
MIN_GROUNDING_RATE = 1.0

# Three outcomes, three exit codes. Conflating "could not run" with "failed"
# is the same mistake as conflating unknown with zero.
EXIT_PASS = 0
EXIT_GATE_FAILED = 1
EXIT_INCONCLUSIVE = 2


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

    # Keep the offending quotes, not just how many there were. A gate that
    # reports "1 ungrounded quote" and nothing else cannot be acted on: the
    # difference between a model that paraphrased, a model that joined two
    # passages with an ellipsis, and a model that returned an empty string is
    # the whole diagnosis, and re-running until it goes green is not one.
    ungrounded = [
        quote for quote, ok in zip(result["quotes"], result["quote_checks"])
        if not ok
    ]

    return {
        "id": q["id"],
        "category": q["category"],
        "abstained": abstained,
        "abstention_correct": abstention_correct,
        "answer_correct": answer_correct,
        "grounding_rate": result["grounding_rate"],
        "fake_quotes": fake_quotes,
        "ungrounded": ungrounded,
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
        print("INCONCLUSIVE: could not load the filing to grade against.")
        return EXIT_INCONCLUSIVE
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
        try:
            row = grade_one(q, source, client)
        except APIError as exc:
            # "The API was unreachable" and "the model fabricated a quote" are
            # different outcomes and must not share an exit code. A traceback
            # here previously exited 1, the same as a breached gate, so a
            # billing problem read as a grounding regression.
            print(f"\n  Q{q['id']}: could not reach the API - {type(exc).__name__}: {exc}")
            print("\nINCONCLUSIVE: the eval did not run. This is not a gate failure.")
            return EXIT_INCONCLUSIVE
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
    print("Hard gates - decided by string matching, not by the model")
    print(f"  Ungrounded quotes:    {total_fake}   (fabricated quotes; must be 0)")
    if mean_grounding is not None:
        print(f"  Mean grounding rate:  {mean_grounding:.0%}  (quotes found in source; floor {MIN_GROUNDING_RATE:.0%})")
    print()
    print("Advisory - depends on sampling, varies run to run (see module docstring)")
    print(f"  Hallucinations:       {hallucinations}   (absent questions answered anyway; target 0)")
    print(f"  Abstention accuracy:  {abstention_acc:.0%}  ({sum(r['abstention_correct'] for r in rows)}/{n})")
    if correctness is not None:
        print(f"  Answer correctness:   {correctness:.0%}  ({sum(r['answer_correct'] for r in answerable)}/{len(answerable)} answerable/specific)")

    if total_fake:
        print()
        print("Ungrounded quotes in full - what the model produced that the")
        print("document does not contain:")
        for r in rows:
            for quote in r.get("ungrounded", []):
                shown = repr(quote) if len(quote) <= 300 else repr(quote[:300]) + "..."
                print(f"  Q{r['id']} [{len(quote)} chars] {shown}")

    failures = []
    if total_fake:
        failures.append(f"{total_fake} ungrounded quote(s)")
    if mean_grounding is not None and mean_grounding < MIN_GROUNDING_RATE:
        failures.append(
            f"grounding rate {mean_grounding:.0%} below floor {MIN_GROUNDING_RATE:.0%}"
        )

    print()
    if failures:
        print("FAIL: " + "; ".join(failures))
        return EXIT_GATE_FAILED
    print("PASS: every hard gate held.")
    return EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main())