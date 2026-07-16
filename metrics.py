

def net_margin(revenue: float | None, net_income: float | None) -> float | None:
    """Net income as a share of revenue. Returns None if revenue is missing
    or zero. Unknown is not the same as 0%."""
    if revenue is None or net_income is None or revenue == 0:
        return None
    return net_income/revenue
    

def fcf_margin(revenue: float | None, free_cash_flow: float | None) -> float | None:
    """ Free cash flow as a share of revenue. Net income is an accounting opinion,
    cash is a fact. A gap between this and net_margin means earnings are
    not converting to cash. Returns None is revenue is missing or 0."""
    if revenue is None or revenue == 0 or free_cash_flow is None:
        return None
    return free_cash_flow/revenue


def roe(net_income: float | None, shareholders_equity: float | None) -> float | None:
    """ Return on equity: profit per dollar of owner capital.
    Returns None is equity is missing, zero, or negative. Negative equity
    makes ROE computable but meaningless. A loss on negative equity reads as
    a prositve return"""
    if shareholders_equity is None or shareholders_equity <= 0 or net_income is None:
        return None
    return net_income/shareholders_equity


def debt_to_equity(total_debt: float | None, shareholders_equity: float | None) -> float | None:
    """Total debt per dollar of equity. Returns None is equity is missing, 
    zero, or negative. """
    if shareholders_equity is None or shareholders_equity <= 0 or total_debt is None:
        return None
    return total_debt/shareholders_equity


    

