"""Phase 1 DCF diagnostics.

Computes and prints every raw input a DCF valuation needs — historical FCF,
the data quality gate, FCF growth, and WACC — with no discounting, terminal
value, or intrinsic value math. Inputs only, so each number can be checked
before valuation logic is built on top.

Historical statements come from the real FMP REST API (financialmodelingprep.com),
not the yfinance-backed data/fmp.py module: yfinance's free annual statements cap
at 4 real fiscal years (the 5th column is always NaN padding), which can't satisfy
the 5-year requirement below. Beta, current price, and market cap still come from
yfinance via data.fmp.get_company_profile. The risk-free rate is fetched here
directly from FRED (DGS10) so fetch success is observed rather than inferred from a
fallback constant, per the fix to the earlier value-equality detection.
"""

import json
import time
import urllib.request

import requests
import yfinance as yf

from data.financials import _get  # shared real-FMP-REST helper (dedup, not a second copy)
from data.fmp import _TREASURY_FALLBACK, _cache_set, _db, get_company_profile
from models.rim import BETA_FLOOR, EQUITY_RISK_PREMIUM, MIN_COST_OF_EQUITY
from models.utils import _safe_float  # canonical NaN-filtering helper (dedup)

CACHE_TTL = 86400  # 24 hours
_SENTINEL = False

REQUIRED_HISTORY_YEARS = 5

# Large-cap tickers with a long, stable FCF history — the set the Phase 4 DCF
# tab offers for selection. Not every ticker here is guaranteed to pass the
# Phase 1 data-quality gate; the tab is responsible for checking gate_status
# per ticker and excluding failures rather than assuming this list is pre-filtered.
DCF_TICKERS = [
    "AAPL", "COST", "GE", "GOOGL", "LMT", "META", "MSFT", "NVDA", "V", "WMT"
]

# Tickers whose FMP statement endpoints return HTTP 402 ("Premium Query
# Parameter") on the current free-tier plan — a subscription-tier paywall, not
# a genuine data-quality gate failure (confirmed: no 429/rate-limit involved).
# Excluded from DCF_TICKERS so the selector doesn't dead-end on them; listed
# here purely so the tab can footnote them for transparency.
DCF_TICKERS_PENDING_PAID_TIER = ["CAT", "HD", "LLY", "LOW", "MELI", "NOC", "ORCL"]

# Used only when this module's own live FRED (DGS10) fetch fails; the source
# label then reflects the fallback so it's never mistaken for a live rate.
# Reuses data.fmp's _TREASURY_FALLBACK (the same 10-yr Treasury fallback that
# models/rim.py uses) as the single source of truth, so the RIM and DCF models
# can't silently diverge on the fallback rate when FRED is unreachable.
RF_FALLBACK = _TREASURY_FALLBACK

MARKET_PREMIUM = EQUITY_RISK_PREMIUM  # reuse rim.py's 5.5% for consistency with the RIM model


# ---------------------------------------------------------------------------
# Cache helpers (local _cache_get so this module's CACHE_TTL is enforced)
# ---------------------------------------------------------------------------

def _cache_get(key: str):
    with _db() as conn:
        row = conn.execute(
            "SELECT data, fetched FROM fmp_cache WHERE key = ?", (key,)
        ).fetchone()
    if row and (time.time() - row[1]) < CACHE_TTL:
        return json.loads(row[0])
    return None


def _cache_hit(cached) -> bool:
    return cached is not None


# ---------------------------------------------------------------------------
# Statement fetch (uses data.financials._get, the shared real-FMP-REST helper)
# ---------------------------------------------------------------------------

def _fetch_statement(path: str, ticker: str, cache_prefix: str) -> tuple[list, str]:
    """Returns (data, fetch_error). fetch_error is '' on a cache hit or a normal
    successful fetch; otherwise it's the raw HTTP status/body (or exception text)
    from the failed live call. Without this, an empty result from a fetch failure
    (bad ticker mapping, API-tier restriction, rate limit) is silently
    indistinguishable from a ticker that genuinely has < 5 years of history and
    should fail the data-quality gate — this makes the two cases printable and
    diagnosable separately."""
    cache_key = f"{cache_prefix}:{ticker}"
    cached = _cache_get(cache_key)
    if _cache_hit(cached):
        # Real statement data is a JSON list; a stable "no data" result is cached
        # as a dict carrying the ORIGINAL fetch-failure detail (see below), so a
        # cache hit within the TTL still reports why it was empty instead of
        # returning a bare ""  — otherwise a fetch failure looks identical to a
        # genuine short-history case for the rest of the 24h window.
        if isinstance(cached, list):
            return cached, ""
        if isinstance(cached, dict):
            return [], cached.get("__fetch_error__", "")
        return [], ""  # legacy bare-False _SENTINEL from an older cache entry

    try:
        data = _get(path, symbol=ticker, period="annual", limit=REQUIRED_HISTORY_YEARS)
        if not data or not isinstance(data, list):
            err = f"Empty/malformed response body: {data!r}"
            # Stable "no data" — cache the error detail (not a bare sentinel) so
            # later cache hits stay diagnosable. Distinct from the transient
            # errors below, which are never cached.
            _cache_set(cache_key, {"__fetch_error__": err})
            return [], err
        _cache_set(cache_key, data)
        return data, ""
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        body = e.response.text.strip() if e.response is not None else str(e)
        return [], f"HTTP {status}: {body[:300]}"
    except Exception as e:
        # Transient errors (network, rate-limit) never cached, same as before.
        return [], f"{type(e).__name__}: {e}"


def _get_shares_outstanding(ticker: str):
    """Standalone yfinance fetch, independent of data.fmp.get_company_profile
    (that module is not to be modified to expose this field)."""
    cache_key = f"dcf-shares-outstanding:{ticker}"
    cached = _cache_get(cache_key)
    if _cache_hit(cached):
        return None if cached is _SENTINEL else cached

    try:
        shares = _optional_float(yf.Ticker(ticker).info.get("sharesOutstanding"))
        if shares is None or shares <= 0:
            # Degraded/partial yfinance response (dict returned but key absent,
            # no exception raised) — do NOT cache, so a later call can retry
            # instead of serving "no data" for the full 24h TTL.
            return None
        _cache_set(cache_key, shares)
        return shares
    except Exception:
        return None  # transient errors not cached


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _optional_float(value):
    """Like _safe_float but returns None for a missing/NaN/non-numeric value, so a
    genuine data gap stays distinguishable from a real 0 (which would silently pass
    the data-quality gate)."""
    if value is None:
        return None
    try:
        f = float(value)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _fmt_num(value, width: int = 18) -> str:
    """Right-aligned thousands-formatted number, or 'N/A' when the value is None."""
    return f"{value:>{width},.0f}" if value is not None else f"{'N/A':>{width}}"


