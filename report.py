"""Full company report: the business-owner framework end to end.

Composes every layer - filed financials, computed metrics, the scorecard,
market price, and the 10-K narrative - into one analysis with a verdict.

THE CENTRAL DESIGN DECISION: THE MODEL NARRATES, IT NEVER CALCULATES

    Every number in the report is computed by code from filed data before
    the LLM sees anything. The model receives finished figures and is asked
    to interpret them - never to derive them.

    Handing a model raw filings and asking for ROIC produces a plausible
    number with no provenance. Handing it "ROIC is 27.5%, computed from
    TTM net income over debt plus equity" produces interpretation grounded
    in arithmetic that can be audited. The split matters: arithmetic is a
    solved problem and belongs in code; judgment about what the arithmetic
    means is what the model is for.

THE VERDICT IS A FRAMEWORK CONCLUSION, NOT ADVICE

    BUY-CASE / WATCH-CASE / AVOID-CASE describe what this framework's
    criteria support, given these inputs. The prompt requires the model to
    frame it that way and to name the specific metric that would change the
    conclusion. A rating with no falsification condition is a horoscope.
"""

import json

from analysis import answer_question, check_quote
from metrics import (
    debt_to_equity,
    fcf_margin,
    net_margin,
    roe,
    roic,
    ttm,
)
from scoring import build_scorecard

def _f(v) -> float | None:
    """Decimal (from Numeric columns) to float, preserving None.

    Numeric columns return Decimal, which JSON serializes as a quoted
    string. The model should see real numbers, not strings of 28-digit
    precision.
    """
    return float(v) if v is not None else None


def _growth(current: float | None, prior: float | None) -> float | None:
    """Year-over-year growth rate. None unless both figures exist and the
    base is positive - growth off a negative base is not interpretable."""
    if current is None or prior is None or prior <= 0:
        return None
    return (current - prior) / prior


def _row_to_dict(row) -> dict:
    """Financials ORM row to a plain dict of the fields the report uses."""
    if row is None:
        return {}
    return {
        "period_end": row.period_end,
        "revenue": row.revenue,
        "net_income": row.net_income,
        "free_cash_flow": row.free_cash_flow,
        "total_debt": row.total_debt,
        "shareholders_equity": row.shareholders_equity,
        "cash": getattr(row, "cash", None),
        "short_term_investments": getattr(row, "short_term_investments", None),
    }


def build_report_data(rows: list, price_data: dict | None) -> dict:
    """Assemble every computed figure for a company. No LLM involved.

    `rows` must be Financials rows ordered newest first. Returns a dict of
    TTM aggregates, the scorecard, valuation, and the financial health
    table - all derived from filed numbers.
    """
    if not rows:
        return {"error": "No financial data available"}

    latest = rows[0]
    prior = rows[1] if len(rows) > 1 else None

    # --- TTM aggregates: the last four quarters, strictly ---
    ttm_revenue = ttm([r.revenue for r in rows[:4]])
    ttm_net_income = ttm([r.net_income for r in rows[:4]])
    ttm_fcf = ttm([r.free_cash_flow for r in rows[:4]])

    # --- Prior-year TTM, for growth: quarters 5-8 back ---
    prior_ttm_revenue = ttm([r.revenue for r in rows[4:8]]) if len(rows) >= 8 else None
    revenue_growth = _growth(ttm_revenue, prior_ttm_revenue)

    # --- Ratios ---
    ttm_net_margin = net_margin(ttm_revenue, ttm_net_income)
    ttm_fcf_margin = fcf_margin(ttm_revenue, ttm_fcf)
    ttm_roe = roe(ttm_net_income, latest.shareholders_equity)
    ttm_roic = roic(ttm_net_income, latest.total_debt, latest.shareholders_equity)
    leverage = debt_to_equity(latest.total_debt, latest.shareholders_equity)

    # --- Historical annual margins for the stability check ---
    # Every fourth row approximates one year back, newest first.
    historical_margins = [
        net_margin(ttm([r.revenue for r in rows[i:i + 4]]),
                   ttm([r.net_income for r in rows[i:i + 4]]))
        for i in range(0, min(len(rows), 20), 4)
    ]

    market_cap = price_data.get("market_cap") if price_data else None

    scorecard = build_scorecard(
        roic=ttm_roic,
        net_margin=ttm_net_margin,
        historical_margins=historical_margins,
        ttm_fcf=ttm_fcf,
        ttm_net_income=ttm_net_income,
        debt_to_equity=leverage,
        revenue_growth=revenue_growth,
        fcf_margin=ttm_fcf_margin,
        cash=latest.cash,
        investments=latest.short_term_investments,
        total_debt=latest.total_debt,
        market_cap=market_cap,
        current_period=_row_to_dict(latest),
        prior_period=_row_to_dict(prior),
    )

    return {
        "as_of": latest.period_end,
        "ttm": {
            "revenue": _f(ttm_revenue),
            "net_income": _f(ttm_net_income),
            "free_cash_flow": _f(ttm_fcf),
            "net_margin": _f(ttm_net_margin),
            "fcf_margin": _f(ttm_fcf_margin),
            "roe": _f(ttm_roe),
            "roic": _f(ttm_roic),
            "revenue_growth": _f(revenue_growth),
        },
        "price": price_data,
        "scorecard": scorecard.to_dict(),
    }


