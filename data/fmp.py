import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

import yfinance as yf

CACHE_TTL = 86400  # 24 hours
DB_PATH = Path(__file__).parent.parent / "db" / "cache.db"

# 10-year US Treasury yield (%). Update monthly.
TREASURY_YIELD_10Y = 4.25

# ---------------------------------------------------------------------------
# yfinance → FMP-compatible field name mappings
# Lists are tried in order; first matching row label wins.
# ---------------------------------------------------------------------------

_INCOME_FIELDS = {
    "netIncome": ["Net Income"],
}

_BALANCE_FIELDS = {
    "totalStockholdersEquity": [
        "Stockholders Equity",
        "Total Equity Gross Minority Interest",
    ],
    "goodwillAndIntangibleAssets": [
        "Goodwill And Other Intangible Assets",
        "Goodwill",
    ],
}

_CASHFLOW_FIELDS = {
    "dividendsPaid": [
        "Cash Dividends Paid",
        "Common Stock Dividend Paid",
    ],
}

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fmp_cache (
            key      TEXT PRIMARY KEY,
            data     TEXT NOT NULL,
            fetched  REAL NOT NULL
        )
    """)
    conn.commit()
    return conn


def _cache_get(key: str):
    with _db() as conn:
        row = conn.execute(
            "SELECT data, fetched FROM fmp_cache WHERE key = ?", (key,)
        ).fetchone()
    if row and (time.time() - row[1]) < CACHE_TTL:
        return json.loads(row[0])
    return None


def _cache_set(key: str, data) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO fmp_cache (key, data, fetched) VALUES (?, ?, ?)",
            (key, json.dumps(data), time.time()),
        )


# ---------------------------------------------------------------------------
# yfinance helpers
# ---------------------------------------------------------------------------

def _to_float(val) -> Optional[float]:
    try:
        f = float(val)
        return None if f != f else f  # filter NaN
    except (TypeError, ValueError):
        return None


def _df_to_records(df, field_map: dict, limit: int) -> list:
    """Convert a yfinance DataFrame (rows=metrics, cols=dates) to a list of
    FMP-compatible dicts, most-recent first."""
    if df is None or df.empty:
        return []
    records = []
    for col in list(df.columns)[:limit]:
        date_str = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)
        record = {"date": date_str}
        for fmp_key, labels in field_map.items():
            record[fmp_key] = None
            for label in labels:
                if label in df.index:
                    record[fmp_key] = _to_float(df.loc[label, col])
                    break
        records.append(record)
    return records


def _calculate_beta(ticker: str) -> Optional[float]:
    """Compute beta from 36 months of monthly returns vs SPY."""
    try:
        data = yf.download(
            [ticker, "SPY"], period="3y", interval="1mo",
            auto_adjust=True, progress=False, actions=False,
        )
        prices = data["Close"][[ticker, "SPY"]].dropna()
        if len(prices) < 12:
            return None
        returns = prices.pct_change().dropna()
        cov = returns.cov()
        return float(cov.loc[ticker, "SPY"] / cov.loc["SPY", "SPY"])
    except Exception:
        return None


def _require_list(data, ticker: str, label: str) -> list:
    if not isinstance(data, list) or len(data) == 0:
        raise ValueError(
            f"No {label} data returned for {ticker}. "
            "Check the ticker symbol or try again later."
        )
    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_income_statement(ticker: str, period: str = "annual", limit: int = 5) -> list:
    ticker = ticker.upper()
    cache_key = f"income-statement:{ticker}:{json.dumps({'limit': limit, 'period': period}, sort_keys=True)}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    t = yf.Ticker(ticker)
    df = t.income_stmt if period == "annual" else t.quarterly_income_stmt
    records = _df_to_records(df, _INCOME_FIELDS, limit)
    _require_list(records, ticker, "income statement")
    _cache_set(cache_key, records)
    return records


def get_balance_sheet(ticker: str, period: str = "annual", limit: int = 5) -> list:
    ticker = ticker.upper()
    cache_key = f"balance-sheet-statement:{ticker}:{json.dumps({'limit': limit, 'period': period}, sort_keys=True)}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    t = yf.Ticker(ticker)
    df = t.balance_sheet if period == "annual" else t.quarterly_balance_sheet
    records = _df_to_records(df, _BALANCE_FIELDS, limit)
    _require_list(records, ticker, "balance sheet")
    _cache_set(cache_key, records)
    return records


def get_cash_flow(ticker: str, period: str = "annual", limit: int = 5) -> list:
    ticker = ticker.upper()
    cache_key = f"cash-flow-statement:{ticker}:{json.dumps({'limit': limit, 'period': period}, sort_keys=True)}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    t = yf.Ticker(ticker)
    df = t.cashflow if period == "annual" else t.quarterly_cashflow
    records = _df_to_records(df, _CASHFLOW_FIELDS, limit)
    _require_list(records, ticker, "cash flow statement")
    _cache_set(cache_key, records)
    return records


def get_company_profile(ticker: str) -> dict:
    ticker = ticker.upper()
    cache_key = f"profile:{ticker}:{{}}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    info = yf.Ticker(ticker).info
    if not info or info.get("quoteType") is None:
        raise ValueError(
            f"No profile data returned for {ticker}. Check the ticker symbol."
        )

    price = _to_float(info.get("currentPrice")) or _to_float(info.get("regularMarketPrice"))

    beta = _to_float(info.get("beta"))
    if beta is None or beta < 0.1 or beta > 3.0:
        beta = _calculate_beta(ticker)

    profile = {
        "symbol": ticker,
        "sector": info.get("sector") or "",
        "beta": beta,
        "price": price,
        "mktCap": _to_float(info.get("marketCap")),
        "bookValuePerShare": _to_float(info.get("bookValue")),
    }

    _cache_set(cache_key, profile)
    return profile


def get_sector(ticker: str) -> str:
    profile = get_company_profile(ticker)
    sector = profile.get("sector", "").strip()
    if not sector:
        raise ValueError(f"Sector not available in yfinance data for {ticker}.")
    return sector


def get_treasury_yield() -> float:
    # 10-year US Treasury yield (%). Update monthly.
    return TREASURY_YIELD_10Y


def get_all_financials(ticker: str, period: str = "annual", limit: int = 5) -> dict:
    profile = get_company_profile(ticker)
    sector = profile.get("sector", "").strip()
    if not sector:
        raise ValueError(f"Sector not available in yfinance data for {ticker}.")

    return {
        "profile": profile,
        "sector": sector,
        "income_statement": get_income_statement(ticker, period, limit),
        "balance_sheet": get_balance_sheet(ticker, period, limit),
        "cash_flow": get_cash_flow(ticker, period, limit),
    }
