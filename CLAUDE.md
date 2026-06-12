# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the app
streamlit run app.py

# Test the earnings tab standalone
streamlit run tabs/earnings_tab.py

# Clear the SQLite cache (forces fresh API fetches)
rm db/cache.db
```

No test suite exists. No linting config is present.

## Secrets

Create `.streamlit/secrets.toml` (gitignored) with:

```toml
FMP_KEY = "your_fmp_api_key"
```

All FMP API calls read `st.secrets["FMP_KEY"]`. Note: `config.py` reads `FMP_API_KEY` from a `.env` file but is vestigial — nothing in the app imports it.

## Architecture

### Navigation

`app.py` uses `st.sidebar.radio` to switch between three pages: **RIM Valuation**, **Earnings Analysis**, and **About RIM**. All three `st.session_state` keys (`portfolio_results`, `portfolio_errors`, `portfolio_analysis`) are initialized at module level, before any page branch, so they're always present. The Earnings page dynamically imports `tabs/earnings_tab.py`.

### Data layers

There are two distinct data sources wired through three modules:

| Module | Source | Used by |
|--------|--------|---------|
| `data/fmp.py` | **yfinance** (despite the name) | `models/rim.py` only |
| `data/earnings.py` | FMP stable API (`/earnings`) | `tabs/earnings_tab.py` |
| `data/financials.py` | FMP stable API (`/income-statement`, `/earnings`) | `tabs/earnings_tab.py` |
| `models/ev_revenue.py` | **yfinance** (market cap, quarterly revenue); FMP balance sheet (`/balance-sheet-statement`) for total debt and cash | `tabs/ev_revenue_tab.py` |

`data/fmp.py` also owns the SQLite cache infrastructure (`_db`, `_cache_get`, `_cache_set`) used by all three modules.

### RIM model

`models/rim.py` → `run_rim(ticker)` pulls 5 years of annual financials from `data.fmp`, computes CAPM cost of equity (`TREASURY_YIELD_10Y` is hardcoded in `data/fmp.py` — update monthly), projects residual income for 5 years + terminal value, and returns a signal dict with `intrinsic_value`, `margin_of_safety_pct`, and `signal` (BUY ≥ 15% MoS, SELL < 0%, HOLD otherwise).

### Cache system

SQLite at `db/cache.db`, 24-hour TTL. The sentinel pattern distinguishes cache miss from stable "no data":

```python
_SENTINEL = False  # JSON false — serializable, distinct from None

# Miss:      _cache_get() returns None  → fetch from API
# No data:   _cache_get() returns False → return None to caller, skip API
# Hit:       _cache_get() returns dict  → return to caller
```

`_cache_hit(cached)` = `cached is not None`. Each module defines its own `_cache_get` that uses the module's local `CACHE_TTL`; importing `_cache_get` from `data.fmp` would silently use `data.fmp`'s TTL instead.

Transient errors (network, rate-limit) are **never** cached — the `except` block returns without calling `_cache_set`. Only stable "no data" responses write `_SENTINEL`.

### FMP API

Uses the stable endpoint: `https://financialmodelingprep.com/stable`. The free tier caps income statements at `limit=5`. Symbol is a query param (`symbol=TICKER`), not a path segment.

`data/financials.py` uses `_get_income_statements(ticker)` as a shared fetcher (cache key `fmp-income-stmt:{ticker}`) so `get_key_stats` and `get_chart_data` hit the API only once per TTL window. However, `get_eps_surprise` (`data/earnings.py`) and `get_revenue_surprise` (`data/financials.py`) each hit `/earnings` independently under separate cache keys.

## Known dead code

`tabs/earnings_tab.py` contains `_quarter_caption()` and `_QUARTER_END` — both are unused (no call sites after the "Most Recent Quarter" caption was removed).