SYNTHESIS_PROMPT = """You are analyzing a company as a business owner would - \
someone buying a piece of a business to hold for five to ten years, not a trader \
chasing momentum. Price and value are different things. A falling price is not a \
reason to sell if the business is intact.

You are given (1) computed financial figures, already calculated from the \
company's SEC filings, and (2) the Risk Factors section of its most recent 10-K.

CRITICAL RULES:

1. Do NOT calculate anything. Every number you need is provided. Use the figures \
as given. If a figure is null, say the data is unavailable - do not estimate it.

2. Every claim about what the company says must be supported by a verbatim quote \
from the filing text. Copy quotes exactly.Each quote must be one contiguous passage. \
Do not use ellipses to join separate passages; pick the single most relevant span instead.

3. Do not use knowledge about this company from outside the provided material.

4. The verdict is a conclusion this framework's criteria support given these \
inputs - not a personal endorsement and not investment advice. Frame it that way.

Produce a JSON object with exactly these keys:

{
  "hype_vs_reality": "2-3 sentences. Is the case here fundamentals or narrative? \
Would this be compelling if nobody were talking about it? If the stock dropped \
40%, would the thesis still hold?",

  "risks": [
    {
      "risk": "one of the 3-6 biggest real risks, drawn from the filing",
      "quote": "verbatim supporting quote from the filing",
      "sell_trigger": "the specific, observable condition that would make you sell"
    }
  ],

  "verdict": "BUY-CASE" or "WATCH-CASE" or "AVOID-CASE",

  "reasoning": "2-3 paragraphs explaining the call, referencing the specific \
figures provided and what the filing says.",

  "strategy": "The exact metric to track and the conditions under which this \
framework would say to buy, add, or walk away."
}

Verdict definitions:
  BUY-CASE   - strong business, reasonable valuation, clear 5-year thesis
  WATCH-CASE - good business, but too expensive now or something needs proving
  AVOID-CASE - broken business, unreasonable valuation, or risk outweighs reward

If valuation data is missing, you cannot distinguish BUY-CASE from WATCH-CASE on \
price. Say so explicitly in the reasoning and default to WATCH-CASE.

Respond with the JSON object only - no preamble, no markdown fences."""


def synthesize(report_data: dict, filing_text: str, company_name: str,
               client=None) -> dict:
    """Ask the model to interpret computed figures against the filing.

    Returns the narrative plus per-quote verification, so the same grounding
    guarantee that applies to briefs applies here: a quote that is not in the
    filing was fabricated, and the code says so.
    """
    from anthropic import Anthropic

    from config import settings

    client = client or Anthropic(api_key=settings.anthropic_key)

    figures = json.dumps(report_data, indent=2, default=str)
    user_message = (
        f"<company>{company_name}</company>\n\n"
        f"<computed_figures>\n{figures}\n</computed_figures>\n\n"
        f"<filing_risk_factors>\n{filing_text}\n</filing_risk_factors>"
    )

    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=4000,
        system=SYNTHESIS_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    text_blocks = [b.text for b in response.content if b.type == "text"]
    raw = text_blocks[0].strip() if text_blocks else ""

    import re
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        return {"error": f"Model response was not valid JSON: {e}", "raw": raw}

    # Verify every quote the model attached to a risk.
    risks = parsed.get("risks", [])
    verified = 0
    for r in risks:
        quote = r.get("quote", "")
        ok = check_quote(quote, filing_text) if quote else False
        r["quote_verified"] = ok
        if ok:
            verified += 1

    parsed["grounding_rate"] = (verified / len(risks)) if risks else None
    return parsed