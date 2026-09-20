# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the app
streamlit run app.py

# Test individual tabs standalone
streamlit run tabs/earnings_tab.py
streamlit run tabs/ev_revenue_tab.py

# Run EV/Revenue model directly (prints results to stdout)
python models/ev_revenue.py

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

`app.py` uses `st.sidebar.radio` to switch between four pages: **📈 RIM Valuation**, **EV/Revenue Analysis**, **📊 Earnings Analysis**, and **📚 Learning**. All four `st.session_state` keys (`portfolio_results`, `portfolio_errors`, `portfolio_analysis`, `ev_revenue_results`) are initialized at module level, before any page branch, so they're always present. Tab modules are dynamically imported inside their `elif` block — never at the top of `app.py`.

### Data layers

| Module | Source | Used by |
|--------|--------|---------|
| `data/fmp.py` | **yfinance** (despite the name) | `models/rim.py` only |
| `data/earnings.py` | FMP stable API (`/earnings`) | `tabs/earnings_tab.py` |
| `data/financials.py` | FMP stable API (`/income-statement`, `/earnings`) | `tabs/earnings_tab.py` |
| `models/ev_revenue.py` | **yfinance** (market cap, quarterly revenue); FMP balance sheet (`/balance-sheet-statement`) for total debt and cash | `tabs/ev_revenue_tab.py` |

`data/fmp.py` owns the SQLite cache infrastructure (`_db`, `_cache_get`, `_cache_set`) used by all modules. `tabs/learning_tab.py` is pure UI — no data dependencies.

### RIM model

`models/rim.py` → `run_rim(ticker)` pulls 5 years of annual financials from `data.fmp`, computes CAPM cost of equity, projects residual income for 5 years + terminal value, and returns a signal dict with `intrinsic_value`, `margin_of_safety_pct`, and `signal` (BUY ≥ 15% MoS, SELL ≤ −15% MoS, HOLD in between). Thresholds live in `MOS_BUY_THRESHOLD = 15.0` and `MOS_SELL_THRESHOLD = -15.0`.

Margin of safety uses **intrinsic value as the denominator** — `(iv − price) / iv` — in both this model and `models/dcf.py`, guarded to return `0.0` when `iv == 0`. Note this is not the price-denominated convention; a figure here is not comparable to one computed against price.

`data/fmp.py` fetches the 10-year treasury yield dynamically via `get_treasury_yield()`, which returns `tuple[float, str]` — the rate plus a source label that distinguishes a live FRED (DGS10) fetch from the fallback. On error it returns `_TREASURY_FALLBACK = 4.5` labelled as a fallback; update that constant when the rate shifts materially. The label exists because the bare float is identical either way, so comparing the value against `_TREASURY_FALLBACK` would misread a genuine 4.5% print as a failed fetch. Failures are never cached, so a cache hit is always a prior successful live fetch.

### EV/Revenue model

`models/ev_revenue.py` → `run_ev_revenue(ticker)` computes EV (Market Cap + Total Debt − Cash) and TTM Revenue (sum of 4 most recent quarterly periods from yfinance) for tickers in `EV_REVENUE_TICKERS` (`XMTR`, `ONDS`, `ARWR`). Signal thresholds compare EV/Rev ratio against per-ticker `_SECTOR_MEDIANS` (hardcoded — update when sector comparables shift):

| Signal | Ratio vs. Median |
|--------|-----------------|
| UNDERVALUED | < 0.80× |
| FAIR | 0.80–1.25× |
| OVERVALUED | > 1.25× |

Negative EV (cash > mkt_cap + debt) returns `signal = "N/A — Negative EV"` with `ev_revenue_ratio = None`.

### DCF model