def _get_risk_free_rate() -> tuple[float, str]:
    """Fetch the 10-year Treasury yield (FRED DGS10) directly so fetch success is
    observed from the fetch itself, not inferred by comparing the returned value
    against a fallback constant — which would misclassify a live rate that happens
    to equal the fallback (e.g. exactly 4.5%) as a failed fetch."""
    cache_key = "dcf-treasury-yield:DGS10"
    cached = _cache_get(cache_key)
    if _cache_hit(cached):
        return cached[0], cached[1]

    try:
        url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"
        with urllib.request.urlopen(url, timeout=10) as resp:
            text = resp.read().decode("utf-8")
        value = None
        for line in reversed(text.strip().splitlines()):
            parts = line.split(",")
            if len(parts) == 2 and parts[1].strip() not in ("", "."):
                value = float(parts[1].strip())
                break
        if value is None:
            raise ValueError("No valid DGS10 value found in FRED response")
        _cache_set(cache_key, [value, "live (FRED DGS10)"])
        return value, "live (FRED DGS10)"
    except Exception:
        # Transient fetch failure — do not cache, so the next call retries.
        return RF_FALLBACK, f"fallback ({RF_FALLBACK}% — live FRED fetch failed)"


def _historical_fcf(cash_flows: list) -> list:
    """Most-recent-first, matching the FMP response order. FMP reports
    capitalExpenditure as a negative outflow, so it must be subtracted as a
    magnitude (abs) — not subtracted as-is, which would double-count it.

    A missing operatingCashFlow or capitalExpenditure yields fcf=None, so the
    data-quality gate fails that year rather than silently reading a data gap
    as a legitimate $0."""
    years = []
    for cf in cash_flows:
        ocf = _optional_float(cf.get("operatingCashFlow"))
        capex_raw = _optional_float(cf.get("capitalExpenditure"))
        if ocf is None or capex_raw is None:
            capex = abs(capex_raw) if capex_raw is not None else None
            fcf = None
        else:
            capex = abs(capex_raw)
            fcf = ocf - capex
        years.append({
            "date": cf.get("date") or "unknown",
            "operating_cash_flow": ocf,
            "capital_expenditures": capex,
            "fcf": fcf,
        })
    return years


def _data_quality_gate(fcf_years: list, fetch_errors: dict) -> tuple[str, str]:
    # A failed income-statement or balance-sheet fetch must fail the gate: WACC
    # and net_debt would otherwise silently compute from an empty {} statement
    # (every field defaulting to 0.0), producing a debt-free / tax-free valuation
    # that still reports GATE_PASSED. Checked first so the reason names the fetch
    # failure rather than the empty-FCF symptom it causes.
    failed = [name for name, err in fetch_errors.items() if err]
    if failed:
        return (
            "GATE_FAILED",
            f"Statement fetch failed ({', '.join(failed)}); cannot compute a "
            f"trustworthy valuation.",
        )
    if len(fcf_years) < REQUIRED_HISTORY_YEARS:
        return (
            "GATE_FAILED",
            f"Only {len(fcf_years)} year(s) of FCF data available "
            f"(need {REQUIRED_HISTORY_YEARS}).",
        )
    missing = [y["date"] for y in fcf_years if y["fcf"] is None]
    if missing:
        return "GATE_FAILED", f"Missing cash-flow data in: {', '.join(missing)}."
    # Reject non-positive FCF (<= 0), not just negative: an exact $0 base year
    # passes a "< 0" check but then compounds to an all-zero projection and a
    # nonsensical (negative) intrinsic value, and a $0 window endpoint makes the
    # growth CAGR undefined. Near-zero-but-positive FCF that yields an implausible
    # growth rate is caught downstream by the sanity cap, so only <= 0 is gated here.
    nonpositive = [y["date"] for y in fcf_years if y["fcf"] <= 0]
    if nonpositive:
        return "GATE_FAILED", f"Non-positive FCF in: {', '.join(nonpositive)}."
    return "GATE_PASSED", ""


def _fcf_growth(fcf_years: list) -> dict:
    chronological = list(reversed(fcf_years))  # oldest -> newest

    yoy = []
    for prev, curr in zip(chronological, chronological[1:]):
        if prev["fcf"] is None or curr["fcf"] is None or prev["fcf"] == 0:
            growth = None
        else:
            growth = (curr["fcf"] - prev["fcf"]) / abs(prev["fcf"]) * 100
        yoy.append({"from": prev["date"], "to": curr["date"], "growth_pct": growth})

    cagr = None
    n = len(chronological)
    first = chronological[0]["fcf"] if n else None
    last = chronological[-1]["fcf"] if n else None
    if n >= 2 and first is not None and last is not None and first > 0 and last > 0:
        years_span = n - 1
        cagr = ((last / first) ** (1 / years_span) - 1) * 100

    return {"yoy": yoy, "cagr_pct": cagr, "years": n}


def _sort_by_date_desc(stmts: list) -> list:
    """Defensive ordering: FMP normally returns most-recent-first, but don't rely
    on it — both the [0]-is-latest assumption and _fcf_growth's reversed() pairing
    silently produce wrong numbers if the list ever arrives out of order."""
    return sorted(stmts, key=lambda s: s.get("date") or "", reverse=True)


def _fiscal_period(stmt: dict):
    return stmt.get("fiscalYear") or stmt.get("date")


