"""Evaluation harness for the risk-factor analyzer.

Runs every question in eval_data against the live analyzer and reports:

    Abstention accuracy  - did it correctly answer vs. decline?
    Hallucinations       - "absent" questions it answered anyway (must be 0)
    Grounding rate       - mean fraction of quotes found in the source
    Ungrounded quotes    - total fabricated quotes across all questions
    Answer correctness   - answerable/specific questions passing key_terms

Run:  python evaluate.py
Cost: ~one API call per question (a few cents total).
"""

from analysis import answer_question
from eval_data import QUESTIONS
from filings import get_risk_factors


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

    print("Fetching Microsoft risk factors...")
    filing = get_risk_factors("789019")
    if filing is None:
        print("Could not fetch filing.")
        return
    source = filing["text"]

    client = Anthropic(api_key=settings.anthropic_api_key)

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