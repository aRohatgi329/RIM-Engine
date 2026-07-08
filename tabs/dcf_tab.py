import pandas as pd
import streamlit as st

from models.dcf import (
    DCF_TICKERS,
    DCF_TICKERS_PENDING_PAID_TIER,
    STARTING_GROWTH_CEILING_PCT,
    STARTING_GROWTH_FLOOR_PCT,
    _margin_of_safety_pct,
    _project_from_growth,
    compute_dcf_inputs,
    compute_dcf_projection,
    compute_sensitivity_grid,
)

WACC_SLIDER_SPREAD_PCT = 5.0
START_GROWTH_SLIDER_SPREAD_PCT = 10.0
TERMINAL_GROWTH_SLIDER_MIN_PCT = 1.0
TERMINAL_GROWTH_SLIDER_MAX_PCT = 6.0


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def _render_fcf_history(inputs: dict) -> None:
    st.subheader("1. Historical Free Cash Flow")
    rows = [
        {
            "Fiscal Year": y["date"],
            "Operating Cash Flow": y["operating_cash_flow"],
            "CapEx": y["capital_expenditures"],
            "FCF": y["fcf"],
        }
        for y in reversed(inputs["fcf_years"])
    ]
    df = pd.DataFrame(rows)
    st.dataframe(
        df.style.format({
            "Operating Cash Flow": "${:,.0f}",
            "CapEx": "${:,.0f}",
            "FCF": "${:,.0f}",
        }),
        width="stretch",
        hide_index=True,
    )


def _render_gate(inputs: dict) -> bool:
    st.subheader("2. Data Quality Gate")
    if inputs["gate_status"] == "GATE_PASSED":
        st.success("PASSED")
    else:
        st.error(f"FAILED — {inputs['gate_reason']}")
    if inputs.get("alignment_warning"):
        st.warning(inputs["alignment_warning"])
    return inputs["gate_status"] == "GATE_PASSED"


def _render_wacc(inputs: dict) -> None:
    w = inputs["wacc"]
    st.subheader("3. WACC (base case)")
    cols = st.columns(4)
    cols[0].metric("WACC", f"{w['wacc_pct']:.2f}%")
    cols[1].metric("Cost of Equity", f"{w['cost_of_equity_pct']:.2f}%")
    cols[2].metric("After-Tax Cost of Debt", f"{w['after_tax_cost_of_debt_pct']:.2f}%")
    cols[3].metric("Beta", f"{w['beta']:.2f}")
    st.caption(
        f"Equity weight {w['equity_weight_pct']:.1f}% · Debt weight {w['debt_weight_pct']:.1f}%"
    )
    # $0 disclosed interest expense makes the modeled cost of debt compute to 0%,
    # which reads as a data gap unless explained. Route the disclosure on total_debt
    # so we assert the actual reason instead of an open either/or:
    #   - debt exists → the interest line just wasn't reported separately (large caps
    #     like AAPL from FY2024 fold it into 'other income/expense, net'); the
    #     debt-weight caveat is meaningful only in this branch.
    #   - no debt → a 0% cost of debt is correct by design, not a missing figure.
    if w["interest_expense"] == 0:
        if w["total_debt"] > 0:
            st.caption(
                "Cost of debt reflects $0 disclosed interest expense. The company doesn't "
                "report interest expense separately (it's often folded into 'other income/expense, "
                "net'), so the modeled cost of debt is 0%. WACC impact is limited by the "
                f"{w['debt_weight_pct']:.1f}% debt weight."
            )
        else:
            st.caption(
                "Company reports no outstanding debt, so cost of debt is 0% by design, not a data gap."
            )


def _render_projection_table(projection_result: dict) -> None:
    rows = [
        {"Year": p["year"], "Growth %": p["growth_pct"], "FCF": p["fcf"]}
        for p in projection_result["projected_fcf"]
    ]
    df = pd.DataFrame(rows)
    st.dataframe(
        df.style.format({"Growth %": "{:.2f}%", "FCF": "${:,.0f}"}),
        width="stretch",
        hide_index=True,
    )


