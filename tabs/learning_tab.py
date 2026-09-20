import streamlit as st


def render_learning_tab() -> None:
    st.title("Learning")
    st.caption("This page's content is AI-generated.")

    tab_rim, tab_evr, tab_dcf, tab_earn = st.tabs(
        [
            "Residual Income Model (RIM)",
            "EV / Revenue (EV/R)",
            "Discounted Cash Flow (DCF)",
            "Earnings Analysis",
        ]
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

It ignores operating expenses entirely. A company with \\$1B revenue and \\$2B in costs looks
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
    # Tab 3 — Discounted Cash Flow (DCF)
    # ------------------------------------------------------------------
    with tab_dcf:
        st.header("What is the Discounted Cash Flow Model?")
        st.markdown(
            """
A DCF values a company by projecting the free cash flow it will generate, discounting each
year back to today, and adding a terminal value for everything beyond the forecast window.

This section describes the DCF **as actually implemented in this app**, including its specific
constants, bounds, and fallbacks. It is not the general textbook form.

---

### Step 0 — Inputs and the data-quality gate

Five years of annual cash-flow, income, and balance-sheet statements are pulled from the FMP
REST API. Free cash flow for each year is:

$$
\\text{FCF} = \\text{Operating Cash Flow} - \\left| \\text{Capital Expenditures} \\right|
$$

CapEx is reported as a negative outflow, so its *magnitude* is subtracted. Subtracting it
as-is would double-count it.

Before any valuation math runs, a gate rejects the ticker outright if:

- any of the three statement fetches failed, since otherwise WACC and net debt would quietly
  compute from empty statements and report a confident debt-free, tax-free valuation
- fewer than **5** years of FCF history are available
- any year is missing operating cash flow or capital expenditures
- any year's FCF is **non-positive** (≤ 0, not merely negative, because a zero base year
  compounds to an all-zero projection and leaves the growth CAGR undefined)

A ticker that fails the gate is not valued at all; the projection never runs.

---

### Step 1 — Where the starting growth rate comes from

The projection starts from the **smoothed 3-year FCF CAGR**, the compound rate between the
oldest and newest FCF in the trailing three-year window:

$$
g_{\\text{start}} = \\left( \\frac{\\text{FCF}_{\\text{newest}}}{\\text{FCF}_{\\text{2 yrs prior}}} \\right)^{1/2} - 1
$$

Only the two endpoints matter, so a collapse-then-rebound year sitting between them washes
out instead of dominating the figure the way an average of raw year-over-year percentages
would.

---

### Step 2 — How that growth rate is bounded

The starting rate is clamped before it drives anything downstream:

| Bound | Value | Behavior |
|-------|-------|----------|
| Ceiling | **40%** | Anything higher is capped to 40% and flagged for manual review |
| Floor | **0%** | Anything negative is raised to 0% and flagged for manual review |

The reasoning: an implausibly high smoothed rate is far more likely a base-year artifact than
a sustainable five-year trend, and a negative rate breaks the model's central assumption.
A single taper toward a *positive* terminal rate simply does not describe a shrinking business.

---

### Step 3 — The 5-year tapered projection

Growth is evenly stepped from the starting rate in year 1 to the terminal rate in year 5,
inclusive at both ends. The final projected year is therefore already growing at the terminal
rate, exactly as the perpetuity formula in the next step assumes.

Each year compounds off the **prior** year's cash flow, with year 1 growing off the last
actual historical FCF:

$$
\\text{FCF}_t = \\text{FCF}_{t-1} \\times (1 + g_t)
$$

---

### Step 4 — Terminal value

Everything past year 5 collapses into a Gordon-growth perpetuity on the final projected year:

$$
\\text{TV} = \\frac{\\text{FCF}_5 \\times (1 + g_{\\text{terminal}})}{\\text{WACC} - g_{\\text{terminal}}}
$$

Terminal growth is a **fixed 3.5%**, a long-run GDP-growth proxy, not something derived per
company. If WACC does not exceed 3.5%, the denominator is non-positive and the model returns
an explicit error instead of a number.

---

### Step 5 — How the discount rate is constructed

Cash flows are discounted at the weighted average cost of capital, assembled component by
component rather than taken from any single data source:

$$
\\text{WACC} = w_e \\times K_e + w_d \\times K_d \\times (1 - \\text{tax rate})
$$

| Component | How it is built | Source |
|-----------|-----------------|--------|
| Risk-free rate | 10-year Treasury yield; falls back to **4.5%** if the fetch fails, and the fallback is labeled as such so it is never mistaken for a live rate | FRED (DGS10), fetched directly |
| Beta | Floored at **0.5**; if the reported beta is missing or outside 0.1–3.0 it is recomputed from 36 months of monthly returns against SPY, and the ticker is rejected if that also fails | yfinance |
| Equity risk premium | Fixed **5.5%**, the same constant the RIM model uses, so the two models cannot silently diverge | hardcoded |
| Cost of equity (Kₑ) | CAPM: risk-free + beta × 5.5%, floored at **6%** | computed |
| Cost of debt | Interest expense ÷ total debt, clamped to 0–100%; zero when total debt is zero | FMP income + balance sheet |
| Tax rate | Income tax expense ÷ pre-tax income, clamped to 0–100%; zero when pre-tax income is non-positive | FMP income statement |
| Weights | wₑ = market cap ÷ (market cap + total debt), with the debt weight as the remainder | yfinance (market cap) + FMP (debt) |

---

### Step 6 — From enterprise value to value per share

Each projected year and the terminal value are discounted at WACC, the terminal value being
received at the end of year 5:

$$
\\text{EV} = \\sum_{t=1}^{5} \\frac{\\text{FCF}_t}{(1 + \\text{WACC})^t} + \\frac{\\text{TV}}{(1 + \\text{WACC})^5}
$$

Net debt is then subtracted to reach equity value, which is divided by the share count:

$$
\\text{Value per Share} = \\frac{\\text{EV} - \\text{Net Debt}}{\\text{Shares Outstanding}}
$$

Net debt comes from the balance sheet's reported figure, falling back to total debt minus cash
and equivalents when that figure is missing or non-numeric. Shares outstanding come from a
separate market-data lookup; when it is unavailable there is no per-share number at all and
the projection is reported as failed rather than guessed at.

Margin of safety is computed last and reported in its own section. It is the difference
between the value per share and the current price, divided by that price, so every assumption
can be judged before the market price appears anywhere.

---

### Sensitivity grid

Because the output is dominated by two assumptions, a 5×5 grid re-runs the entire pipeline
across a range of WACC and terminal-growth values around the computed base case. WACC varies
down the rows, terminal growth across the columns. The starting growth rate is held fixed in
every cell, so only those two axes move.

The two axes use **different increments**:

| Axis | Offsets from the base case |
|------|----------------------------|
| WACC (rows) | −3.0, −1.5, base, +1.5, +3.0 |
| Terminal growth (columns) | −2.0, −1.0, base, +1.0, +2.0 |

Every offset above is in **percentage points, not a relative change**. A row labeled +1.5 is a
WACC 1.5 percentage points above the base, not one 1.5% higher in relative terms.

---

### Interactive sliders

The DCF tab carries three sliders. They recompute intrinsic value live and never alter the
auto-computed base case, so the model's own answer stays on screen next to whatever you dial in.

| Slider | Range |
|--------|-------|
| Starting growth | The computed base ± 10 percentage points, then clipped to the same **0% floor** and **40% ceiling** the model itself applies |
| WACC | The computed base ± 5 percentage points |
| Terminal growth | A fixed **1.0% to 6.0%** range, independent of the base |

The starting-growth slider is the only one clipped by the model's own bounds, which is why a
low-growth company shows a range that starts at 0%. When the computed base sits below 10%, the
bottom of the 10-point window falls below zero and the floor lifts it back to 0%. The same
happens at the top for a base above 30%, where the 40% ceiling clips the upper end.

---

### Further reading

[Discounted Cash Flow — Investopedia](https://www.investopedia.com/terms/d/dcf.asp) describes
the general two-stage DCF form. This implementation deliberately departs from it in the
specific ways documented above: a fixed 3.5% terminal growth rate, an evenly stepped taper
between the starting and terminal rates, and hard 0% / 40% bounds on the starting growth rate.
"""
        )

    # ------------------------------------------------------------------
    # Tab 4 — Earnings Analysis
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