def _alignment_warning(cash_flows: list, income_stmts: list, balance_sheets: list) -> str:
    """Warn if the latest cash-flow / income / balance-sheet records don't share a
    fiscal period — WACC and FCF would otherwise silently blend different years."""
    periods = {}
    for name, stmts in (("cash flow", cash_flows),
                        ("income", income_stmts),
                        ("balance sheet", balance_sheets)):
        if stmts:
            periods[name] = _fiscal_period(stmts[0])
    if len(set(periods.values())) > 1:
        detail = ", ".join(f"{k}={v}" for k, v in periods.items())
        return f"Latest-period mismatch across statements ({detail}); WACC may blend fiscal years."
    return ""


def _wacc_components(profile: dict, income_stmts: list, balance_sheets: list, rf: float) -> dict:
    # beta / mktCap are validated non-None and positive by compute_dcf_inputs
    # before this runs, so read them directly instead of masking a missing value
    # with a silent default. BETA_FLOOR is a legitimate floor (matches rim.py).
    beta = max(float(profile["beta"]), BETA_FLOOR)
    cost_of_equity = max(rf + beta * MARKET_PREMIUM, MIN_COST_OF_EQUITY)

    income = income_stmts[0] if income_stmts else {}
    balance = balance_sheets[0] if balance_sheets else {}

    interest_expense = _safe_float(income.get("interestExpense"))
    total_debt = _safe_float(balance.get("totalDebt"))
    cost_of_debt = interest_expense / total_debt if total_debt > 0 else 0.0
    # Bound to [0, 100%] so a disproportionate interest/debt pairing (e.g. a tiny
    # residual debt line item) can't emit an absurd rate — mirrors the tax_rate clamp.
    cost_of_debt = max(0.0, min(1.0, cost_of_debt))

    pretax_income = _safe_float(income.get("incomeBeforeTax"))
    income_tax_expense = _safe_float(income.get("incomeTaxExpense"))
    tax_rate = income_tax_expense / pretax_income if pretax_income > 0 else 0.0
    tax_rate = max(0.0, min(1.0, tax_rate))

    after_tax_cost_of_debt = cost_of_debt * (1 - tax_rate)

    market_cap = float(profile["mktCap"])
    capital_base = market_cap + total_debt
    equity_weight = market_cap / capital_base if capital_base > 0 else 1.0
    debt_weight = 1.0 - equity_weight

    wacc = equity_weight * cost_of_equity + debt_weight * after_tax_cost_of_debt

    return {
        "beta": beta,
        "cost_of_equity_pct": cost_of_equity * 100,
        "interest_expense": interest_expense,
        "total_debt": total_debt,
        "cost_of_debt_pct": cost_of_debt * 100,
        "pretax_income": pretax_income,
        "income_tax_expense": income_tax_expense,
        "tax_rate_pct": tax_rate * 100,
        "after_tax_cost_of_debt_pct": after_tax_cost_of_debt * 100,
        "market_cap": market_cap,
        "equity_weight_pct": equity_weight * 100,
        "debt_weight_pct": debt_weight * 100,
        "wacc_pct": wacc * 100,
    }


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def compute_dcf_inputs(ticker: str) -> dict:
    """Fetch and compute every Phase-1 DCF input for `ticker`."""
    ticker = ticker.upper().strip()

    rf_pct, rf_source = _get_risk_free_rate()
    rf = rf_pct / 100

    profile = get_company_profile(ticker)

    # Surface missing market data loudly (matching models/rim.py) rather than
    # silently substituting a floor/zero that looks like a real value.
    if profile.get("beta") is None:
        raise ValueError(f"Beta not available for {ticker}.")
    price = _safe_float(profile.get("price"))
    if price <= 0:
        raise ValueError(f"Current price is zero or missing for {ticker}.")
    if _safe_float(profile.get("mktCap")) <= 0:
        raise ValueError(f"Market cap is zero or missing for {ticker}.")

    shares_outstanding = _get_shares_outstanding(ticker)

    cash_flows_raw, cash_flow_fetch_error = _fetch_statement(
        "/cash-flow-statement", ticker, "dcf-cash-flow-statement")
    income_stmts_raw, income_fetch_error = _fetch_statement(
        "/income-statement", ticker, "dcf-income-statement")
    balance_sheets_raw, balance_sheet_fetch_error = _fetch_statement(
        "/balance-sheet-statement", ticker, "dcf-balance-sheet-statement")

    cash_flows = _sort_by_date_desc(cash_flows_raw)
    income_stmts = _sort_by_date_desc(income_stmts_raw)
    balance_sheets = _sort_by_date_desc(balance_sheets_raw)

    alignment_warning = _alignment_warning(cash_flows, income_stmts, balance_sheets)

    fcf_years = _historical_fcf(cash_flows)
    gate_status, gate_reason = _data_quality_gate(
        fcf_years,
        {
            "cash flow": cash_flow_fetch_error,
            "income": income_fetch_error,
            "balance sheet": balance_sheet_fetch_error,
        },
    )
    growth = _fcf_growth(fcf_years)
    wacc = _wacc_components(profile, income_stmts, balance_sheets, rf)

    balance = balance_sheets[0] if balance_sheets else {}
    total_debt = wacc["total_debt"]  # reuse the value _wacc_components already computed
    cash = _safe_float(balance.get("cashAndCashEquivalents"))
    # Fall back to total_debt - cash whenever netDebt is missing OR malformed
    # (non-numeric), not only when it is None — a bad value must not silently
    # become 0.0 and contradict the debt/cash figures we already have.
    net_debt = _optional_float(balance.get("netDebt"))
    if net_debt is None:
        net_debt = total_debt - cash

    return {
        "ticker": ticker,
        "fcf_years": fcf_years,
        "gate_status": gate_status,
        "gate_reason": gate_reason,
        "alignment_warning": alignment_warning,
        "growth": growth,
        "wacc": wacc,
        "risk_free_rate_pct": rf_pct,
        "risk_free_rate_source": rf_source,
        "current_price": price,
        "shares_outstanding": shares_outstanding,
        "total_debt": total_debt,
        "cash": cash,
        "net_debt": net_debt,
        "beta": wacc["beta"],
        "cash_flow_fetch_error": cash_flow_fetch_error,
        "income_fetch_error": income_fetch_error,
        "balance_sheet_fetch_error": balance_sheet_fetch_error,
    }