`models/dcf.py` → `run_dcf_valuation(ticker)` projects tapered free cash flow, discounts at WACC, and returns `intrinsic_value_per_share`, `margin_of_safety_pct`, and a WACC × terminal-growth `sensitivity_grid`. Statements come from the **real FMP REST API** (via `data/financials.py`'s `_get`), not the yfinance-backed `data/fmp.py` — yfinance caps annual statements at 4 real fiscal years, short of `REQUIRED_HISTORY_YEARS = 5`.

`DCF_TICKERS` is the 10-ticker large-cap set the tab offers (`AAPL`, `COST`, `GE`, `GOOGL`, `LMT`, `META`, `MSFT`, `NVDA`, `V`, `WMT`). It is **not** pre-filtered — the tab checks `gate_status` per ticker and excludes failures. `DCF_TICKERS_PENDING_PAID_TIER` lists tickers whose FMP statement endpoints return HTTP 402 on the free tier (a paywall, not a data-quality failure).

**No BUY/HOLD/SELL logic.** Unlike the other two models, `dcf.py` emits only a `margin_of_safety_pct` and an undervalued/overvalued label. That label is derived from `intrinsic_value_per_share > current_price` directly, *not* from the sign of the MoS figure — with intrinsic value in the denominator, a negative intrinsic value flips the ratio's sign and would otherwise read as "undervalued".

Imports `BETA_FLOOR`, `EQUITY_RISK_PREMIUM`, and `MIN_COST_OF_EQUITY` from `models/rim.py`, aliasing the premium as `MARKET_PREMIUM = EQUITY_RISK_PREMIUM` so both models share one equity risk premium. This makes `rim.py` a dependency of `dcf.py`: `rim.py` must never import from `dcf.py` (circular).

Has its own `CACHE_TTL` and its own `_cache_get`, importing only `_cache_set` and `_db` from `data.fmp`. It also has its own FRED fetcher, `_get_risk_free_rate()`, under cache key `dcf-treasury-yield:DGS10` — separate from `data/fmp.py`'s `treasury-yield:DGS10`, and caching `[value, label]` where `data/fmp.py` caches a bare float. The two fetchers are still duplicated; only the fallback constant is shared (`RF_FALLBACK = _TREASURY_FALLBACK`).

### Cache system

SQLite at `db/cache.db`, 24-hour TTL. The sentinel pattern distinguishes cache miss from stable "no data":

```python
_SENTINEL = False  # JSON false — serializable, distinct from None

# Miss:      _cache_get() returns None  → fetch from API
# No data:   _cache_get() returns False → return None to caller, skip API
# Hit:       _cache_get() returns dict  → return to caller
```

`_cache_hit(cached)` = `cached is not None`. Each module defines its own `_cache_get` that uses the module's local `CACHE_TTL`; importing `_cache_get` from `data.fmp` would silently use `data.fmp`'s TTL instead. `models/ev_revenue.py` imports `_cache_set` and `_db` from `data.fmp` but defines its own `_cache_get`.

Transient errors (network, rate-limit) are **never** cached — the `except` block returns without calling `_cache_set`. Only stable "no data" responses write `_SENTINEL`.

### FMP API

Uses the stable endpoint: `https://financialmodelingprep.com/stable`. The free tier caps income statements at `limit=5`. Symbol is a query param (`symbol=TICKER`), not a path segment.

`data/financials.py` uses `_get_income_statements(ticker)` as a shared fetcher (cache key `fmp-income-stmt:{ticker}`) so `get_key_stats` and `get_chart_data` hit the API only once per TTL window. However, `get_eps_surprise` (`data/earnings.py`) and `get_revenue_surprise` (`data/financials.py`) each hit `/earnings` independently under separate cache keys.

## Code Review Priorities

- **Cache sentinel pattern correctness** — `None` = cache miss (retry), `False` = stable "no data" (skip API), `dict` = valid hit. Transient errors must never write `_SENTINEL`.
- **Lazy imports in `app.py` elif branches only** — tab modules (`render_*`) are imported inside their `elif` block, never at the top of `app.py`. An eager top-level import crashes all pages if that module fails.
- **Signal logic correctness in `models/`** — RIM thresholds: BUY ≥ 15% MoS, SELL ≤ −15% MoS. EV/Revenue thresholds: UNDERVALUED < 0.80× median, OVERVALUED > 1.25×. Check boundary conditions (`>` vs `>=`) and that edge cases (negative EV, zero revenue) return a defined signal rather than a computed nonsense value.
- **New features go in new files only** — do not modify `data/fmp.py`, `models/rim.py`, `tabs/earnings_tab.py`, or other existing files when adding features unless explicitly instructed.

## Known dead code

`tabs/earnings_tab.py` contains `_quarter_caption()` and `_QUARTER_END` — both are unused (no call sites after the "Most Recent Quarter" caption was removed).