def _render_valuation_summary(label: str, projection_result: dict) -> None:
    if projection_result["intrinsic_value_per_share"] is None:
        # The base-case dict (compute_dcf_projection) stores its failure text under
        # 'projection_reason'; the override dict (_project_from_growth) uses 'reason'.
        # Read both so this shared helper surfaces the specific reason for either caller
        # instead of the generic fallback.
        reason = (
            projection_result.get("projection_reason")
            or projection_result.get("reason")
            or "Projection failed."
        )
        st.warning(f"{label}: {reason}")
        return
    st.metric(
        f"{label} — Intrinsic Value/Share",
        f"${projection_result['intrinsic_value_per_share']:,.2f}",
    )
    st.caption(
        f"Terminal Value: \\${projection_result['terminal_value']:,.0f}  ·  "
        f"Enterprise Value: \\${projection_result['enterprise_value']:,.0f}"
    )


def _nearest_index(values: list, target: float) -> int:
    return min(range(len(values)), key=lambda i: abs(values[i] - target))


def _render_sensitivity_grid(grid: dict, current_wacc_pct: float, current_terminal_growth_pct: float) -> None:
    st.subheader("4. Sensitivity Grid — Intrinsic Value/Share (base case, static)")
    st.caption("WACC (rows) x Terminal Growth (columns). Not recomputed by the sliders below.")

    df = pd.DataFrame(
        [[c["intrinsic_value_per_share"] for c in row] for row in grid["rows"]],
        index=[f"{w:.2f}%" for w in grid["wacc_values"]],
        columns=[f"{t:.2f}%" for t in grid["terminal_growth_values"]],
    )

    nearest_row = _nearest_index(grid["wacc_values"], current_wacc_pct)
    nearest_col = _nearest_index(grid["terminal_growth_values"], current_terminal_growth_pct)
    base_row_col = next(
        (r, c)
        for r, row in enumerate(grid["rows"])
        for c, cell in enumerate(row)
        if cell["is_base_case"]
    )

    def _highlight(_):
        styles = pd.DataFrame("", index=df.index, columns=df.columns)
        styles.iloc[base_row_col[0], base_row_col[1]] = "border: 2px solid #e6a817"
        nearest_existing = styles.iloc[nearest_row, nearest_col]
        nearest_style = "background-color: #2f6feb; color: white; font-weight: 700"
        styles.iloc[nearest_row, nearest_col] = (
            f"{nearest_existing}; {nearest_style}" if nearest_existing else nearest_style
        )
        return styles

    styled = df.style.format("${:,.2f}", na_rep="N/A").apply(_highlight, axis=None)
    st.table(styled)

    grid_values = [
        c["intrinsic_value_per_share"]
        for row in grid["rows"]
        for c in row
        if c["intrinsic_value_per_share"] is not None
    ]
    if grid_values:
        st.caption(f"Grid range: \\${min(grid_values):,.2f} – \\${max(grid_values):,.2f}")
    st.caption(
        "Blue = grid point nearest your current WACC / terminal-growth sliders (below).  "
        "Gold border = base case (matches the auto-computed intrinsic value below)."
    )
    st.caption(
        "This grid holds the starting growth rate fixed at the base-case value and varies "
        "only WACC and terminal growth, since those two dominate terminal value. Moving the "
        "starting-growth slider changes the live valuation below, not this grid."
    )


# ---------------------------------------------------------------------------
# Shared compute + render (used by both the official portfolio list and
# Explore Mode, so neither duplicates the Phase 1-4 pipeline or its display)
# ---------------------------------------------------------------------------

def _compute_dcf_result(ticker: str) -> dict:
    try:
        inputs = compute_dcf_inputs(ticker)
        result = {"inputs": inputs, "error": None}
        if inputs["gate_status"] == "GATE_PASSED":
            result["projection"] = compute_dcf_projection(inputs)
            if result["projection"].get("start_growth_pct") is not None:
                result["grid"] = compute_sensitivity_grid(inputs, result["projection"])
        return result
    except Exception as exc:
        return {"error": str(exc)}


