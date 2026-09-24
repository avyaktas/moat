"""Offline eval for the grounding verifier. No API calls, no network, no cost.

WHAT THIS MEASURES, AND WHAT IT DOES NOT

    evaluate.py measures the MODEL: given a real filing and a real question,
    does it answer honestly and quote accurately? That needs the API, costs
    money, and cannot be made deterministic because the model is sampled.

    This measures the VERIFIER: given a model response, does the grounding
    machinery reach the right verdict about it? That is pure string matching
    against a real document, so it is free, instant, deterministic, and can
    run on every commit and in CI forever.

    The split matters because the verifier is the part that makes the project
    trustworthy. A model that fabricates is expected and tolerable; a verifier
    that fails to notice is not. check_quote returning True for an empty
    string went unnoticed precisely because nothing exercised the verifier
    against adversarial input.

HOW IT WORKS

    The document is the real pinned MSFT Item 1A - so a quote either genuinely
    appears in a real SEC filing or genuinely does not, and the expected
    verdicts are facts rather than opinions. The model responses are recorded
    or constructed to exercise one behaviour each, and are replayed through
    the real answer_question code path via a stub client. That means the JSON
    parsing, the markdown-fence stripping, the quote checking and the
    grounding-rate arithmetic are all the production ones.

Run:  python grounding_replay.py
Exit: 0 if every case reached its expected verdict, 1 otherwise.
"""

import json
import pathlib

from analysis import answer_question

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "msft_fy2025_item1a.txt"


def load_source() -> str:
    return FIXTURE.read_text(encoding="utf-8")


# --- real spans, copied verbatim out of the pinned filing -------------------

VERBATIM = "We face significant competition from firms that provide competing platforms."
CROSS_LINE = "Competition in the technology sector Our competitors range in size"
NBSP_SPAN = "PART I Item 1A Business model competition"

# --- inventions: plausible, fluent, and absent from the document ------------

FABRICATED = "We expect our cloud revenue to grow by at least 20% annually."
TRUE_BUT_ABSENT = "We compete with Google and Amazon in cloud services."


def _reply(addressed: bool, answer: str, quotes: list[str]) -> str:
    return json.dumps({"addressed": addressed, "answer": answer, "quotes": quotes})