def run_dcf_diagnostics(ticker: str) -> dict:
    """Print a clean, labeled diagnostic table of all Phase-1 DCF inputs for `ticker`."""
    inputs = compute_dcf_inputs(ticker)

    print(f"\n{'=' * 72}")
    print(f" DCF DIAGNOSTICS — {inputs['ticker']}")
    print(f"{'=' * 72}")

    print("\n--- 1. HISTORICAL FREE CASH FLOW ---")
    if not inputs["fcf_years"]:
        print("  No cash flow data returned.")
    for y in reversed(inputs["fcf_years"]):  # oldest -> newest
        print(
            f"  {y['date']:<12} OCF: {_fmt_num(y['operating_cash_flow'])}  "
            f"CapEx: {_fmt_num(y['capital_expenditures'])}  "
            f"FCF: {_fmt_num(y['fcf'])}"
        )

    print("\n--- 2. DATA QUALITY GATE ---")
    print(f"  Status: {inputs['gate_status']}")
    if inputs["gate_reason"]:
        print(f"  Reason: {inputs['gate_reason']}")
    if inputs["alignment_warning"]:
        print(f"  ⚠ WARNING: {inputs['alignment_warning']}")

    print("\n--- 3. HISTORICAL FCF GROWTH ---")
    for g in inputs["growth"]["yoy"]:
        growth_str = f"{g['growth_pct']:.1f}%" if g["growth_pct"] is not None else "N/A"
        print(f"  {g['from']} -> {g['to']}: {growth_str}")
    cagr = inputs["growth"]["cagr_pct"]
    n_years = inputs["growth"]["years"]
    print(f"  {n_years}-Year FCF CAGR: {f'{cagr:.1f}%' if cagr is not None else 'N/A'}")

    w = inputs["wacc"]
    print("\n--- 4. WACC ---")
    print(f"  Beta:                    {w['beta']:.2f}")
    print(f"  Cost of Equity (CAPM):   {w['cost_of_equity_pct']:.2f}%")
    print(f"  Interest Expense:        {w['interest_expense']:,.0f}")
    print(f"  Total Debt:              {w['total_debt']:,.0f}")
    print(f"  Cost of Debt:            {w['cost_of_debt_pct']:.2f}%")
    print(f"  Pretax Income:           {w['pretax_income']:,.0f}")
    print(f"  Income Tax Expense:      {w['income_tax_expense']:,.0f}")
    print(f"  Tax Rate:                {w['tax_rate_pct']:.2f}%")
    print(f"  After-Tax Cost of Debt:  {w['after_tax_cost_of_debt_pct']:.2f}%")
    print(f"  Market Cap:              {w['market_cap']:,.0f}")
    print(f"  Equity Weight:           {w['equity_weight_pct']:.2f}%")
    print(f"  Debt Weight:             {w['debt_weight_pct']:.2f}%")
    print(f"  WACC:                    {w['wacc_pct']:.2f}%")

    print("\n--- 5. SUPPORTING DATA ---")
    print(f"  Current Price:           {inputs['current_price']:,.2f}")
    shares = inputs["shares_outstanding"]
    print(f"  Shares Outstanding:      {shares:,.0f}" if shares else "  Shares Outstanding:      N/A")
    print(f"  Total Debt:              {inputs['total_debt']:,.0f}")
    print(f"  Cash:                    {inputs['cash']:,.0f}")
    print(f"  Net Debt:                {inputs['net_debt']:,.0f}")
    print(f"  Beta:                    {inputs['beta']:.2f}")
    print(f"  Risk-Free Rate:          {inputs['risk_free_rate_pct']:.2f}% ({inputs['risk_free_rate_source']})")

    print(f"\n{'=' * 72}\n")

    return inputs


# ---------------------------------------------------------------------------
# Phase 2: tapered growth projection, terminal value, and intrinsic value
#
# Built entirely on top of compute_dcf_inputs() (Phase 1) — no Phase 1 function
# above this point is modified. Output ordering is deliberate: every projection
# assumption (taper schedule, terminal growth, WACC, terminal value, enterprise
# value, intrinsic value per share) prints before current price appears
# anywhere, so the assumptions can be judged on their own merits, not reverse-
# engineered to match the stock price. Margin of safety — the only place price
# and intrinsic value are compared — is a separate, final section.
# ---------------------------------------------------------------------------

TERMINAL_GROWTH_PCT = 3.5  # long-run GDP-growth proxy used as the terminal FCF growth rate
PROJECTION_YEARS = 5


SMOOTHING_LOOKBACK_YEARS = 3


def _starting_growth_rate_pct(growth: dict):
    """Single most-recent YoY FCF growth already computed in Phase 1 (last entry
    of the chronological yoy list). Falls back to the multi-year CAGR if that
    single year-over-year figure is undefined (e.g. a $0 FCF base year).

    Serves two roles in compute_dcf_projection: (1) the "old" basis printed for
    side-by-side comparison against the smoothed figure, and (2) the FALLBACK
    that drives the taper when the smoothed 3-yr FCF CAGR is undefined (a
    non-positive FCF endpoint in the smoothing window) — but only if it is itself
    within the sanity ceiling. Whenever the smoothed CAGR is available (the normal
    case) that drives the taper and this value is used for comparison only.
    Returns None (caller must check) if neither is available."""
    yoy = growth["yoy"]
    if yoy and yoy[-1]["growth_pct"] is not None:
        return yoy[-1]["growth_pct"]
    return growth["cagr_pct"]


def _smoothed_growth_rate_pct(fcf_years: list, lookback: int = SMOOTHING_LOOKBACK_YEARS):
    """CAGR of FCF from the earliest to the most recent of the last `lookback`
    FCF values (fcf_years is most-recent-first, so window[0] is newest and
    window[-1] is oldest of the window). CAGR only compares the window's two
    endpoints, so a single-year low-base rebound sitting *between* them washes
    out instead of dominating the figure the way an average of raw YoY
    percentages does (e.g. a FCF collapse-then-rebound year inflates a YoY
    average far more than it inflates the endpoint-to-endpoint CAGR).
    Returns None (caller must check) if the window is unavailable or either
    endpoint is non-positive (CAGR is undefined for zero/negative FCF)."""
    window = fcf_years[:lookback]
    if len(window) < lookback:
        return None
    newest, oldest = window[0]["fcf"], window[-1]["fcf"]
    if newest is None or oldest is None or newest <= 0 or oldest <= 0:
        return None
    years_span = lookback - 1
    return ((newest / oldest) ** (1 / years_span) - 1) * 100


