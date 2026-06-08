from statistics import mean

from data.fmp import get_all_financials, get_treasury_yield

EQUITY_RISK_PREMIUM = 0.055
TERMINAL_GROWTH = 0.03
PROJECTION_YEARS = 5
FINANCIAL_SECTORS = {"Financial Services", "Banking", "Insurance",
                     "Financials", "Finance", "Banks"}
MOS_BUY_THRESHOLD = 15.0
MOS_SELL_THRESHOLD = -15.0
BETA_FLOOR = 0.5
MIN_COST_OF_EQUITY = 0.06


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_financial(sector: str) -> bool:
    return sector.strip() in FINANCIAL_SECTORS


def _compute_bvps(profile: dict, balance_sheets: list[dict], is_fin: bool,
                  price: float) -> float:
    if not is_fin:
        bvps = _safe_float(profile.get("bookValuePerShare"), default=0.0)
        if bvps > 0:
            return bvps

    equity = _safe_float(balance_sheets[0].get("totalStockholdersEquity"))
    if equity == 0:
        raise ValueError(
            f"totalStockholdersEquity is zero or missing for {profile.get('symbol', '?')}."
        )

    gw_raw = balance_sheets[0].get("goodwillAndIntangibleAssets")
    if is_fin:
        if gw_raw is None:
            raise ValueError(
                "goodwillAndIntangibleAssets missing — required for tangible book value "
                f"calculation on financial-sector ticker {profile.get('symbol', '?')}."
            )
        equity -= float(gw_raw)
    else:
        equity -= _safe_float(gw_raw, default=0.0)

    mkt_cap = _safe_float(profile.get("mktCap"))
    shares = mkt_cap / price  # price validated non-zero before this call
    if shares <= 0:
        raise ValueError("Could not estimate shares outstanding (mktCap or price invalid).")

    return equity / shares


def _compute_avg_roe(income_stmts: list[dict], balance_sheets: list[dict]) -> float:
    if len(balance_sheets) < 2:
        raise ValueError(
            "Insufficient historical data — need at least 2 years of balance sheets."
        )
    n = min(3, len(income_stmts), len(balance_sheets) - 1)

    roe_list = []
    for i in range(n):
        net_income = _safe_float(income_stmts[i].get("netIncome"))
        end_eq = _safe_float(balance_sheets[i].get("totalStockholdersEquity"))
        beg_eq = _safe_float(balance_sheets[i + 1].get("totalStockholdersEquity"))
        avg_eq = (beg_eq + end_eq) / 2
        if avg_eq == 0:
            continue
        roe_list.append(net_income / avg_eq)

    if not roe_list:
        raise ValueError("Could not compute ROE — average equity was zero for all available years.")
    return mean(roe_list)


def _compute_retention(cash_flows: list[dict], income_stmts: list[dict]) -> float:
    net_income = _safe_float(income_stmts[0].get("netIncome"))
    if net_income <= 0:
        return 0.6  # default for loss-making companies; BV growth assumption is a known simplification

    div_paid = _safe_float(cash_flows[0].get("dividendsPaid"), default=0.0)
    if div_paid == 0:
        return 0.6

    retention = 1.0 - abs(div_paid) / net_income
    return max(0.0, min(1.0, retention))


def _project_rim(bv0: float, roe: float, ke: float,
                 retention: float) -> tuple[float, float]:
    if ke <= TERMINAL_GROWTH:
        raise ValueError(
            f"Cost of equity ({ke:.2%}) ≤ terminal growth rate ({TERMINAL_GROWTH:.2%}) — "
            "model is undefined. Try a ticker with a higher beta or wait for a higher risk-free rate."
        )

    bv = bv0
    sum_pv = 0.0
    last_ri = 0.0

    for t in range(1, PROJECTION_YEARS + 1):
        last_ri = bv * (roe - ke)
        sum_pv += last_ri / (1 + ke) ** t
        bv *= 1 + roe * retention

    tv = last_ri / (ke - TERMINAL_GROWTH)
    pv_tv = tv / (1 + ke) ** PROJECTION_YEARS
    return sum_pv, pv_tv


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_rim(ticker: str) -> dict:
    """Run the Residual Income Model for a given ticker.

    All inputs are fetched from FMP. Returns a dict with intrinsic value,
    margin of safety, and a BUY/HOLD/SELL signal.
    """
    ticker = ticker.upper().strip()

    rf = get_treasury_yield() / 100
    financials = get_all_financials(ticker, period="annual", limit=5)

    profile = financials["profile"]
    sector = financials["sector"]
    income_stmts = financials["income_statement"]
    balance_sheets = financials["balance_sheet"]
    cash_flows = financials["cash_flow"]

    beta_raw = profile.get("beta")
    if beta_raw is None:
        raise ValueError(f"Beta not available in FMP profile for {ticker}.")
    beta = float(beta_raw)
    beta = max(beta, BETA_FLOOR)

    price = _safe_float(profile.get("price"))
    if price <= 0:
        raise ValueError(f"Current price is zero or missing for {ticker}.")

    ke = max(rf + beta * EQUITY_RISK_PREMIUM, MIN_COST_OF_EQUITY)
    is_fin = _is_financial(sector)

    bv0 = _compute_bvps(profile, balance_sheets, is_fin, price)
    avg_roe = _compute_avg_roe(income_stmts, balance_sheets)
    retention = _compute_retention(cash_flows, income_stmts)
    sum_pv_ri, pv_tv = _project_rim(bv0, avg_roe, ke, retention)

    iv = bv0 + sum_pv_ri + pv_tv
    mos = ((iv - price) / iv * 100) if iv != 0 else 0.0

    if mos >= MOS_BUY_THRESHOLD:
        signal = "BUY"
    elif mos <= MOS_SELL_THRESHOLD:
        signal = "SELL"
    else:
        signal = "HOLD"

    return {
        "ticker": ticker,
        "sector": sector,
        "intrinsic_value": iv,
        "current_price": price,
        "margin_of_safety_pct": mos,
        "signal": signal,
        "cost_of_equity": ke * 100,
        "avg_roe": avg_roe * 100,
        "book_value_per_share": bv0,
        "risk_free_rate": rf * 100,
        "beta": beta,
    }
