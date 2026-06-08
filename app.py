import pandas as pd
import streamlit as st

from models.rim import run_rim

PORTFOLIO_TICKERS = ["JPM", "BRK-B", "LMT", "WMB"]

st.set_page_config(page_title="Residual Income Model (RIM)", layout="wide")

if "portfolio_results" not in st.session_state:
    st.session_state.portfolio_results = []
if "portfolio_errors" not in st.session_state:
    st.session_state.portfolio_errors = []

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIGNAL_COLORS = {"BUY": "#28a745", "HOLD": "#e6a817", "SELL": "#dc3545"}


def _style_signal(val: str) -> str:
    color = _SIGNAL_COLORS.get(val, "")
    return f"background-color:{color};color:white;font-weight:700" if color else ""


def _style_mos(val: float) -> str:
    if val >= 15:
        return "color:#28a745;font-weight:700"
    if val <= -15:
        return "color:#dc3545;font-weight:700"
    return "color:#e6a817;font-weight:700"


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("Residual Income Model (RIM)")

tab_portfolio, tab_about = st.tabs(["Portfolio", "About RIM"])

# ---------------------------------------------------------------------------
# Tab 1: Portfolio
# ---------------------------------------------------------------------------

with tab_portfolio:
    st.caption("Tickers: " + "  ·  ".join(PORTFOLIO_TICKERS))

    if st.button("Run Portfolio Analysis"):
        results, errors = [], []
        bar = st.progress(0, text="Starting...")

        for i, t in enumerate(PORTFOLIO_TICKERS):
            bar.progress(i / len(PORTFOLIO_TICKERS), text=f"Analyzing {t}...")
            try:
                results.append(run_rim(t))
            except Exception as exc:
                errors.append(f"**{t}**: {exc}")

        bar.progress(1.0, text="Complete.")
        bar.empty()

        st.session_state.portfolio_results = results
        st.session_state.portfolio_errors = errors

    if st.session_state.portfolio_results:
        ranked = sorted(
            st.session_state.portfolio_results,
            key=lambda x: x["margin_of_safety_pct"],
            reverse=True,
        )

        df = pd.DataFrame([
            {
                "Ticker": r["ticker"],
                "Sector": r["sector"],
                "Signal": r["signal"],
                "RIM Value": round(r["intrinsic_value"], 2),
                "Current Price": round(r["current_price"], 2),
                "MoS %": round(r["margin_of_safety_pct"], 1),
                "Cost of Eq %": round(r["cost_of_equity"], 2),
                "ROE %": round(r["avg_roe"], 2),
                "Beta": round(r["beta"], 2),
            }
            for r in ranked
        ])

        styled = (
            df.style
            .map(_style_signal, subset=["Signal"])
            .map(_style_mos, subset=["MoS %"])
            .format({
                "RIM Value":     "${:.2f}",
                "Current Price": "${:.2f}",
                "MoS %":         "{:.1f}%",
                "Cost of Eq %":  "{:.2f}%",
                "ROE %":         "{:.2f}%",
            })
        )

        st.dataframe(styled, use_container_width=True, hide_index=True)

    if st.session_state.portfolio_errors:
        with st.expander(f"{len(st.session_state.portfolio_errors)} ticker(s) failed to load"):
            for msg in st.session_state.portfolio_errors:
                st.markdown(msg)

# ---------------------------------------------------------------------------
# Tab 2: About RIM
# ---------------------------------------------------------------------------

with tab_about:
    st.header("What is the Residual Income Model?")
    st.markdown(
        """
A stock is worth more than its book value only if it earns *more* than what investors
require. The Residual Income Model (RIM) makes that idea precise: it values a company as
its current book value per share *plus* the present value of all the "excess" returns it
is expected to generate above its cost of equity.

If a company earns exactly its cost of equity every year, it is worth exactly book value —
no more, no less. If it consistently earns above that hurdle, the surplus compounds and the
intrinsic value climbs above book. If it earns below the hurdle, the stock is worth *less*
than book.

---

### The Formula

$$
\\text{RIM Value} = \\text{Book Value}_0 + \\sum_{t=1}^{T} \\frac{\\text{BV}_{t-1} \\times (\\text{ROE} - K_e)}{(1 + K_e)^t} + \\frac{\\text{Terminal Value}}{(1 + K_e)^T}
$$

In plain English:

- **Book Value₀** — what shareholders have invested in the business today (equity per share)
- **ROE − Kₑ** — the "excess return": how much the company earns above what equity investors require
- **Kₑ (cost of equity)** — computed via CAPM: risk-free rate + beta × equity risk premium
- **Terminal Value** — a perpetuity that captures excess returns beyond the 5-year forecast window
- **Margin of Safety** — how far the current price is below (or above) the RIM Value

---

### Signal Thresholds

| Signal | Margin of Safety | Meaning |
|--------|-----------------|---------|
| 🟢 **BUY** | > 15% | Price is meaningfully below RIM Value |
| 🟡 **HOLD** | 0 – 15% | Fairly valued; limited margin of safety |
| 🔴 **SELL** | < 0% | Price exceeds RIM Value |

---

### When RIM Works — and When It Doesn't

**Works well on:**
- Banks, insurers, and financials (book value is economically meaningful)
- Mature industrials and consumer staples with stable, predictable ROE
- Capital-intensive businesses where equity on the balance sheet reflects real earning power

**Less reliable on:**
- Early-stage or high-growth companies where current book value understates future earnings power
- Asset-light businesses (software, platforms) where intangibles dominate value
- Companies with highly volatile or negative earnings — ROE projections become unreliable
- Firms that have aggressively bought back stock (book value can be near zero or negative)

---

### Further Reading

[Residual Income — Investopedia](https://www.investopedia.com/terms/r/residualincome.asp)

---

*This tool is a directional valuation screen, not a price target or investment advice.*
*Treat signals as a starting point for deeper research.*
"""
    )
