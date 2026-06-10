import json
import time

import requests
import streamlit as st

from data.earnings import _fmt_currency, _surprise_status
from data.fmp import _cache_set, _db

CACHE_TTL = 86400  # 24 hours; controls expiry via the local _cache_get below
_BASE_URL = "https://financialmodelingprep.com/stable"

# Stored in cache on stable "no data" responses so the API is not hit again
# within the TTL window.  False is JSON-serializable and distinct from None
# (which signals a cache miss).
_SENTINEL = False


# ---------------------------------------------------------------------------
# Cache helpers (local _cache_get so CACHE_TTL above is actually enforced)
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
# HTTP helper
# ---------------------------------------------------------------------------

def _get(path: str, **params):
    params["apikey"] = st.secrets["FMP_KEY"]
    resp = requests.get(f"{_BASE_URL}{path}", params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Calculation / formatting helpers
# ---------------------------------------------------------------------------

def _margin(num: float, den: float) -> float:
    if den == 0.0:
        return 0.0
    return round(num / den * 100, 1)


def _yoy(current: float, prior: float):
    if prior == 0.0:
        return None
    return round((current - prior) / abs(prior) * 100, 1)


def _pick_eps(q: dict) -> float:
    """Prefer epsDiluted; fall back to eps.  Uses is-None checks, not truthiness,
    so a legitimate zero diluted EPS is never silently replaced by basic EPS."""
    v = q.get("epsDiluted")
    if v is None:
        v = q.get("eps")
    return float(v) if v is not None else 0.0


def _quarter_label(item: dict) -> str:
    period = item.get("period", "")
    year = item.get("fiscalYear") or (item.get("date", "")[:4]) or ""
    if not year:
        return period
    return f"{period} {year}"


# ---------------------------------------------------------------------------
# Shared income-statement fetch
# Cached under a single key; both get_key_stats and get_chart_data call this
# so the /income-statement endpoint is hit only once per ticker per TTL window.
# ---------------------------------------------------------------------------

def _get_income_statements(ticker: str) -> list:
    cache_key = f"fmp-income-stmt:{ticker}"
    cached = _cache_get(cache_key)
    if _cache_hit(cached):
        return [] if cached is _SENTINEL else cached

    try:
        data = _get("/income-statement", symbol=ticker, period="quarter", limit=5)
        if not data or not isinstance(data, list):
            _cache_set(cache_key, _SENTINEL)
            return []
        _cache_set(cache_key, data)
    except Exception:
        return []  # transient errors not cached

    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_key_stats(ticker: str):
    ticker = ticker.upper()
    cache_key = f"fmp-key-stats:{ticker}"
    cached = _cache_get(cache_key)
    if _cache_hit(cached):
        return None if cached is _SENTINEL else cached

    try:
        data = _get_income_statements(ticker)
        if not data:
            return None

        q = data[0]
        revenue = float(q.get("revenue") or 0)
        net_income = float(q.get("netIncome") or 0)
        gross_profit = float(q.get("grossProfit") or 0)
        operating_income = float(q.get("operatingIncome") or 0)
        eps = _pick_eps(q)

        # Validate the YoY baseline by matching fiscal quarter label ("Q2", etc.)
        # rather than blindly using data[4], so a missing quarter doesn't silently
        # produce a wrong comparison.
        current_period = q.get("period", "")
        prior = next(
            (entry for entry in data[1:] if entry.get("period") == current_period),
            None,
        )
        if prior is not None:
            revenue_yoy = _yoy(revenue, float(prior.get("revenue") or 0))
            eps_yoy = _yoy(eps, _pick_eps(prior))
        else:
            revenue_yoy = None
            eps_yoy = None

        result = {
            "revenue": _fmt_currency(revenue),
            "net_income": _fmt_currency(net_income),
            "gross_margin": _margin(gross_profit, revenue),
            "operating_margin": _margin(operating_income, revenue),
            "eps": eps,
            "revenue_yoy": revenue_yoy,
            "eps_yoy": eps_yoy,
        }
        _cache_set(cache_key, result)
    except Exception:
        return None  # transient errors (including SQLite write failures) not cached

    return result


def get_revenue_surprise(ticker: str):
    ticker = ticker.upper()
    cache_key = f"fmp-revenue-surprise:{ticker}"
    cached = _cache_get(cache_key)
    if _cache_hit(cached):
        return None if cached is _SENTINEL else cached

    try:
        # limit=3: the first entry may be a future quarter with null actuals;
        # we only need the first one where both values are reported.
        data = _get("/earnings", symbol=ticker, limit=3)
        if not data or not isinstance(data, list):
            _cache_set(cache_key, _SENTINEL)
            return None

        most_recent = next(
            (item for item in data
             if item.get("revenueActual") is not None
             and item.get("revenueEstimated") is not None),
            None,
        )
        if most_recent is None:
            _cache_set(cache_key, _SENTINEL)
            return None

        # next() already guarantees both fields are non-None.
        actual = float(most_recent["revenueActual"])
        estimate = float(most_recent["revenueEstimated"])
        if estimate == 0.0:
            _cache_set(cache_key, _SENTINEL)
            return None

        surprise_pct = (actual - estimate) / abs(estimate) * 100
        result = {
            "actual": _fmt_currency(actual),
            "estimate": _fmt_currency(estimate),
            "surprise_pct": round(surprise_pct, 2),
            "status": _surprise_status(surprise_pct),
        }
        _cache_set(cache_key, result)
    except Exception:
        return None  # transient errors not cached

    return result


def get_chart_data(ticker: str) -> list:
    ticker = ticker.upper()
    cache_key = f"fmp-chart-data:{ticker}"
    cached = _cache_get(cache_key)
    if _cache_hit(cached):
        return [] if cached is _SENTINEL else cached

    try:
        data = _get_income_statements(ticker)
        if not data:
            return []

        # Reverse so charts render oldest → newest
        result = []
        for q in reversed(data):
            revenue = float(q.get("revenue") or 0)
            gross_profit = float(q.get("grossProfit") or 0)
            operating_income = float(q.get("operatingIncome") or 0)
            result.append({
                "label": _quarter_label(q),
                "revenue_raw": revenue,
                "eps": _pick_eps(q),
                "gross_margin": _margin(gross_profit, revenue),
                "operating_margin": _margin(operating_income, revenue),
            })
        _cache_set(cache_key, result)
    except Exception:
        return []  # transient errors not cached

    return result
