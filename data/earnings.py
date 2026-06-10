import json
import math
import time

import requests
import streamlit as st

from data.fmp import _cache_set, _db

CACHE_TTL = 86400  # 24 hours
_SENTINEL = False
_BASE_URL = "https://financialmodelingprep.com/stable"


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


def _from_cache(cached):
    return None if cached is _SENTINEL else cached


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _fmp_get(path: str, **params):
    params["apikey"] = st.secrets["FMP_KEY"]
    resp = requests.get(f"{_BASE_URL}{path}", params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _surprise_status(pct: float) -> str:
    if pct > 1:
        return "BEAT"
    if pct < -1:
        return "MISS"
    return "IN LINE"


def _fmt_currency(val: float) -> str:
    if not math.isfinite(val):
        return "N/A"
    abs_val = abs(val)
    sign = "-" if val < 0 else ""
    if abs_val >= 1e12:
        return f"{sign}${abs_val / 1e12:.1f}T"
    if abs_val >= 1e9:
        return f"{sign}${abs_val / 1e9:.1f}B"
    if abs_val >= 1e6:
        return f"{sign}${abs_val / 1e6:.1f}M"
    if abs_val >= 1e3:
        return f"{sign}${abs_val / 1e3:.1f}K"
    return f"{sign}${abs_val:.2f}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_eps_surprise(ticker: str):
    ticker = ticker.upper()
    cache_key = f"eps-surprise:{ticker}"
    cached = _cache_get(cache_key)
    if _cache_hit(cached):
        return _from_cache(cached)

    try:
        # limit=3: the first entry may be a future quarter with null actuals;
        # find the most recent quarter where both values are reported.
        data = _fmp_get("/earnings", symbol=ticker, limit=3)
        if not data or not isinstance(data, list):
            _cache_set(cache_key, _SENTINEL)
            return None

        most_recent = next(
            (item for item in data
             if item.get("epsActual") is not None
             and item.get("epsEstimated") is not None),
            None,
        )
        if most_recent is None:
            _cache_set(cache_key, _SENTINEL)
            return None

        actual = float(most_recent["epsActual"])
        estimate = float(most_recent["epsEstimated"])
        if estimate == 0.0:
            _cache_set(cache_key, _SENTINEL)
            return None

        surprise_pct = (actual - estimate) / abs(estimate) * 100
        result = {
            "actual": f"${actual:.2f}",
            "estimate": f"${estimate:.2f}",
            "surprise_pct": round(surprise_pct, 2),
            "status": _surprise_status(surprise_pct),
        }
        _cache_set(cache_key, result)
    except Exception:
        return None  # transient errors not cached

    return result
