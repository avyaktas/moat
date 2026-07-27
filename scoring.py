"""The business-owner framework, encoded as deterministic checks.

This module answers the computable half of "would I own this business for
5-10 years?" - the parts that are arithmetic, not judgment. It deliberately
does NOT produce a buy/sell rating: it produces a scorecard of criteria met
and failed, with every input traceable to a filed number. The narrative
verdict is assembled elsewhere, on top of these facts.

DESIGN PRINCIPLES

    Honest nulls. Every function returns None when its inputs are missing.
    A criterion that cannot be evaluated is not a failed criterion, and a
    scorecard says so rather than quietly scoring it zero.

    Thresholds are explicit and named. They live in THRESHOLDS below so
    they can be argued with, tuned, and cited - not buried in conditionals.

    No forecasting. Every check describes what the business has already
    done. Nothing here predicts a price or a return.

WHAT EACH CHECK MEANS

    ROIC          Return on invested capital. Sustained high ROIC is the
                  clearest quantitative fingerprint of a durable advantage:
                  it means competitors have not been able to compete the
                  returns away.
    Margin level  Profitability per dollar of sales.
    Margin trend  Stability matters as much as level. A margin that swings
                  wildly suggests commodity economics or no pricing power.
    FCF quality   Net income is an accounting opinion; cash is a fact.
                  Earnings that do not convert to cash deserve suspicion.
    Leverage      Debt magnifies both outcomes. High leverage turns a
                  survivable bad year into a dilutive one.
    Rule of 40    Growth % + FCF margin %. A single number capturing the
                  tradeoff between growing and generating cash.
    Survivability Can this company fund itself without issuing stock?
                  Dilution is the quiet way shareholders lose.
"""

from dataclasses import dataclass, field

THRESHOLDS = {
    "roic_quality": 0.15,          # TTM ROIC above this suggests durable advantage
    "net_margin_floor": 0.10,      # minimum acceptable profitability
    "margin_stability_range": 0.15,  # max 5yr spread (15 percentage points)
    "fcf_conversion_floor": 0.80,  # FCF / net income
    "debt_equity_healthy": 1.0,    # below this is conservative
    "debt_equity_concerning": 2.0,  # above this is a red flag
    "rule_of_40": 40.0,            # growth% + FCF margin%
}


@dataclass
class Check:
    """One criterion, its verdict, and the numbers behind it."""

    name: str
    passed: bool | None      # None means "could not evaluate"
    value: float | None
    threshold: float | None
    detail: str

    @property
    def status(self) -> str:
        if self.passed is None:
            return "UNKNOWN"
        return "PASS" if self.passed else "FAIL"


@dataclass
class Scorecard:
    """The full set of checks plus derived summary numbers."""

    checks: list[Check] = field(default_factory=list)
    financial_health: dict = field(default_factory=dict)
    valuation: dict = field(default_factory=dict)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.passed is True)

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if c.passed is False)

    @property
    def unknown(self) -> int:
        return sum(1 for c in self.checks if c.passed is None)

    @property
    def evaluable(self) -> int:
        return self.passed + self.failed

    def to_dict(self) -> dict:
        return {
            "summary": {
                "passed": self.passed,
                "failed": self.failed,
                "unknown": self.unknown,
                "evaluable": self.evaluable,
            },
            "checks": [
                {
                    "name": c.name,
                    "status": c.status,
                    "value": c.value,
                    "threshold": c.threshold,
                    "detail": c.detail,
                }
                for c in self.checks
            ],
            "financial_health": self.financial_health,
            "valuation": self.valuation,
        }


# ----------------------------------------------------------------------
# Individual checks
# ----------------------------------------------------------------------


def check_roic(roic: float | None) -> Check:
    """Sustained returns on invested capital above the threshold suggest a
    business competitors have not been able to erode."""
    t = THRESHOLDS["roic_quality"]
    if roic is None:
        return Check("ROIC", None, None, t, "ROIC could not be computed")
    return Check(
        "ROIC",
        roic > t,
        roic,
        t,
        f"TTM ROIC {roic:.1%} vs threshold {t:.0%}",
    )


def check_margin_level(net_margin: float | None) -> Check:
    """Profitability per dollar of sales."""
    t = THRESHOLDS["net_margin_floor"]
    if net_margin is None:
        return Check("Net margin", None, None, t, "Net margin unavailable")
    return Check(
        "Net margin",
        net_margin > t,
        net_margin,
        t,
        f"TTM net margin {net_margin:.1%} vs floor {t:.0%}",
    )


def check_margin_stability(margins: list[float | None]) -> Check:
    """Stability matters as much as level.

    A wide spread between the best and worst annual margin suggests the
    company cannot hold price - commodity economics rather than a moat.
    Requires at least three observations to say anything.
    """
    t = THRESHOLDS["margin_stability_range"]
    values = [m for m in margins if m is not None]
    if len(values) < 3:
        return Check(
            "Margin stability", None, None, t,
            f"Only {len(values)} periods available; need 3+",
        )
    spread = max(values) - min(values)
    return Check(
        "Margin stability",
        spread < t,
        spread,
        t,
        f"Margin range {spread:.1%} across {len(values)} periods (max {t:.0%})",
    )


def check_fcf_quality(fcf: float | None, net_income: float | None) -> Check:
    """Earnings that do not convert to cash deserve suspicion.

    Only meaningful when net income is positive: the ratio inverts sign and
    stops meaning anything against a loss.
    """
    t = THRESHOLDS["fcf_conversion_floor"]
    if fcf is None or net_income is None:
        return Check("FCF conversion", None, None, t, "FCF or net income unavailable")
    if net_income <= 0:
        return Check(
            "FCF conversion", None, None, t,
            "Net income is not positive; conversion ratio is not meaningful",
        )
    ratio = fcf / net_income
    return Check(
        "FCF conversion",
        ratio > t,
        ratio,
        t,
        f"TTM FCF is {ratio:.2f}x net income (floor {t:.2f}x)",
    )