def _render_full_dcf_analysis(ticker: str, result: dict, key_ns: str) -> None:
    """Renders Phase 1-4 for one ticker's already-computed result dict. key_ns
    namespaces every slider/button key so the official portfolio list and
    Explore Mode never collide when the same ticker symbol appears in both."""
    if result.get("error"):
        st.error(f"Failed to compute DCF for {ticker}: {result['error']}")
        return

    st.header(f"DCF Valuation: {ticker}")

    inputs = result["inputs"]
    _render_fcf_history(inputs)
    gate_passed = _render_gate(inputs)

    if not gate_passed:
        st.info(f"{ticker} is excluded from DCF valuation — data quality gate did not pass.")
        return

    _render_wacc(inputs)

    projection = result.get("projection")
    if projection is None or projection.get("start_growth_pct") is None:
        reason = projection.get("projection_reason") if projection else "Unknown error."
        st.warning(f"Base-case projection failed: {reason}")
        return

    base_wacc_pct = inputs["wacc"]["wacc_pct"]
    base_start_growth_pct = projection["start_growth_pct"]
    base_terminal_growth_pct = projection["terminal_growth_pct"]

    start_growth_min = round(max(base_start_growth_pct - START_GROWTH_SLIDER_SPREAD_PCT, STARTING_GROWTH_FLOOR_PCT), 2)
    start_growth_max = round(
        min(base_start_growth_pct + START_GROWTH_SLIDER_SPREAD_PCT, STARTING_GROWTH_CEILING_PCT), 2
    )
    default_wacc_pct = round(base_wacc_pct, 2)
    default_terminal_growth_pct = min(
        max(round(base_terminal_growth_pct, 2), TERMINAL_GROWTH_SLIDER_MIN_PCT),
        TERMINAL_GROWTH_SLIDER_MAX_PCT,
    )
    default_start_growth_pct = round(min(max(base_start_growth_pct, start_growth_min), start_growth_max), 2)

    wacc_key = f"dcf_wacc_slider_{key_ns}_{ticker}"
    terminal_growth_key = f"dcf_terminal_growth_slider_{key_ns}_{ticker}"
    start_growth_key = f"dcf_start_growth_slider_{key_ns}_{ticker}"

    if st.button("Reset to base case", key=f"dcf_reset_button_{key_ns}_{ticker}"):
        # Must run before both the grid's session_state read below and the slider
        # widgets further down (same script execution) so neither shows a stale
        # value on the rerun this click triggers.
        st.session_state[wacc_key] = default_wacc_pct
        st.session_state[terminal_growth_key] = default_terminal_growth_pct
        st.session_state[start_growth_key] = default_start_growth_pct

    # Sliders are declared further down (in the Assumptions section), but the grid
    # renders above them — read each slider's current value from session_state
    # (set by its own widget on a prior run) so the "nearest cell" highlight reflects
    # the live slider position instead of always the base case. Streamlit updates
    # session_state for a widget's key as soon as the user interacts with it, before
    # the script reruns, so this reads the up-to-date value even though the slider
    # widget itself hasn't been (re-)declared yet this run.
    current_wacc_pct = st.session_state.get(wacc_key, default_wacc_pct)
    current_terminal_growth_pct = st.session_state.get(terminal_growth_key, default_terminal_growth_pct)

    grid = result.get("grid")
    if grid is not None:
        _render_sensitivity_grid(grid, current_wacc_pct, current_terminal_growth_pct)
        st.divider()

    st.subheader("5. Assumptions & Live Valuation")

    # Streamlit warns ("created with a default value but also had its value set via
    # the Session State API") if both `value=` and a pre-existing session_state entry
    # apply to the same widget key in the same run — which happens here once the key
    # exists, whether from a prior user interaction or the reset handler above. Only
    # pass `value=` the very first time the widget is ever created for this ticker.
    def _value_kwarg(key: str, default: float) -> dict:
        return {} if key in st.session_state else {"value": default}

    st.caption(
        "Sliders are view-only. They recompute intrinsic value live without altering "
        f"the auto-computed base case above. "
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        slider_wacc_pct = st.slider(
            "WACC (%)",
            min_value=round(base_wacc_pct - WACC_SLIDER_SPREAD_PCT, 2),
            max_value=round(base_wacc_pct + WACC_SLIDER_SPREAD_PCT, 2),
            step=0.1,
            key=wacc_key,
            **_value_kwarg(wacc_key, default_wacc_pct),
        )
    with col2:
        slider_terminal_growth_pct = st.slider(
            "Terminal Growth Rate (%)",
            min_value=TERMINAL_GROWTH_SLIDER_MIN_PCT,
            max_value=TERMINAL_GROWTH_SLIDER_MAX_PCT,
            step=0.1,
            key=terminal_growth_key,
            **_value_kwarg(terminal_growth_key, default_terminal_growth_pct),
        )
    with col3:
        slider_start_growth_pct = st.slider(
            "Starting Growth Rate (%)",
            min_value=start_growth_min,
            max_value=start_growth_max,
            step=0.5,
            key=start_growth_key,
            **_value_kwarg(start_growth_key, default_start_growth_pct),
        )
        st.caption(
            "Base case uses 3yr FCF CAGR — consider analyst estimates or "
            "company guidance for informed overrides."
        )
        if projection["growth_rate_capped"]:
            st.caption(
                f"Starting growth rate capped at {STARTING_GROWTH_CEILING_PCT:.0f}% — the raw "
                "smoothed FCF CAGR was implausibly high (more likely a base-year artifact than "
                "a sustainable trend), so it's capped for the projection. Review manually."
            )
        elif projection["growth_rate_floored"]:
            st.caption(
                f"Starting growth rate floored at {STARTING_GROWTH_FLOOR_PCT:.0f}% — the raw "
                "smoothed FCF CAGR was negative (which the growing-company taper model doesn't "
                "support), so it's floored for the projection. Review manually."
            )

    override_result = _project_from_growth(
        inputs, slider_start_growth_pct, slider_wacc_pct, slider_terminal_growth_pct
    )

    base_col, override_col, price_col = st.columns(3)
    with base_col:
        st.markdown("**Base Case (auto-computed)**")
        bound_suffix = (
            " (capped)" if projection["growth_rate_capped"]
            else " (floored)" if projection["growth_rate_floored"]
            else ""
        )
        st.caption(
            f"WACC {base_wacc_pct:.2f}% · Start Growth {base_start_growth_pct:.2f}%"
            f"{bound_suffix}"
            f" · Terminal Growth {base_terminal_growth_pct:.2f}%"
        )
        _render_valuation_summary("Base Case", projection)
    with override_col:
        st.markdown("**User-Adjusted**")
        st.caption(
            f"WACC {slider_wacc_pct:.2f}% · Start Growth {slider_start_growth_pct:.2f}% "
            f"· Terminal Growth {slider_terminal_growth_pct:.2f}%"
        )
        _render_valuation_summary("User-Adjusted", override_result)
    with price_col:
        st.markdown("**Market**")
        st.caption("Current price, for reference against both estimates.")
        st.metric("Current Price", f"${inputs['current_price']:,.2f}")

    with st.expander("Tapered FCF Projection — Base Case"):
        _render_projection_table(projection)

    with st.expander("Tapered FCF Projection — User-Adjusted"):
        if override_result["projected_fcf"] is not None:
            _render_projection_table(override_result)
        else:
            st.warning(override_result.get("reason") or "Projection failed.")

    st.divider()
    st.subheader("6. Margin of Safety (final)")
    mos_col1, mos_col2 = st.columns(2)
    with mos_col1:
        if projection["intrinsic_value_per_share"] is not None:
            mos_base = _margin_of_safety_pct(projection["intrinsic_value_per_share"], inputs["current_price"])
            st.metric("Base Case MoS", f"{mos_base:+.1f}%")
        else:
            st.warning("Base case projection unavailable.")
    with mos_col2:
        if override_result["intrinsic_value_per_share"] is not None:
            mos_override = _margin_of_safety_pct(
                override_result["intrinsic_value_per_share"], inputs["current_price"]
            )
            st.metric("User-Adjusted MoS", f"{mos_override:+.1f}%")
        else:
            st.warning(override_result.get("reason") or "Override projection unavailable.")


# ---------------------------------------------------------------------------
# Unified ticker input — official 10-ticker dropdown + free-type Explore Mode,
# behind a single Run button. Rule: only a Run click ever produces a displayed
# result, and changing EITHER ticker input (dropdown or Explore text) clears the
# current view immediately (via _clear_dcf_display / on_change) — so a result is
# only ever shown for the exact ticker that was last Run, never auto-restored
# from an earlier-in-session compute just because an input happens to match.
# One result displays at a time via the dcf_active_section exclusivity flag.
# ---------------------------------------------------------------------------

def _clear_dcf_display() -> None:
    """on_change handler for both ticker inputs. Any user edit to the official
    dropdown or the Explore text field drops the current view by clearing the
    active-section flag, so nothing is displayed until the next Run. Fires only
    on real user edits — Streamlit does not invoke on_change for the programmatic
    reset of dcf_explore_ticker_input below, so clearing that field after an
    Explore run does NOT wipe the result that same run just produced."""
    st.session_state.dcf_active_section = None


def _render_ticker_input_and_results() -> None:
    st.caption(
        "Free-cash-flow based intrinsic value for holdings with a 5-year "
        "available financial history and positive, stable FCF. Pick from the official list, or "
        "type any ticker to explore."
    
    )

    if "dcf_results" not in st.session_state:
        st.session_state.dcf_results = {}
    if "dcf_explore_results" not in st.session_state:
        st.session_state.dcf_explore_results = {}

    # Clear the Explore ticker input on the run immediately after it was consumed.
    # This must happen here, before the text_input widget below is (re-)instantiated —
    # Streamlit forbids mutating a widget's session_state in the same run it's created
    # in, so the actual clear is deferred to the top of the next run via this flag.
    if st.session_state.get("dcf_explore_clear_pending"):
        st.session_state.dcf_explore_ticker_input = ""
        st.session_state.dcf_explore_clear_pending = False

    col_left, col_right = st.columns(2)
    with col_left:
        ticker = st.selectbox(
            "Official Ticker", DCF_TICKERS, key="dcf_ticker_select",
            on_change=_clear_dcf_display,
        )
        st.caption(f"Unavailable on FMP API free tier: {', '.join(DCF_TICKERS_PENDING_PAID_TIER)}.")
    with col_right:
        raw_input = st.text_input(
            "Or type any ticker", key="dcf_explore_ticker_input",
            on_change=_clear_dcf_display,
        )

    raw_ticker = raw_input.strip().upper()
    run_target = raw_ticker or ticker
    run = st.button(f"Run New DCF Analysis — {run_target}", key="dcf_run_button")

    if run:
        if raw_ticker:
            with st.spinner(f"Computing DCF for {raw_ticker}..."):
                st.session_state.dcf_explore_results[raw_ticker] = _compute_dcf_result(raw_ticker)
            st.session_state.dcf_explore_last_ticker = raw_ticker
            st.session_state.dcf_active_section = "explore"
            st.session_state.dcf_explore_clear_pending = True
        else:
            with st.spinner(f"Computing DCF for {ticker}..."):
                st.session_state.dcf_results[ticker] = _compute_dcf_result(ticker)
            st.session_state.dcf_official_last_ticker = ticker
            st.session_state.dcf_active_section = "official"
        st.rerun()

    # Render whichever section is active off the ticker that was actually Run
    # (dcf_official_last_ticker / dcf_explore_last_ticker), NOT the live widget
    # values — _clear_dcf_display resets active_section to None on any input edit,
    # so a section is active only immediately after its Run, for that exact ticker.
    active_section = st.session_state.get("dcf_active_section")
    if active_section == "official":
        last_ticker = st.session_state.get("dcf_official_last_ticker")
        result = st.session_state.dcf_results.get(last_ticker) if last_ticker else None
        if result is not None:
            _render_full_dcf_analysis(last_ticker, result, key_ns="official")
    elif active_section == "explore":
        last_ticker = st.session_state.get("dcf_explore_last_ticker")
        result = st.session_state.dcf_explore_results.get(last_ticker) if last_ticker else None
        if result is not None:
            _render_full_dcf_analysis(last_ticker, result, key_ns="explore")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def render_dcf_tab() -> None:
    st.title("DCF Valuation")

    if "dcf_active_section" not in st.session_state:
        # Shared flag so only one of the two sections' results is ever visible at
        # once ("official" or "explore") — flipped whenever either Run button fires.
        st.session_state.dcf_active_section = None

    try:
        _render_ticker_input_and_results()
    except Exception as e:
        # OPEN ITEM (deferred): this broad catch reports a transient "Loading..."
        # message for deterministic rendering bugs (which a refresh won't fix) and
        # pre-empts app.py's more accurate "DCF tab error" surface. Consider narrowing
        # to transient data/network failures and letting logic bugs propagate.
        st.warning(f"Loading... please refresh if this persists. ({e})")


if __name__ == "__main__":
    render_dcf_tab()
