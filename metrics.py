

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


def ttm(values: list[float | None]) -> float | None:
    """Trailing-twelve-month toal: sum of last 4 quarterly values.
    Returns None unless all 4 present. TTm build on 3 understates by 25%.
    Expects 4 most recent quarters, newest or oldest first."""

    if len(values) != 4 or any(v is None for v in values):
        return None
    return sum(values)

def roic(ttm_net_income: float | None, total_debt: float | None,
         shareholders_equity: float | None) -> float | None:
    """Return on invested capital: TTM income over (debt+equity).
    How much does this company earn per dolar of toal capital entrusted to it.
    Uses TTM income against currecnt invested capital.
    Returns None if income is missing or invested capital is missing/ < 0.
    Proper ROIC uses NOPAT (operating profit after tax) and subs excess cash
    from invested capital."""
    
    if ttm_net_income is None or total_debt is None or shareholders_equity is None:
        return None
    invested = total_debt + shareholders_equity
    if invested <= 0:
        return None
    return ttm_net_income/invested
