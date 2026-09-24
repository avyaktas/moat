""" LLM analysis of 10-K filing text with enforced grounding.

PROBLEM BEING SOLVED: A language model asked "Does Microsoft compete with Google?" will answer yes because it knows that
from its training, not from the filing. For this financial analysis that is bad because the answer is plausible and unsupported. 

APPROACH: Every claim must be accompanied by a source from document. Quotes from the source are checked by a string machine, and 
if a quote is not in the document the model fabricated it. Quote is either there or it isnt. 

Model is also required to answer "not addressed" when the document does not cover a question rather than filing it with
outside knowledge. 

WHY NORMALIZATION IS NEEDED: Text extracted from filer HTML contains non-breaking spaces, newlines inside words, and bad spacing. 
Formatting is needed.  """

import json
import re

from anthropic import Anthropic

from llm import get_client

MODEL = "claude-sonnet-5"
MAX_TOKENS = 2000

SYSTEM_PROMPT = """You are a financial analyst reading SEC filings. You answer \
questions using ONLY the document provided to you.
 
Rules:
 
1. Answer only from the document. You may know things about this company from \
other sources - ignore all of it. If the document does not address the \
question, say so.
 
2. Every factual claim must be supported by a verbatim quote from the \
document. Copy quotes exactly as they appear, character for character. Do not \
paraphrase inside quotation marks.
 
3. If the document does not address the question, set "addressed" to false, \
leave "quotes" empty, and explain briefly what the document does cover \
instead. Do not guess. Do not fill the gap from general knowledge. A correct \
"not addressed" is far more valuable than a plausible invention.
 
4. Respond with a single JSON object and nothing else - no preamble, no \
markdown fences:
 
{
  "addressed": true or false,
  "answer": "your answer in 1-4 sentences",
  "quotes": ["verbatim quote 1", "verbatim quote 2"]
}"""


def normalize(text:str) -> str:
    """Collapse whitespace runs to single spaces. Used for display."""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()

# Filings are typeset; models type ASCII. A 10-K contains curly quotes, curly
# apostrophes, en and em dashes, ellipsis characters and bullets, and a model
# asked to copy a passage "character for character" reliably produces the
# keyboard equivalents instead. Microsoft's FY2025 Item 1A alone has 20 curly
# apostrophes and 16 curly double quotes, so this is the common case, not an
# edge one: quoting "Microsoft's competitors" with a straight apostrophe used
# to be reported as fabricated.
#
# Folding is applied for COMPARISON only. normalize(), which produces text for
# a human to read, leaves the author's punctuation alone.
_PUNCTUATION_FOLD = str.maketrans({
    # Quotation marks and apostrophes.
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u2032": "'", "\u00b4": "'", "\u0060": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u2033": '"', "\u00ab": '"', "\u00bb": '"',
    # Dashes and minus signs.
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2212": "-",
    # Ellipsis.
    "\u2026": "...",
    # Spacing variants, folded to a plain space; compact() then strips them.
    "\u00a0": " ", "\u2007": " ", "\u2009": " ", "\u202f": " ",
    "\u200a": " ", "\u2002": " ", "\u2003": " ",
    # Zero-width characters and list markers are layout, not content, and a
    # model quoting a bulleted passage does not reproduce the bullet.
    "\u200b": "", "\u200c": "", "\u200d": "", "\ufeff": "",
    "\u2022": "", "\u00b7": "", "\u25cf": "", "\u25aa": "",
})


def fold_punctuation(text: str) -> str:
    """Map typographic characters to their ASCII equivalents."""
    return text.translate(_PUNCTUATION_FOLD)


