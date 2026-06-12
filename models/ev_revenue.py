import json
import time
from typing import Optional

import yfinance as yf

from data.fmp import _cache_set, _db

CACHE_TTL = 86400  # 24 hours
_SENTINEL = False

EV_REVENUE_TICKERS = ["ARWR", "ONDS", "XMTR"]

# Hardcoded sector EV/Revenue medians for peer comparison
_SECTOR_MEDIANS = {
    "XMTR": 9.56,
    "ONDS": 3.57,
    "ARWR": 7.92,
}

OVERVALUED_THRESHOLD = 1.25
UNDERVALUED_THRESHOLD = 0.80


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
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val, default: float = 0.0) -> float:
    try:
        f = float(val)
        return default if (f != f) else f  # NaN check: NaN != NaN
    except (TypeError, ValueError):
        return default


def _signal(ratio: float, median: float) -> str:
    ratio_vs = ratio / median
    if ratio_vs > OVERVALUED_THRESHOLD:
        return "OVERVALUED"
    if ratio_vs < UNDERVALUED_THRESHOLD:
        return "UNDERVALUED"
    return "FAIR"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_ev_revenue(ticker: str) -> Optional[dict]:
    ticker = ticker.upper().strip()
    if ticker not in _SECTOR_MEDIANS:
        raise ValueError(f"{ticker} is not in the supported EV/Revenue ticker list: {list(_SECTOR_MEDIANS)}")

    cache_key = f"ev-revenue:{ticker}"
    cached = _cache_get(cache_key)
    if _cache_hit(cached):
        return None if cached is _SENTINEL else cached

    try:
        yf_ticker = yf.Ticker(ticker)
        info = yf_ticker.info

        mkt_cap = _safe_float(info.get("marketCap"))
        if mkt_cap == 0:
            # Do not cache: zero may be a transient yfinance failure, not stable "no data"
            return None

        total_debt = _safe_float(info.get("totalDebt"))
        cash = _safe_float(info.get("totalCash"))

        # TTM revenue: sum 4 most recent quarterly periods
        qis = yf_ticker.quarterly_income_stmt
        if qis is None or qis.empty or "Total Revenue" not in qis.index:
            _cache_set(cache_key, _SENTINEL)
            return None

        cols = list(qis.columns)[:4]
        ttm_revenue = sum(_safe_float(qis.loc["Total Revenue", c]) for c in cols)
        if ttm_revenue == 0:
            _cache_set(cache_key, _SENTINEL)
            return None

        ev = mkt_cap + total_debt - cash
        sector_median = _SECTOR_MEDIANS[ticker]

        if ev <= 0:
            # Negative EV (cash > debt + mkt_cap): ratio is undefined
            result = {
                "ticker": ticker,
                "ev": round(ev, 2),
                "revenue": round(ttm_revenue, 2),
                "ev_revenue_ratio": None,
                "sector_median": sector_median,
                "ratio_vs_median": None,
                "signal": "N/A — Negative EV",
            }
        else:
            ev_revenue_ratio = ev / ttm_revenue
            ratio_vs_median = ev_revenue_ratio / sector_median
            result = {
                "ticker": ticker,
                "ev": round(ev, 2),
                "revenue": round(ttm_revenue, 2),
                "ev_revenue_ratio": round(ev_revenue_ratio, 2),
                "sector_median": sector_median,
                "ratio_vs_median": round(ratio_vs_median, 2),
                "signal": _signal(ev_revenue_ratio, sector_median),
            }
    except Exception:
        return None  # transient errors not cached

    _cache_set(cache_key, result)
    return result


if __name__ == "__main__":
    for t in EV_REVENUE_TICKERS:
        result = run_ev_revenue(t)
        if result is None:
            print(f"{t}: no data returned")
        elif result.get("ev_revenue_ratio") is None:
            print(f"{t}: signal={result['signal']}  EV={result['ev']:.2f}")
        else:
            print(
                f"{t}: EV/Rev={result['ev_revenue_ratio']:.2f}x  "
                f"median={result['sector_median']:.2f}x  "
                f"vs_median={result['ratio_vs_median']:.2f}x  "
                f"signal={result['signal']}"
            )