STARTING_GROWTH_CEILING_PCT = 40.0
STARTING_GROWTH_FLOOR_PCT = 0.0
# Sanity bounds: an implausibly high smoothed starting growth rate is far more
# likely a data/base-year artifact than a sustainable 5-year trend, so it's
# capped. Symmetrically, a negative smoothed rate would taper the projection
# down from a shrinking base — the model's growing-company assumptions (single
# taper toward a positive terminal growth rate) don't hold below 0%, so it's
# floored instead. Either way the raw figure is flagged for manual review
# rather than silently fed into the taper.


def _apply_growth_bounds(
    start_growth_pct: float,
    floor_pct: float = STARTING_GROWTH_FLOOR_PCT,
    ceiling_pct: float = STARTING_GROWTH_CEILING_PCT,
):
    """Returns (growth_pct_to_use, was_capped, was_floored)."""
    if start_growth_pct > ceiling_pct:
        return ceiling_pct, True, False
    if start_growth_pct < floor_pct:
        return floor_pct, False, True
    return start_growth_pct, False, False


def _taper_schedule(start_growth_pct: float, terminal_growth_pct: float, n: int) -> list:
    """Linearly interpolate n growth rates from start_growth_pct (year 1) down to
    terminal_growth_pct (year n), inclusive at both ends — so the final projected
    year already grows at the terminal rate, consistent with the perpetuity TV
    formula applied to it."""
    if n == 1:
        return [terminal_growth_pct]
    step = (terminal_growth_pct - start_growth_pct) / (n - 1)
    return [start_growth_pct + step * i for i in range(n)]


def _project_fcf(last_actual_fcf: float, growth_schedule_pct: list) -> list:
    """Apply each year's tapered growth rate to the PRIOR year's FCF (year 1 grows
    off the last actual historical FCF, not off itself)."""
    projections = []
    prior_fcf = last_actual_fcf
    for year, growth_pct in enumerate(growth_schedule_pct, start=1):
        fcf = prior_fcf * (1 + growth_pct / 100)
        projections.append({"year": year, "growth_pct": growth_pct, "fcf": fcf})
        prior_fcf = fcf
    return projections


def _terminal_value(final_year_fcf: float, wacc_pct: float, terminal_growth_pct: float):
    """TV = FCF_n * (1 + g) / (WACC - g). Returns (tv, error) — error is set instead
    of dividing by a non-positive spread when WACC doesn't exceed terminal growth."""
    wacc = wacc_pct / 100
    g = terminal_growth_pct / 100
    if wacc <= g:
        return None, (
            f"WACC ({wacc_pct:.2f}%) <= terminal growth ({terminal_growth_pct:.2f}%); "
            "terminal value is undefined (would divide by a non-positive spread)."
        )
    return final_year_fcf * (1 + g) / (wacc - g), ""


def _discount_to_present(projected_fcf: list, terminal_value: float, wacc_pct: float) -> dict:
    """Discount each projected year's FCF, and the terminal value (received at the
    end of the final projected year), back to present value at WACC."""
    wacc = wacc_pct / 100
    discounted_years = []
    pv_fcf_sum = 0.0
    for p in projected_fcf:
        discount_factor = 1 / (1 + wacc) ** p["year"]
        pv = p["fcf"] * discount_factor
        discounted_years.append({**p, "discount_factor": discount_factor, "pv": pv})
        pv_fcf_sum += pv

    final_year = projected_fcf[-1]["year"]
    tv_discount_factor = 1 / (1 + wacc) ** final_year
    pv_terminal_value = terminal_value * tv_discount_factor

    return {
        "years": discounted_years,
        "pv_fcf_sum": pv_fcf_sum,
        "tv_discount_factor": tv_discount_factor,
        "pv_terminal_value": pv_terminal_value,
        "enterprise_value": pv_fcf_sum + pv_terminal_value,
    }


def _intrinsic_value_per_share(enterprise_value: float, net_debt: float, shares_outstanding):
    if not shares_outstanding:
        return None, "Shares outstanding unavailable; cannot compute per-share intrinsic value."
    equity_value = enterprise_value - net_debt
    return equity_value / shares_outstanding, ""


def _margin_of_safety_pct(intrinsic_value_per_share: float, current_price: float) -> float:
    return (intrinsic_value_per_share - current_price) / current_price * 100


def _project_from_growth(
    inputs: dict, start_growth_pct: float, wacc_pct: float = None, terminal_growth_pct: float = None
) -> dict:
    """Runs the taper -> terminal value -> discount -> intrinsic-value pipeline
    for a single starting-growth-rate assumption. Shared by both the old
    (single-year) and new (smoothed) bases so they run through an identical
    pipeline and are only ever different in the one input being compared.

    wacc_pct and terminal_growth_pct default to the base-case values (the
    computed WACC and the module's TERMINAL_GROWTH_PCT) so every existing call
    site is unaffected; Phase 3's sensitivity grid overrides them per-cell to
    reuse this exact pipeline instead of duplicating the terminal-value/
    discounting math."""
    last_actual_fcf = inputs["fcf_years"][0]["fcf"]  # most-recent-first
    if wacc_pct is None:
        wacc_pct = inputs["wacc"]["wacc_pct"]
    if terminal_growth_pct is None:
        terminal_growth_pct = TERMINAL_GROWTH_PCT

    taper = _taper_schedule(start_growth_pct, terminal_growth_pct, PROJECTION_YEARS)
    projected_fcf = _project_fcf(last_actual_fcf, taper)

    tv, tv_error = _terminal_value(projected_fcf[-1]["fcf"], wacc_pct, terminal_growth_pct)
    if tv is None:
        return {
            "status": "PROJECTION_FAILED", "reason": tv_error,
            "projected_fcf": projected_fcf, "terminal_value": None,
            "discounted": None, "enterprise_value": None, "intrinsic_value_per_share": None,
        }

    discounted = _discount_to_present(projected_fcf, tv, wacc_pct)
    intrinsic_value, iv_error = _intrinsic_value_per_share(
        discounted["enterprise_value"], inputs["net_debt"], inputs["shares_outstanding"]
    )
    return {
        "status": "PROJECTION_OK" if intrinsic_value is not None else "PROJECTION_FAILED",
        "reason": iv_error,
        "projected_fcf": projected_fcf,
        "terminal_value": tv,
        "discounted": discounted,
        "enterprise_value": discounted["enterprise_value"],
        "intrinsic_value_per_share": intrinsic_value,
    }


