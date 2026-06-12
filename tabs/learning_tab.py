import streamlit as st


def render_learning_tab() -> None:
    st.title("Learning")

    tab_rim, tab_evr, tab_earn = st.tabs(
        ["Residual Income Model (RIM)", "EV / Revenue (EV/R)", "Earnings Analysis"]
    )

    # ------------------------------------------------------------------
    # Tab 1 — Residual Income Model (RIM)
    # ------------------------------------------------------------------
    with tab_rim:
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
"""
        )

    # ------------------------------------------------------------------
    # Tab 2 — EV/Revenue Multiple (EV/R)
    # ------------------------------------------------------------------
    with tab_evr:
        st.header("What is the EV/Revenue Multiple?")
        st.markdown(
            """
A valuation metric that compares a company's total value (enterprise value) to its annual
revenue. Used for companies with minimal or no profits.

---

### Formula

$$
\\text{EV/R} = \\frac{\\text{Enterprise Value}}{\\text{Revenue}}
$$

$$
\\text{Enterprise Value} = \\text{Market Cap} + \\text{Debt} - \\text{Cash}
$$

---

### Why It's Useful

Unlike EV/EBITDA, EV/R works on pre-profit companies because it only looks at the top line
— no earnings required. EV/EBITDA measures operating cash flow generation; EV/R measures
revenue generation. For early-stage companies like XMTR, ONDS, and ARWR, EBITDA is
negative or meaningless, so EV/R is the right tool.

---

### Limitation

It ignores operating expenses entirely. A company with $1B revenue and $2B in costs looks
the same as one that's profitable. Always pair with other metrics.

---

### Best Used

Within the same industry — sector medians vary widely. A 9× EV/R is normal for software;
it would be extreme for retail.

---

### Further Reading

[EV/Revenue Multiple — Investopedia](https://www.investopedia.com/terms/e/ev-revenue-multiple.asp)
"""
        )

    # ------------------------------------------------------------------
    # Tab 3 — Earnings Analysis
    # ------------------------------------------------------------------
    with tab_earn:
        st.header("How to Read an Earnings Report")
        st.markdown(
            """
A quarterly earnings report (10-Q) details revenues, expenses, and profits for the quarter.
Main components: **income statement**, **balance sheet**, **cash flow statement**,
**management discussion & analysis (MD&A)**, and **risk disclosures**.

---

### Key Metrics to Watch

For each quarter ask: how did it compare to the prior quarter? The same quarter last year?
Is cost of sales rising faster than revenue?

- **Revenue** — top-line growth; the raw measure of business scale
- **Net Income** — bottom-line profit after all expenses and taxes
- **EPS (Earnings Per Share)** — net income divided by diluted share count; the most-cited headline number
- **EBIT** — earnings before interest and taxes; strips out capital structure to show operating performance

---

### Beat / Miss

Wall Street analysts publish EPS and revenue estimates before earnings. When a company
reports above those estimates it's a **"beat"** — stock typically rises. Below is a
**"miss"** — stock typically falls. The **surprise %** is how far above or below the
estimate the actual result landed.

---

### The Cash Flow Reality Check

A company can show positive net income but negative cash flow. Net income is an accounting
figure subject to accruals and non-cash items; cash flow is harder to manipulate. Always
check the cash flow statement — if operating cash flow is consistently negative while net
income looks fine, that's a warning sign.

---

### Risk Flags in the Filing

- **Item 1A — Risk Factors**: material risks the company is required to disclose; watch for
  new or expanded liquidity warnings
- **Item I — Legal Proceedings**: major litigation, regulatory actions, or settlements that
  could affect the business

---

### Further Reading

[How to Decode an Earnings Report — Investopedia](https://www.investopedia.com/articles/fundamental-analysis/10/decoding-earnings-reports.asp)
"""
        )


if __name__ == "__main__":
    render_learning_tab()
