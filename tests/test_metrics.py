from moat.metrics import net_margin


def test_net_margin_basic():
    assert net_margin(100.0, 20.0) == 0.20


def test_net_margin_none_revenue():
    assert net_margin(None, 20.0) is None


def test_net_margin_zero_revenue():
    assert net_margin(0.0, 20.0) is None


def test_net_margin_negative_income():
    assert net_margin(100.0, -20.0) == -0.20

from moat.metrics import debt_to_equity, fcf_margin, roe


def test_fcf_margin_basic():        assert fcf_margin(100.0, 25.0) == 0.25
def test_fcf_margin_none_fcf():     assert fcf_margin(100.0, None) is None
def test_roe_basic():               assert roe(20.0, 100.0) == 0.20
def test_roe_none_income():         assert roe(None, 100.0) is None
def test_roe_negative_equity():     assert roe(-50.0, -100.0) is None
def test_debt_to_equity_basic():    assert debt_to_equity(50.0, 100.0) == 0.50
def test_debt_to_equity_none_debt(): assert debt_to_equity(None, 100.0) is None

# --- ttm ---
#
# "Returns None unless all 4 present. TTM built on 3 understates by 25%."
# That is the whole point of the function and it was untested.

from moat.metrics import roic, ttm


def test_ttm_sums_four_quarters():
    assert ttm([10.0, 20.0, 30.0, 40.0]) == 100.0


def test_ttm_none_with_three_quarters():
    # A three-quarter sum silently understates the year by roughly 25%.
    assert ttm([10.0, 20.0, 30.0]) is None


def test_ttm_none_with_five_quarters():
    assert ttm([10.0, 20.0, 30.0, 40.0, 50.0]) is None


def test_ttm_none_when_any_quarter_is_missing():
    assert ttm([10.0, None, 30.0, 40.0]) is None


def test_ttm_none_on_empty():
    assert ttm([]) is None


def test_ttm_handles_negative_quarters():
    assert ttm([10.0, -20.0, 30.0, 40.0]) == 60.0


def test_ttm_of_all_zeroes_is_zero_not_none():
    # Zero is a value. Unknown is not.
    assert ttm([0.0, 0.0, 0.0, 0.0]) == 0.0


# --- roic ---

def test_roic_basic():
    # 100 / (100 + 300) = 0.25
    assert roic(100.0, 100.0, 300.0) == 0.25


def test_roic_none_without_income():
    assert roic(None, 100.0, 300.0) is None


def test_roic_none_without_debt():
    # Debt of 0 is a value; debt of None is not known.
    assert roic(100.0, None, 300.0) is None


def test_roic_none_without_equity():
    assert roic(100.0, 100.0, None) is None


def test_roic_with_zero_debt_is_computable():
    assert roic(100.0, 0.0, 400.0) == 0.25


def test_roic_none_when_invested_capital_is_zero():
    assert roic(100.0, 0.0, 0.0) is None


def test_roic_none_when_invested_capital_is_negative():
    # Negative invested capital inverts the sign and stops meaning anything.
    assert roic(100.0, 10.0, -50.0) is None


def test_roic_negative_income_gives_negative_return():
    assert roic(-100.0, 100.0, 300.0) == -0.25