def compute_dcf_projection(inputs: dict) -> dict:
    """Phase 2: tapered projection, terminal value, discounting, and intrinsic
    value — built on already-computed Phase 1 inputs. Callers must check
    inputs['gate_status'] == 'GATE_PASSED' before calling this; it does not
    re-check the gate itself.

    The taper's starting growth rate is the smoothed 3-year FCF CAGR
    (_smoothed_growth_rate_pct), not the single most-recent YoY figure — one
    unusually strong/weak year shouldn't set the trajectory for the whole 5-year
    projection. That smoothed figure is clamped to [STARTING_GROWTH_FLOOR_PCT,
    STARTING_GROWTH_CEILING_PCT] (_apply_growth_bounds) rather than fed straight
    into the taper — implausibly high, and negative, raw figures are both more
    likely a base-year artifact than a trend to project 5 years forward on. The
    old single-year basis is still computed and run through the same pipeline
    purely so the two can be printed and compared side by side."""
    single_year_growth_pct = _starting_growth_rate_pct(inputs["growth"])
    smoothed_growth_pct_raw = _smoothed_growth_rate_pct(inputs["fcf_years"])

    result = {
        "projection_status": "PROJECTION_OK",
        "projection_reason": "",
        "single_year_growth_pct": single_year_growth_pct,
        "smoothed_growth_pct_raw": smoothed_growth_pct_raw,
        "start_growth_pct": None,
        "growth_rate_capped": False,
        "growth_rate_floored": False,
        "terminal_growth_pct": TERMINAL_GROWTH_PCT,
        "projected_fcf": None,
        "terminal_value": None,
        "discounted": None,
        "enterprise_value": None,
        "intrinsic_value_per_share": None,
        "old_basis": None,  # full old-basis projection, for comparison only
    }

    # Pick the starting growth rate that drives the taper.
    #  - Normal path: the smoothed 3-yr FCF CAGR, clamped to [floor, ceiling] —
    #    capped if implausibly high, floored (to 0%) if negative.
    #  - Fallback: when the smoothed CAGR is undefined (a non-positive FCF
    #    endpoint in the 3-yr window), fall back to the single-year YoY figure,
    #    clamped the same way, but only used at all if it is itself defined AND
    #    within the sanity ceiling.
    #  - Otherwise there is no trustworthy basis (no smoothed trend, and the
    #    fallback is missing or itself implausibly high) — fail cleanly instead
    #    of projecting off a number we don't trust (or crashing on the print).
    if smoothed_growth_pct_raw is not None:
        start_growth_pct, growth_rate_capped, growth_rate_floored = _apply_growth_bounds(smoothed_growth_pct_raw)
    elif single_year_growth_pct is not None and single_year_growth_pct <= STARTING_GROWTH_CEILING_PCT:
        start_growth_pct, growth_rate_capped, growth_rate_floored = _apply_growth_bounds(single_year_growth_pct)
    else:
        result["projection_status"] = "PROJECTION_FAILED"
        result["projection_reason"] = (
            "No trustworthy starting growth rate: the 3-yr FCF CAGR is undefined "
            "(non-positive FCF in the smoothing window) and the single-year YoY "
            "fallback is unavailable or exceeds the sanity ceiling."
        )
        if single_year_growth_pct is not None:
            result["old_basis"] = _project_from_growth(inputs, single_year_growth_pct)
        return result

    result["start_growth_pct"] = start_growth_pct
    result["growth_rate_capped"] = growth_rate_capped
    result["growth_rate_floored"] = growth_rate_floored

    primary = _project_from_growth(inputs, start_growth_pct)
    result["projected_fcf"] = primary["projected_fcf"]
    result["terminal_value"] = primary["terminal_value"]
    result["discounted"] = primary["discounted"]
    result["enterprise_value"] = primary["enterprise_value"]
    result["intrinsic_value_per_share"] = primary["intrinsic_value_per_share"]
    if primary["status"] == "PROJECTION_FAILED":
        result["projection_status"] = "PROJECTION_FAILED"
        result["projection_reason"] = primary["reason"]

    if single_year_growth_pct is not None:
        result["old_basis"] = _project_from_growth(inputs, single_year_growth_pct)

    return result


# ---------------------------------------------------------------------------
# Phase 3: WACC x terminal-growth sensitivity grid
#
# Built entirely on top of Phase 2's _project_from_growth (now generalized to
# accept wacc_pct/terminal_growth_pct overrides, defaulting to the base case so
# every existing Phase 2 call site is unchanged). No terminal-value or
# discounting math is reimplemented here — every grid cell reruns the same
# pipeline Phase 2 already uses, just at a different WACC/terminal-growth pair,
# including the base-case cell itself (offset 0.0/0.0), so it can be diffed
# against Phase 2's own point estimate as a sanity check instead of assumed
# consistent.
# ---------------------------------------------------------------------------

WACC_SENSITIVITY_STEPS_PCT = [-3.0, -1.5, 0.0, 1.5, 3.0]
TERMINAL_GROWTH_SENSITIVITY_STEPS_PCT = [-2.0, -1.0, 0.0, 1.0, 2.0]