def check_leverage(debt_to_equity: float | None) -> Check:
    """Debt magnifies outcomes in both directions."""
    healthy = THRESHOLDS["debt_equity_healthy"]
    concerning = THRESHOLDS["debt_equity_concerning"]
    if debt_to_equity is None:
        return Check("Leverage", None, None, healthy, "Debt/equity unavailable")
    if debt_to_equity < healthy:
        detail = f"Debt/equity {debt_to_equity:.2f} - conservative"
    elif debt_to_equity < concerning:
        detail = f"Debt/equity {debt_to_equity:.2f} - elevated but not alarming"
    else:
        detail = f"Debt/equity {debt_to_equity:.2f} - above {concerning:.1f}, a red flag"
    return Check("Leverage", debt_to_equity < healthy, debt_to_equity, healthy, detail)


def rule_of_40(revenue_growth: float | None, fcf_margin: float | None) -> Check:
    """Growth % plus FCF margin %.

    Captures the tradeoff between growing and generating cash: a company may
    justify thin margins if it is growing fast, or slow growth if it prints
    cash, but not both weak.
    """
    t = THRESHOLDS["rule_of_40"]
    if revenue_growth is None or fcf_margin is None:
        return Check("Rule of 40", None, None, t, "Growth or FCF margin unavailable")
    score = (revenue_growth * 100) + (fcf_margin * 100)
    return Check(
        "Rule of 40",
        score >= t,
        score,
        t,
        f"Growth {revenue_growth:.1%} + FCF margin {fcf_margin:.1%} = {score:.1f}",
    )


# ----------------------------------------------------------------------
# Derived figures
# ----------------------------------------------------------------------


def survivability(
    cash: float | None,
    investments: float | None,
    total_debt: float | None,
    ttm_fcf: float | None,
) -> dict:
    """Can the company fund itself without issuing stock?

    Dilution is the quiet way shareholders lose, so the question is not
    only "is it profitable" but "can it survive a bad stretch on its own
    balance sheet". Returns a dict rather than a Check because the answer
    is a short narrative, not a threshold.
    """
    liquid = None
    if cash is not None or investments is not None:
        liquid = (cash or 0) + (investments or 0)

    net_cash = None
    if liquid is not None and total_debt is not None:
        net_cash = liquid - total_debt

    if ttm_fcf is None or liquid is None:
        verdict = "Cannot assess - missing cash or cash flow data"
    elif ttm_fcf > 0 and (net_cash is None or net_cash > 0):
        verdict = "Self-funding: positive free cash flow and net cash position"
    elif ttm_fcf > 0:
        verdict = "Positive free cash flow, but carries net debt"
    elif net_cash is not None and net_cash > 0:
        verdict = "Burning cash, but net cash position provides runway"
    else:
        verdict = "Burning cash with net debt - dilution risk"

    return {
        "liquid_assets": liquid,
        "total_debt": total_debt,
        "net_cash": net_cash,
        "ttm_free_cash_flow": ttm_fcf,
        "verdict": verdict,
    }


def valuation_ratios(
    market_cap: float | None,
    ttm_fcf: float | None,
    ttm_net_income: float | None,
) -> dict:
    """Price multiples. Deliberately not scored against a threshold.

    What counts as expensive depends on growth, durability, and the
    alternatives available - judgment this module does not make. These are
    reported so a human (or the narrative layer) can weigh them.
    """
    out = {"market_cap": market_cap, "p_fcf": None, "p_e": None}
    if market_cap is None:
        return out
    if ttm_fcf is not None and ttm_fcf > 0:
        out["p_fcf"] = market_cap / ttm_fcf
    if ttm_net_income is not None and ttm_net_income > 0:
        out["p_e"] = market_cap / ttm_net_income
    return out


def financial_health_table(current: dict, prior: dict) -> dict:
    """Prior period vs most recent quarter for the four survival metrics.

    Direction matters more than level: cash falling while debt rises is a
    different story from the same balance sheet moving the other way.
    """
    fields = ["cash", "short_term_investments", "total_debt", "free_cash_flow", "shareholders_equity"]
    table = {}
    for f in fields:
        now = current.get(f)
        then = prior.get(f)
        change = None
        if now is not None and then is not None:
            change = now - then
        table[f] = {"current": now, "prior": then, "change": change}
    return table


# ----------------------------------------------------------------------
# Assembly
# ----------------------------------------------------------------------


def build_scorecard(
    roic: float | None,
    net_margin: float | None,
    historical_margins: list[float | None],
    ttm_fcf: float | None,
    ttm_net_income: float | None,
    debt_to_equity: float | None,
    revenue_growth: float | None,
    fcf_margin: float | None,
    cash: float | None,
    investments: float | None,
    total_debt: float | None,
    market_cap: float | None,
    current_period: dict,
    prior_period: dict,
) -> Scorecard:
    """Run every check and assemble the scorecard."""
    checks = [
        check_roic(roic),
        check_margin_level(net_margin),
        check_margin_stability(historical_margins),
        check_fcf_quality(ttm_fcf, ttm_net_income),
        check_leverage(debt_to_equity),
        rule_of_40(revenue_growth, fcf_margin),
    ]

    health = financial_health_table(current_period, prior_period)
    health["survivability"] = survivability(cash, investments, total_debt, ttm_fcf)

    return Scorecard(
        checks=checks,
        financial_health=health,
        valuation=valuation_ratios(market_cap, ttm_fcf, ttm_net_income),
    )