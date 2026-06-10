from typing import Optional

import streamlit as st
import pandas as pd
import plotly.express as px

from data.earnings import get_eps_surprise
from data.financials import get_key_stats, get_revenue_surprise, get_chart_data

_TICKERS = [
    "AAPL", "AMZN", "ARWR", "BA", "BRK-B", "CAT", "COST", "CVX",
    "DAL", "ET", "GE", "GOOGL", "GS", "HD", "IESC", "JPM",
    "LLY", "LMT", "LOW", "MELI", "META", "MSFT", "NOC", "NVDA",
    "ONDS", "ORCL", "TKO", "V", "VRT", "WMB", "WMT", "XMTR",
]


_QUARTER_END = {"Q1": "March 31", "Q2": "June 30", "Q3": "September 30", "Q4": "December 31"}


def _fmt_yoy(val) -> Optional[str]:
    if val is None:
        return None
    return f"{val:+.1f}% YoY"


def _quarter_caption(label: str) -> str:
    parts = label.split()
    if len(parts) != 2:
        return f"Most Recent Quarter: {label}"
    quarter, year = parts[0], parts[1]
    month_day = _QUARTER_END.get(quarter)
    if not month_day:
        return f"Most Recent Quarter: {quarter} FY{year}"
    return f"Most Recent Quarter: {quarter} FY{year} ({month_day}, {year})"


def _fmt_quarter_short(label: str) -> str:
    parts = label.split()
    if len(parts) != 2:
        return label
    return f"{parts[0]} FY{parts[1]}"


def _beat_miss_card(label: str, data: Optional[dict], ticker: str) -> None:
    if data is None:
        st.warning(f"Estimate data unavailable for {ticker}")
        return

    actual = data["actual"]
    estimate = data["estimate"]
    surprise_pct = data["surprise_pct"]
    status = data["status"]

    st.metric(
        label=f"{label} Actual",
        value=actual,
        delta=f"{surprise_pct:+.1f}% vs estimate",
    )
    if status == "BEAT":
        st.success("✅ BEAT")
    elif status == "MISS":
        st.error("❌ MISS")
    else:
        st.info("➖ IN LINE")
    st.caption(f"Estimate was {estimate}")


def render_earnings_tab() -> None:
    try:
        st.title("Earnings Analysis")

        if "earnings_results" not in st.session_state:
            st.session_state.earnings_results = {}

        col_select, col_input = st.columns(2)
        with col_select:
            selected = st.selectbox("Select a ticker:", _TICKERS, key="earnings_ticker_select")
        with col_input:
            typed = st.text_input("Or type any ticker:", key="earnings_ticker_typed")

        t = typed.strip()
        ticker = t.upper() if t else selected

        run = st.button("Run Analysis", use_container_width=True, key="earnings_run_button")
        if run:
            with st.spinner("Fetching earnings data..."):
                eps_data     = get_eps_surprise(ticker)
                rev_surprise = get_revenue_surprise(ticker)
                key_stats    = get_key_stats(ticker)
                chart_data   = get_chart_data(ticker)
            st.session_state.earnings_results[ticker] = {
                "eps_data":    eps_data,
                "rev_surprise": rev_surprise,
                "key_stats":   key_stats,
                "chart_data":  chart_data,
            }

        results = st.session_state.earnings_results.get(ticker)
        if results is None:
            return

        eps_data     = results["eps_data"]
        rev_surprise = results["rev_surprise"]
        key_stats    = results["key_stats"]
        chart_data   = results["chart_data"]

        # ------------------------------------------------------------------
        # Section 1 — Beat / Miss
        # ------------------------------------------------------------------
        st.subheader("Beat / Miss")
        col_eps, col_rev = st.columns(2)
        with col_eps:
            _beat_miss_card("EPS", eps_data, ticker)
            st.caption("EPS figures are GAAP diluted. Estimates may vary by source.")
        with col_rev:
            _beat_miss_card("Revenue", rev_surprise, ticker)

        # ------------------------------------------------------------------
        # Section 2 — Key Stats
        # ------------------------------------------------------------------
        quarter_label = _fmt_quarter_short(chart_data[-1]["label"]) if chart_data else "Most Recent Quarter"
        st.subheader(f"Key Stats — {quarter_label}")
        if key_stats is None:
            st.warning(f"Financial data unavailable for {ticker}")
        else:
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Revenue", key_stats["revenue"],
                      delta=_fmt_yoy(key_stats.get("revenue_yoy")),
                      delta_color="normal")
            c2.metric("Net Income", key_stats["net_income"])
            c3.metric("Gross Margin", f"{key_stats['gross_margin']}%")
            c4.metric("Operating Margin", f"{key_stats['operating_margin']}%")
            c5.metric("EPS", f"${key_stats['eps']:.2f}",
                      delta=_fmt_yoy(key_stats.get("eps_yoy")),
                      delta_color="normal")

        # ------------------------------------------------------------------
        # Section 3 — Charts
        # ------------------------------------------------------------------
        if not chart_data:
            st.info("Chart data unavailable")
            return

        st.subheader("Trends — Last 5 Quarters")
        df = pd.DataFrame(chart_data)

        col_r, col_e, col_m = st.columns(3)

        with col_r:
            fig = px.bar(
                df, x="label", y="revenue_raw",
                title="Revenue (Last 5 Quarters)",
                labels={"label": "Quarter", "revenue_raw": "Revenue ($)"},
                color_discrete_sequence=["#4C78A8"],
            )
            fig.update_layout(showlegend=False, plot_bgcolor="rgba(0,0,0,0)")
            fig.update_yaxes(tickprefix="$", tickformat=".2s")
            st.plotly_chart(fig, use_container_width=True)

        with col_e:
            fig = px.bar(
                df, x="label", y="eps",
                title="EPS (Last 5 Quarters)",
                labels={"label": "Quarter", "eps": "EPS ($)"},
                color_discrete_sequence=["#54A24B"],
            )
            fig.update_layout(showlegend=False, plot_bgcolor="rgba(0,0,0,0)")
            fig.update_yaxes(tickprefix="$")
            st.plotly_chart(fig, use_container_width=True)

        with col_m:
            df_melted = df.melt(
                id_vars="label",
                value_vars=["gross_margin", "operating_margin"],
                var_name="Metric",
                value_name="Margin (%)",
            )
            df_melted["Metric"] = df_melted["Metric"].replace({
                "gross_margin": "Gross Margin",
                "operating_margin": "Operating Margin",
            })
            fig = px.line(
                df_melted, x="label", y="Margin (%)", color="Metric",
                title="Margins (Last 5 Quarters)",
                labels={"label": "Quarter"},
                color_discrete_sequence=["#4C78A8", "#F58518"],
                markers=True,
            )
            fig.update_layout(
                plot_bgcolor="rgba(0,0,0,0)",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            fig.update_yaxes(ticksuffix="%")
            st.plotly_chart(fig, use_container_width=True)

    except Exception as e:
        st.warning(f"Loading... please refresh if this persists. ({e})")


if __name__ == "__main__":
    render_earnings_tab()
