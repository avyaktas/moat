"""Response models.

No endpoint declared one, so /docs listed every route with no indication of
what any of them returned - which is most of what an OpenAPI page is for.

These are deliberately precise where the shape is ours and loose where it is
not. Everything computed in code - the TTM block, the scorecard, the health
table, the cache metadata - is modelled field by field, because that shape is
a contract this application defines and should be held to. The narrative is a
plain dict: it comes back from the model, and pinning a schema onto it would
either drop keys the model added or reject a response that was perfectly
usable. Declaring precision that is not there would be worse than declaring
none.

Note that response_model FILTERS as well as documents: a field absent from
the model is dropped from the response. That makes a careless model here a
silent data-loss bug rather than a documentation gap, which is why the
endpoints' full responses were diffed before and after these were added.
"""

from datetime import date, datetime

from pydantic import BaseModel, Field


class CompanyOut(BaseModel):
    id: int
    ticker: str
    name: str
    sector: str | None = None


class FinancialsOut(BaseModel):
    """One filed quarter. Every figure is nullable on purpose: a company that
    did not report a line item has no value for it, and that is not zero."""

    period_end: date
    revenue: float | None = None
    net_income: float | None = None
    free_cash_flow: float | None = None
    total_debt: float | None = None
    shareholders_equity: float | None = None


class QuarterMetricsOut(BaseModel):
    period_end: date
    net_margin: float | None = None
    fcf_margin: float | None = None
    roe: float | None = None
    debt_to_equity: float | None = None


class TtmMetricsOut(BaseModel):
    revenue: float | None = None
    net_income: float | None = None
    free_cash_flow: float | None = None
    net_margin: float | None = None
    fcf_margin: float | None = None
    roe: float | None = None
    roic: float | None = None


class MetricsOut(BaseModel):
    quarterly: list[QuarterMetricsOut]
    ttm: TtmMetricsOut


class BriefOut(BaseModel):
    question: str
    addressed: bool
    answer: str
    quotes: list[str]
    grounding_rate: float | None = Field(
        default=None,
        description=(
            "Fraction of quotes found verbatim in the filing. None means the "
            "answer cited no quotes, which is different from 0%."
        ),
    )
    filing_url: str
    report_date: str
    cached_at: datetime | None = None


class CheckOut(BaseModel):
    name: str
    status: str = Field(description="PASS, FAIL, or UNKNOWN")
    value: float | None = None
    threshold: float | None = None
    detail: str


class ScorecardSummaryOut(BaseModel):
    passed: int
    failed: int
    unknown: int
    evaluable: int


class ValuationOut(BaseModel):
    market_cap: float | None = None
    p_fcf: float | None = None
    p_e: float | None = None


class ReportTtmOut(TtmMetricsOut):
    revenue_growth: float | None = None


class ScorecardOut(BaseModel):
    summary: ScorecardSummaryOut
    checks: list[CheckOut]
    # The health table is keyed by metric name with a survivability entry
    # alongside, so it is a mapping rather than a fixed set of fields.
    financial_health: dict
    valuation: ValuationOut


class PriceOut(BaseModel):
    price: float | None = None
    market_cap: float | None = None
    shares_outstanding: float | None = None


class ReportDataOut(BaseModel):
    as_of: date
    ttm: ReportTtmOut
    price: PriceOut | None = None
    scorecard: ScorecardOut


class SourcesOut(BaseModel):
    financials: str
    filing: str | None = None
    report_date: str | None = None
    price: str


class CacheOut(BaseModel):
    cached: bool
    generated_at: str
    age_days: float | None = None


class ReportOut(BaseModel):
    company: str
    name: str
    data: ReportDataOut
    narrative: dict | None = Field(
        default=None,
        description=(
            "Model-generated analysis, or null when synthesis failed or no "
            "filing was available. A null narrative means the report is "
            "degraded; it is deliberately not cached in that state."
        ),
    )
    sources: SourcesOut
    cache: CacheOut