def compute_sensitivity_grid(inputs: dict, projection: dict) -> dict:
    """WACC (rows) x terminal growth (columns) grid of intrinsic value/share.

    Callers must check projection['start_growth_pct'] is not None (i.e. the
    Phase 2 projection succeeded) before calling this. start_growth_pct — the
    taper's starting point — is held fixed at Phase 2's already-chosen value
    for every cell; only WACC and terminal growth vary, per the requested grid
    axes. The base case (offset 0.0/0.0) is included as an ordinary cell, not
    special-cased, so it runs through _project_from_growth exactly like every
    other cell."""
    base_wacc_pct = inputs["wacc"]["wacc_pct"]
    base_terminal_growth_pct = projection["terminal_growth_pct"]
    start_growth_pct = projection["start_growth_pct"]

    wacc_values = [base_wacc_pct + step for step in WACC_SENSITIVITY_STEPS_PCT]
    terminal_growth_values = [
        base_terminal_growth_pct + step for step in TERMINAL_GROWTH_SENSITIVITY_STEPS_PCT
    ]

    rows = []
    for wacc_pct in wacc_values:
        row = []
        for tg_pct in terminal_growth_values:
            # _project_from_growth -> _terminal_value already guards WACC <= terminal
            # growth (returns intrinsic_value_per_share=None with a reason instead of
            # dividing by a non-positive spread); reused as-is, not reimplemented, so
            # a grid cell can never silently produce a nonsense value in a case where
            # Phase 2 itself would have failed.
            cell_result = _project_from_growth(inputs, start_growth_pct, wacc_pct, tg_pct)
            row.append({
                "wacc_pct": wacc_pct,
                "terminal_growth_pct": tg_pct,
                "intrinsic_value_per_share": cell_result["intrinsic_value_per_share"],
                # Exact-float comparison is safe here: wacc_values/terminal_growth_values
                # are built as base + step, and the base-case step is exactly 0.0, so
                # adding it reproduces the original float bit-for-bit.
                "is_base_case": wacc_pct == base_wacc_pct and tg_pct == base_terminal_growth_pct,
                "error": cell_result["reason"] if cell_result["intrinsic_value_per_share"] is None else "",
            })
        rows.append(row)

    return {
        "wacc_values": wacc_values,
        "terminal_growth_values": terminal_growth_values,
        "rows": rows,
        "base_wacc_pct": base_wacc_pct,
        "base_terminal_growth_pct": base_terminal_growth_pct,
    }


def _print_sensitivity_grid(grid: dict) -> None:
    col_w = 11
    header_cells = "".join(f"{tg:>{col_w}.2f}%" for tg in grid["terminal_growth_values"])
    row_label = "WACC / TermG"
    print(f"  {row_label:<15}{header_cells}")
    for wacc_pct, row in zip(grid["wacc_values"], grid["rows"]):
        cells = []
        for cell in row:
            if cell["intrinsic_value_per_share"] is None:
                val_str = "N/A"
            else:
                val_str = f"{cell['intrinsic_value_per_share']:,.2f}"
            if cell["is_base_case"]:
                val_str += "*"
            cells.append(f"{val_str:>{col_w + 1}}")
        print(f"  {wacc_pct:>11.2f}%   " + "".join(cells))
    print("  (* = base-case assumptions — matches Phase 2's point-estimate intrinsic value/share)")


