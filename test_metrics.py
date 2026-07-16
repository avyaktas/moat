from metrics import net_margin

def test_net_margin_basic():
    assert net_margin(100.0, 20.0) == 0.20


def test_net_margin_none_revenue():
    assert net_margin(None, 20.0) is None


def test_net_margin_zero_revenue():
    assert net_margin(0.0, 20.0) is None


def test_net_margin_negative_income():
    assert net_margin(100.0, -20.0) == -0.20

from metrics import debt_to_equity, fcf_margin, net_margin, roe

def test_fcf_margin_basic():        assert fcf_margin(100.0, 25.0) == 0.25
def test_fcf_margin_none_fcf():     assert fcf_margin(100.0, None) is None
def test_roe_basic():               assert roe(20.0, 100.0) == 0.20
def test_roe_none_income():         assert roe(None, 100.0) is None
def test_roe_negative_equity():     assert roe(-50.0, -100.0) is None
def test_debt_to_equity_basic():    assert debt_to_equity(50.0, 100.0) == 0.50
def test_debt_to_equity_none_debt(): assert debt_to_equity(None, 100.0) is None