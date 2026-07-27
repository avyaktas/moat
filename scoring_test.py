"""Tests for the scoring framework.

Pure functions, no database, no network. Each check is tested for its
pass case, fail case, and - most importantly - its unknown case, since
"cannot evaluate" is a distinct outcome from "failed".
"""

from scoring import (
    build_scorecard,
    check_fcf_quality,
    check_leverage,
    check_margin_level,
    check_margin_stability,
    check_roic,
    financial_health_table,
    rule_of_40,
    survivability,
    valuation_ratios,
)


# --- ROIC ---

def test_roic_passes_above_threshold():
    assert check_roic(0.275).passed is True


def test_roic_fails_below_threshold():
    assert check_roic(0.08).passed is False


def test_roic_unknown_when_missing():
    c = check_roic(None)
    assert c.passed is None
    assert c.status == "UNKNOWN"


# --- Margin level ---

def test_margin_level_passes():
    assert check_margin_level(0.39).passed is True


def test_margin_level_fails():
    assert check_margin_level(0.04).passed is False


def test_margin_level_negative_fails():
    assert check_margin_level(-0.15).passed is False


# --- Margin stability ---

def test_margin_stability_passes_when_tight():
    assert check_margin_stability([0.35, 0.37, 0.34, 0.36]).passed is True


def test_margin_stability_fails_when_volatile():
    assert check_margin_stability([0.05, 0.35, 0.10, 0.28]).passed is False


def test_margin_stability_unknown_with_too_few_periods():
    assert check_margin_stability([0.35, 0.36]).passed is None


def test_margin_stability_ignores_nulls():
    c = check_margin_stability([0.35, None, 0.37, 0.36, None])
    assert c.passed is True


# --- FCF quality ---

def test_fcf_conversion_passes():
    assert check_fcf_quality(90.0, 100.0).passed is True


def test_fcf_conversion_fails():
    assert check_fcf_quality(50.0, 100.0).passed is False


def test_fcf_conversion_unknown_when_income_negative():
    # The ratio inverts sign against a loss and stops meaning anything.
    c = check_fcf_quality(50.0, -100.0)
    assert c.passed is None


def test_fcf_conversion_unknown_when_missing():
    assert check_fcf_quality(None, 100.0).passed is None


# --- Leverage ---

def test_leverage_passes_when_conservative():
    assert check_leverage(0.10).passed is True


def test_leverage_fails_when_elevated():
    assert check_leverage(1.5).passed is False


def test_leverage_detail_flags_red_zone():
    c = check_leverage(2.5)
    assert c.passed is False
    assert "red flag" in c.detail


# --- Rule of 40 ---

def test_rule_of_40_passes():
    # 18% growth + 23% FCF margin = 41
    assert rule_of_40(0.18, 0.23).passed is True


def test_rule_of_40_fails():
    assert rule_of_40(0.05, 0.10).passed is False


def test_rule_of_40_unknown_when_missing():
    assert rule_of_40(None, 0.23).passed is None


# --- Survivability ---

def test_survivability_self_funding():
    s = survivability(cash=32.0, investments=46.0, total_debt=40.0, ttm_fcf=72.0)
    assert s["net_cash"] == 38.0
    assert "Self-funding" in s["verdict"]


def test_survivability_positive_fcf_with_net_debt():
    s = survivability(cash=5.0, investments=0.0, total_debt=50.0, ttm_fcf=10.0)
    assert s["net_cash"] == -45.0
    assert "net debt" in s["verdict"]


def test_survivability_burning_with_runway():
    s = survivability(cash=100.0, investments=0.0, total_debt=10.0, ttm_fcf=-5.0)
    assert "runway" in s["verdict"]


def test_survivability_dilution_risk():
    s = survivability(cash=5.0, investments=0.0, total_debt=50.0, ttm_fcf=-20.0)
    assert "dilution" in s["verdict"].lower()


def test_survivability_unknown_when_missing():
    s = survivability(None, None, None, None)
    assert "Cannot assess" in s["verdict"]


# --- Valuation ---

def test_valuation_computes_multiples():
    v = valuation_ratios(market_cap=1000.0, ttm_fcf=50.0, ttm_net_income=100.0)
    assert v["p_fcf"] == 20.0
    assert v["p_e"] == 10.0


def test_valuation_skips_negative_denominators():
    v = valuation_ratios(market_cap=1000.0, ttm_fcf=-50.0, ttm_net_income=-10.0)
    assert v["p_fcf"] is None
    assert v["p_e"] is None


def test_valuation_none_without_market_cap():
    v = valuation_ratios(None, 50.0, 100.0)
    assert v["p_fcf"] is None


# --- Financial health table ---

def test_health_table_computes_change():
    t = financial_health_table(
        current={"cash": 32.0, "total_debt": 40.0},
        prior={"cash": 28.0, "total_debt": 43.0},
    )
    assert t["cash"]["change"] == 4.0
    assert t["total_debt"]["change"] == -3.0


def test_health_table_handles_missing():
    t = financial_health_table(current={"cash": 32.0}, prior={})
    assert t["cash"]["change"] is None


# --- Scorecard assembly ---

def test_scorecard_counts_outcomes():
    sc = build_scorecard(
        roic=0.275,
        net_margin=0.39,
        historical_margins=[0.35, 0.37, 0.34, 0.36],
        ttm_fcf=72.0,
        ttm_net_income=125.0,
        debt_to_equity=0.10,
        revenue_growth=0.18,
        fcf_margin=0.23,
        cash=32.0,
        investments=46.0,
        total_debt=40.0,
        market_cap=2900.0,
        current_period={"cash": 32.0},
        prior_period={"cash": 28.0},
    )
    # ROIC, margin level, stability, leverage, rule of 40 pass;
    # FCF conversion 72/125 = 0.58 fails.
    assert sc.passed == 5
    assert sc.failed == 1
    assert sc.evaluable == 6


def test_scorecard_reports_unknowns_separately():
    sc = build_scorecard(
        roic=None,
        net_margin=None,
        historical_margins=[],
        ttm_fcf=None,
        ttm_net_income=None,
        debt_to_equity=None,
        revenue_growth=None,
        fcf_margin=None,
        cash=None,
        investments=None,
        total_debt=None,
        market_cap=None,
        current_period={},
        prior_period={},
    )
    assert sc.unknown == 6
    assert sc.evaluable == 0


def test_scorecard_serializes():
    sc = build_scorecard(
        roic=0.275, net_margin=0.39, historical_margins=[0.35, 0.37, 0.34],
        ttm_fcf=72.0, ttm_net_income=125.0, debt_to_equity=0.10,
        revenue_growth=0.18, fcf_margin=0.23, cash=32.0, investments=46.0,
        total_debt=40.0, market_cap=2900.0,
        current_period={"cash": 32.0}, prior_period={"cash": 28.0},
    )
    d = sc.to_dict()
    assert d["summary"]["passed"] == 5
    assert len(d["checks"]) == 6
    assert d["valuation"]["p_fcf"] is not None