def compact(text: str) -> str:
    """Strip ALL whitespace and lowercase, for quote comparison.
 
    Filing HTML splits words across tags for styling, so the extracted
    text contains breaks inside words: "RIS\\nK FACTORS". A model quoting
    that passage will naturally write "RISK FACTORS". Collapsing runs of
    whitespace to single spaces does not fix this - the space lands in
    the middle of the word - so comparison ignores whitespace entirely.
 
    Typographic punctuation is folded to ASCII for the same reason. A
    filing is typeset and contains curly quotes, curly apostrophes and em
    dashes; a model told to copy "character for character" types the
    keyboard equivalents. The document had (\u201cNOPAs\u201d) and the model
    wrote ("NOPAs"), so a passage genuinely present in the filing was
    reported as fabricated.

    This is deliberately permissive: it forgives every formatting
    artifact, at the cost of also forgiving a model that mangles spacing.
    That trade is right for this purpose. The check exists to catch
    fabricated content, and no fabrication survives it - inventing text
    that happens to match the source character-for-character minus
    whitespace and quote glyphs is not a realistic failure mode. Every
    substitution here maps a rendering difference, never two distinct
    words onto each other.
    """
    return re.sub(r"\s+", "", fold_punctuation(text)).lower()

def check_quote(quote: str, source: str) -> bool:
    """Return True if the quote appears in the source, ignoring whitespace.

    An empty quote is never grounded. `"" in source` is True for every
    source, so without this guard a model returning empty strings scored a
    perfect grounding rate - the check reporting success precisely when it
    had verified nothing. The same applies to a quote that is only
    whitespace or only punctuation, since compacting strips it to nothing.
    A quote carrying no text cannot support a claim; it is absent, not
    verified.
    """
    q = compact(quote).rstrip(".,;:?\"'")
    if not q:
        return False
    return q in compact(source)

def grounding_rate(quotes: list[str], source: str) -> float | None:
    """Fraction of quotes that actually appear in the source.
 
    Returns None when there are no quotes - an unanswered question has
    nothing to ground, which is different from a 0% grounding rate.
    """
    if not quotes:
        return None
    hits = sum(1 for q in quotes if check_quote(q, source))
    return hits / len(quotes)
 
 
def answer_question(question: str, source_text: str, client: Anthropic | None = None) -> dict:
    """Ask a question about a filing section and return a grounded answer.
 
    Returns a dict with:
        addressed:       whether the document covers the question
        answer:          the model's answer
        quotes:          supporting verbatim quotes
        quote_checks:    per-quote booleans - does it appear in the source?
        grounding_rate:  fraction of quotes verified, or None if no quotes
        raw:             the model's unparsed response (for debugging)
    """
    client = client or get_client()
 
    user_message = (
        f"<document>\n{source_text}\n</document>\n\n"
        f"Question: {question}"
    )
 
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
 
    text_blocks = [b.text for b in response.content if b.type == "text"]
    raw = text_blocks[0].strip() if text_blocks else ""
 
    # Models sometimes wrap JSON in markdown fences despite instructions.
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
 
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return {
            "addressed": None,
            "answer": None,
            "quotes": [],
            "quote_checks": [],
            "grounding_rate": None,
            "raw": raw,
            "error": "Response was not valid JSON",
        }
 
    quotes = parsed.get("quotes", [])
    checks = [check_quote(q, source_text) for q in quotes]
 
    return {
        "addressed": parsed.get("addressed"),
        "answer": parsed.get("answer"),
        "quotes": quotes,
        "quote_checks": checks,
        "grounding_rate": grounding_rate(quotes, source_text),
        "raw": raw,
    }
 
 
if __name__ == "__main__":
    import sys
 
    from filings import get_risk_factors
 
    question = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "Does the filing name specific competitor companies such as Google or Amazon?"
    )
 
    filing = get_risk_factors("789019")
    if filing is None:
        print("Could not fetch filing.")
        raise SystemExit(1)
 
    print(f"Question: {question}\n")
    result = answer_question(question, filing["text"])
 
    print(f"Addressed: {result['addressed']}")
    print(f"Answer: {result['answer']}\n")
 
    for quote, ok in zip(result["quotes"], result["quote_checks"], strict=True):
        mark = "OK  " if ok else "FAKE"
        print(f"  [{mark}] {quote[:120]}")
 
    print(f"\nGrounding rate: {result['grounding_rate']}")
 