CASES = [
    {
        "id": "verbatim",
        "why": "An exact span must verify.",
        "reply": _reply(True, "The filing describes platform competition.", [VERBATIM]),
        "expect": {"checks": [True], "grounding_rate": 1.0, "addressed": True},
    },
    {
        "id": "cross_line",
        "why": (
            "Filing HTML breaks lines mid-sentence; a model quoting the passage "
            "writes it flat. compact() ignores whitespace so this must verify."
        ),
        "reply": _reply(True, "Competitors vary in size.", [CROSS_LINE]),
        "expect": {"checks": [True], "grounding_rate": 1.0, "addressed": True},
    },
    {
        "id": "non_breaking_space",
        "why": "The source has \\xa0 here; the model writes an ordinary space.",
        "reply": _reply(True, "Section heading.", [NBSP_SPAN]),
        "expect": {"checks": [True], "grounding_rate": 1.0, "addressed": True},
    },
    {
        "id": "trailing_punctuation",
        "why": "A trailing period the source lacks must not fail the quote.",
        "reply": _reply(True, "Platform competition.", [VERBATIM.rstrip(".") + "."]),
        "expect": {"checks": [True], "grounding_rate": 1.0, "addressed": True},
    },
    {
        "id": "case_insensitive",
        "why": "compact() lowercases, so case drift must not fail a real quote.",
        "reply": _reply(True, "Platform competition.", [VERBATIM.upper()]),
        "expect": {"checks": [True], "grounding_rate": 1.0, "addressed": True},
    },
    {
        "id": "fabricated",
        "why": "A fluent invention must be caught. This is the core guarantee.",
        "reply": _reply(True, "The filing forecasts cloud growth.", [FABRICATED]),
        "expect": {"checks": [False], "grounding_rate": 0.0, "addressed": True},
    },
    {
        "id": "true_but_absent",
        "why": (
            "True of Microsoft, absent from this document. The failure mode the "
            "whole project exists to catch: knowledge from training, not source."
        ),
        "reply": _reply(True, "It names cloud competitors.", [TRUE_BUT_ABSENT]),
        "expect": {"checks": [False], "grounding_rate": 0.0, "addressed": True},
    },
    {
        "id": "empty_quote",
        "why": (
            "`'' in source` is True for every source. An empty quote scored as "
            "grounded and produced a perfect rate while verifying nothing."
        ),
        "reply": _reply(True, "Something about competition.", [""]),
        "expect": {"checks": [False], "grounding_rate": 0.0, "addressed": True},
    },
    {
        "id": "whitespace_only_quote",
        "why": "Compacts to nothing, same hole as the empty quote.",
        "reply": _reply(True, "Something.", ["   \n\t"]),
        "expect": {"checks": [False], "grounding_rate": 0.0, "addressed": True},
    },
    {
        "id": "mixed_real_and_fake",
        "why": "The rate must be a fraction, not a boolean collapse.",
        "reply": _reply(True, "Mixed support.", [VERBATIM, FABRICATED]),
        "expect": {"checks": [True, False], "grounding_rate": 0.5, "addressed": True},
    },
    {
        "id": "one_real_three_fake",
        "why": "Arithmetic check on an uneven split.",
        "reply": _reply(
            True, "Mostly invented.",
            [VERBATIM, FABRICATED, TRUE_BUT_ABSENT, "Another invention entirely."],
        ),
        "expect": {"checks": [True, False, False, False], "grounding_rate": 0.25,
                   "addressed": True},
    },
    {
        "id": "abstention",
        "why": (
            "An unanswered question has nothing to ground. That is None, not "
            "0% - the same unknown-is-not-zero rule the rest of the code holds."
        ),
        "reply": _reply(False, "The document does not address this.", []),
        "expect": {"checks": [], "grounding_rate": None, "addressed": False},
    },
    {
        "id": "markdown_fenced",
        "why": "Models wrap JSON in fences despite instructions; it must parse.",
        "reply": "```json\n" + _reply(True, "Fenced reply.", [VERBATIM]) + "\n```",
        "expect": {"checks": [True], "grounding_rate": 1.0, "addressed": True},
    },
    {
        "id": "bare_fenced",
        "why": "Fences without a language tag are just as common.",
        "reply": "```\n" + _reply(True, "Fenced reply.", [VERBATIM]) + "\n```",
        "expect": {"checks": [True], "grounding_rate": 1.0, "addressed": True},
    },
    {
        "id": "malformed_json",
        "why": (
            "An unparseable reply must degrade to an explicit error shape, "
            "never to a silent success with zero quotes."
        ),
        "reply": "I'm afraid I can't answer that.",
        "expect": {"checks": [], "grounding_rate": None, "addressed": None,
                   "error": True},
    },
]


class _StubClient:
    """Returns a recorded reply instead of calling the API."""

    def __init__(self, reply: str):
        self._reply = reply
        self.messages = self

    def create(self, **kwargs):
        reply = self._reply

        class _Block:
            type = "text"
            text = reply

        class _Response:
            content = [_Block()]

        return _Response()


def run_case(case: dict, source: str) -> dict:
    """Replay one recorded reply through the real answer_question path."""
    result = answer_question("replayed question", source,
                             client=_StubClient(case["reply"]))
    expect = case["expect"]

    problems = []
    if result["quote_checks"] != expect["checks"]:
        problems.append(
            f"quote_checks {result['quote_checks']} != {expect['checks']}"
        )
    if result["grounding_rate"] != expect["grounding_rate"]:
        problems.append(
            f"grounding_rate {result['grounding_rate']} != {expect['grounding_rate']}"
        )
    if result["addressed"] != expect["addressed"]:
        problems.append(
            f"addressed {result['addressed']} != {expect['addressed']}"
        )
    if expect.get("error") and "error" not in result:
        problems.append("expected an error key, got none")

    return {"id": case["id"], "ok": not problems, "problems": problems}


def replay() -> list[dict]:
    source = load_source()
    return [run_case(c, source) for c in CASES]


def main() -> int:
    if not FIXTURE.exists():
        print(f"INCONCLUSIVE: fixture missing at {FIXTURE}")
        return 2

    rows = replay()
    print(f"Replaying {len(rows)} recorded responses against the pinned filing\n")
    for row in rows:
        status = "ok" if row["ok"] else "FAIL: " + "; ".join(row["problems"])
        print(f"  {row['id']:<24} {status}")

    failed = [r for r in rows if not r["ok"]]
    print()
    if failed:
        print(f"FAIL: {len(failed)}/{len(rows)} cases reached the wrong verdict.")
        return 1
    print(f"PASS: all {len(rows)} cases reached the expected verdict.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
