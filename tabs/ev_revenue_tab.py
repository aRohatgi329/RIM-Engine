from typing import Optional

import streamlit as st

from data.earnings import _fmt_currency
from models.ev_revenue import EV_REVENUE_TICKERS, run_ev_revenue


def _signal_badge(signal: str) -> None:
    if signal == "UNDERVALUED":
        st.success("🟢 UNDERVALUED")
    elif signal == "OVERVALUED":
        st.error("🔴 OVERVALUED")
    elif signal == "FAIR":
        st.info("🟡 FAIR")
    else:
        st.warning(f"⚪ {signal}")


def _ticker_card(result: Optional[dict], ticker: str) -> None:
    if result is None:
        st.warning(f"No data for {ticker}")
        return

    ev_revenue_ratio = result["ev_revenue_ratio"]
    ratio_vs_median = result["ratio_vs_median"]

    if ev_revenue_ratio is not None and ratio_vs_median is not None:
        ratio_delta_pct = (ratio_vs_median - 1) * 100
        st.metric(
            label="EV / Revenue",
            value=f"{ev_revenue_ratio:.2f}x",
            delta=f"{ratio_delta_pct:+.1f}% vs median",
            delta_color="inverse",  # below median (negative %) = good = green
        )
    else:
        st.metric(label="EV / Revenue", value="N/A")

    _signal_badge(result["signal"])
    st.caption(f"Sector median: {result['sector_median']:.2f}x")
    st.caption(f"EV: {_fmt_currency(result['ev'])}")
    st.caption("EV = Market Cap + Total Debt − Cash")
    st.caption(f"TTM Revenue: {_fmt_currency(result['revenue'])}")


def render_ev_revenue_tab() -> None:
    try:
        st.title("EV / Revenue Analysis")
        st.caption("Covers small-cap / high-growth holdings not suited to the RIM model.")

        if "ev_revenue_results" not in st.session_state:
            st.session_state.ev_revenue_results = {}

        run = st.button("Run EV/Revenue Analysis — XMTR, ONDS, ARWR", use_container_width=True, key="ev_revenue_run_button")
        if run:
            with st.spinner("Fetching EV/Revenue data..."):
                for ticker in EV_REVENUE_TICKERS:
                    st.session_state.ev_revenue_results[ticker] = run_ev_revenue(ticker)

        results = st.session_state.ev_revenue_results
        if not any(v is not None for v in results.values()):
            return

        cols = st.columns(len(EV_REVENUE_TICKERS))
        for col, ticker in zip(cols, EV_REVENUE_TICKERS):
            with col:
                st.subheader(ticker)
                _ticker_card(results.get(ticker), ticker)

        st.divider()
        st.caption(
            "Signal thresholds: UNDERVALUED < 0.80× median · FAIR 0.80–1.25× · OVERVALUED > 1.25×  |  "
            "EV = Market Cap + Total Debt − Cash (TTM Revenue, last 4 quarters)"
        )

    except Exception as e:
        st.warning(f"Loading... please refresh if this persists. ({e})")


if __name__ == "__main__":
    render_ev_revenue_tab()