def run_dcf_valuation(ticker: str) -> dict:
    """Phase 1 + Phase 2, printed in one pass. Historical FCF, the data-quality
    gate, historical growth, and WACC print first (all Phase 1, via
    compute_dcf_inputs — no Phase 1 print or logic reused/altered from
    run_dcf_diagnostics). If the gate fails, printing stops there — no
    projection math runs. If it passes, every projection assumption prints
    next. Current price and margin of safety are held back until a final,
    separate, clearly-labeled section at the very end."""
    inputs = compute_dcf_inputs(ticker)

    print(f"\n{'=' * 72}")
    print(f" DCF VALUATION — {inputs['ticker']}")
    print(f"{'=' * 72}")

    print("\n--- 1. HISTORICAL FREE CASH FLOW ---")
    if not inputs["fcf_years"]:
        print("  No cash flow data returned.")
        if inputs["cash_flow_fetch_error"]:
            print(f"  ⚠ FETCH FAILURE, not a genuine short-history gate failure:")
            print(f"    {inputs['cash_flow_fetch_error']}")
    for y in reversed(inputs["fcf_years"]):  # oldest -> newest
        print(
            f"  {y['date']:<12} OCF: {_fmt_num(y['operating_cash_flow'])}  "
            f"CapEx: {_fmt_num(y['capital_expenditures'])}  "
            f"FCF: {_fmt_num(y['fcf'])}"
        )

    print("\n--- 2. DATA QUALITY GATE ---")
    print(f"  Status: {inputs['gate_status']}")
    if inputs["gate_reason"]:
        print(f"  Reason: {inputs['gate_reason']}")
    for label, err in (("Income Statement", inputs["income_fetch_error"]),
                       ("Balance Sheet Statement", inputs["balance_sheet_fetch_error"])):
        if err:
            print(f"  ⚠ FETCH FAILURE ({label}): {err}")
    if inputs["alignment_warning"]:
        print(f"  ⚠ WARNING: {inputs['alignment_warning']}")

    if inputs["gate_status"] != "GATE_PASSED":
        print(f"\n{'=' * 72}")
        print(" PROJECTION SKIPPED — data quality gate did not pass.")
        print(f"{'=' * 72}\n")
        return {**inputs, "projection_status": "GATE_FAILED"}

    print("\n--- 3. HISTORICAL FCF GROWTH ---")
    for g in inputs["growth"]["yoy"]:
        growth_str = f"{g['growth_pct']:.1f}%" if g["growth_pct"] is not None else "N/A"
        print(f"  {g['from']} -> {g['to']}: {growth_str}")
    cagr = inputs["growth"]["cagr_pct"]
    print(f"  {inputs['growth']['years']}-Year FCF CAGR: {f'{cagr:.1f}%' if cagr is not None else 'N/A'}")

    w = inputs["wacc"]
    print("\n--- 4. WACC ---")
    print(f"  Cost of Equity (CAPM):   {w['cost_of_equity_pct']:.2f}%")
    print(f"  After-Tax Cost of Debt:  {w['after_tax_cost_of_debt_pct']:.2f}%")
    print(f"  Equity Weight:           {w['equity_weight_pct']:.2f}%")
    print(f"  Debt Weight:             {w['debt_weight_pct']:.2f}%")
    print(f"  WACC:                    {w['wacc_pct']:.2f}%")

    projection = compute_dcf_projection(inputs)

    print("\n--- 5. TAPERED GROWTH PROJECTION (5 YEARS) ---")
    old_g = projection["single_year_growth_pct"]
    smoothed_g_raw = projection["smoothed_growth_pct_raw"]
    used_g = projection["start_growth_pct"]
    print(f"  Old basis  — single most-recent YoY growth:        "
          f"{f'{old_g:.2f}%' if old_g is not None else 'N/A'}")
    print(f"  New basis  — {SMOOTHING_LOOKBACK_YEARS}-yr FCF CAGR (raw):                "
          f"{f'{smoothed_g_raw:.2f}%' if smoothed_g_raw is not None else 'N/A (fell back to old basis)'}")
    if projection["growth_rate_capped"]:
        print(f"  ⚠ SANITY CAP: starting growth rate unusually high, review manually.")
        print(f"    Raw {SMOOTHING_LOOKBACK_YEARS}-yr FCF CAGR ({smoothed_g_raw:.2f}%) exceeds the "
              f"{STARTING_GROWTH_CEILING_PCT:.2f}% ceiling — capped for the taper.")
    if projection["growth_rate_floored"]:
        raw_pct = smoothed_g_raw if smoothed_g_raw is not None else old_g
        raw_label = (
            f"{SMOOTHING_LOOKBACK_YEARS}-yr FCF CAGR" if smoothed_g_raw is not None
            else "single-year YoY growth (fallback basis)"
        )
        print(f"  ⚠ SANITY FLOOR: starting growth rate negative, review manually.")
        print(f"    Raw {raw_label} ({raw_pct:.2f}%) is below the "
              f"{STARTING_GROWTH_FLOOR_PCT:.2f}% floor — floored for the taper.")
    print(f"  New basis  — used in taper (after cap/floor):       "
          f"{f'{used_g:.2f}%' if used_g is not None else 'N/A'}")
    print(f"  Terminal growth rate:                               {TERMINAL_GROWTH_PCT:.2f}%")
    if projection["projected_fcf"] is None:
        print(f"\n  ⚠ PROJECTION FAILED: {projection['projection_reason']}")
        print(f"\n{'=' * 72}\n")
        return {**inputs, **projection}
    for p in projection["projected_fcf"]:
        print(f"  Year {p['year']}: growth {p['growth_pct']:>6.2f}%   FCF: {_fmt_num(p['fcf'])}")

    print("\n--- 6. TERMINAL VALUE ---")
    print(f"  WACC:              {w['wacc_pct']:.2f}%")
    print(f"  Terminal Growth:   {TERMINAL_GROWTH_PCT:.2f}%")
    if projection["terminal_value"] is None:
        print(f"\n  ⚠ PROJECTION FAILED: {projection['projection_reason']}")
        print(f"\n{'=' * 72}\n")
        return {**inputs, **projection}
    print(f"  Terminal Value:    {_fmt_num(projection['terminal_value'])}")

    print("\n--- 7. DISCOUNTING & ENTERPRISE VALUE ---")
    d = projection["discounted"]
    for y in d["years"]:
        print(
            f"  Year {y['year']}: discount factor {y['discount_factor']:.4f}   "
            f"PV: {_fmt_num(y['pv'])}"
        )
    print(f"  PV of Terminal Value (factor {d['tv_discount_factor']:.4f}): {_fmt_num(d['pv_terminal_value'])}")
    print(f"  Enterprise Value:  {_fmt_num(projection['enterprise_value'])}")

    print("\n--- 8. INTRINSIC VALUE PER SHARE (assumption-based estimate) ---")
    print(f"  Net Debt:                {_fmt_num(inputs['net_debt'])}")
    print(f"  Shares Outstanding:      {_fmt_num(inputs['shares_outstanding'])}")
    if projection["intrinsic_value_per_share"] is None:
        print(f"\n  ⚠ PROJECTION FAILED: {projection['projection_reason']}")
        print(f"\n{'=' * 72}\n")
        return {**inputs, **projection}
    print(f"  Intrinsic Value/Share:   {projection['intrinsic_value_per_share']:,.2f}  (assumption-based estimate)")

    print("\n--- 9. GROWTH-BASIS COMPARISON (old single-year vs. new 3-yr CAGR) ---")
    old_basis = projection["old_basis"]
    old_iv = old_basis["intrinsic_value_per_share"] if old_basis else None
    print(f"  Old basis  (start growth {f'{old_g:.2f}%' if old_g is not None else 'N/A':>7}): "
          f"Intrinsic Value/Share = {f'{old_iv:,.2f}' if old_iv is not None else 'N/A'}")
    bound_suffix = (
        " [capped]" if projection["growth_rate_capped"]
        else " [floored]" if projection["growth_rate_floored"]
        else ""
    )
    print(f"  New basis  (start growth {f'{used_g:.2f}%' if used_g is not None else 'N/A':>7}"
          f"{bound_suffix}): "
          f"Intrinsic Value/Share = {projection['intrinsic_value_per_share']:,.2f}")

    print("\n--- 10. SENSITIVITY GRID: INTRINSIC VALUE/SHARE (WACC x Terminal Growth) ---")
    grid = compute_sensitivity_grid(inputs, projection)
    _print_sensitivity_grid(grid)

    mos_pct = _margin_of_safety_pct(projection["intrinsic_value_per_share"], inputs["current_price"])

    print(f"\n{'=' * 72}")
    print(" MARGIN OF SAFETY (final — current price compared against the above)")
    print(f"{'=' * 72}")
    print(f"  Current Price:           {inputs['current_price']:,.2f}")
    print(f"  Intrinsic Value/Share:   {projection['intrinsic_value_per_share']:,.2f}")
    print(f"  Margin of Safety:        {mos_pct:+.1f}%  ({'undervalued' if mos_pct > 0 else 'overvalued'})")
    print(f"{'=' * 72}\n")

    return {**inputs, **projection, "margin_of_safety_pct": mos_pct, "sensitivity_grid": grid}


if __name__ == "__main__":
    for _ticker in ["AAPL", "MSFT", "GOOGL", "NVDA", "COST"]:
        try:
            run_dcf_valuation(_ticker)
        except Exception as e:
            print(f"\n{_ticker}: FAILED — {e}\n")
