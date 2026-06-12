import pandas as pd
import streamlit as st

from models.rim import run_rim


# ---------------------------------------------------------------------------
# Rule-based analysis
# ---------------------------------------------------------------------------

def _generate_analysis(results: list) -> str:
    buys  = [r for r in results if r["signal"] == "BUY"]
    holds = [r for r in results if r["signal"] == "HOLD"]
    sells = [r for r in results if r["signal"] == "SELL"]

    sentences = []

    if buys:
        names = ", ".join(
            f"{r['ticker']} ({r['margin_of_safety_pct']:+.1f}% MoS)" for r in buys
        )
        sentences.append(
            f"The model flags {names} as undervalued — "
            f"{'each is' if len(buys) > 1 else 'it is'} trading at a meaningful discount "
            "to its estimated residual-income value, offering a margin of safety above the 15% threshold."
        )
    if sells:
        names = ", ".join(
            f"{r['ticker']} ({r['margin_of_safety_pct']:+.1f}%)" for r in sells
        )
        sentences.append(
            f"{names} {'appear' if len(sells) > 1 else 'appears'} overvalued — "
            f"{'their' if len(sells) > 1 else 'its'} current market price exceeds "
            "the RIM intrinsic value, leaving no margin of safety."
        )
    if holds:
        names = ", ".join(r["ticker"] for r in holds)
        sentences.append(
            f"{names} {'sit' if len(holds) > 1 else 'sits'} in fairly-valued territory, "
            "trading close enough to RIM value that the signal is neutral."
        )

    sentences.append(
        "These signals are derived from a residual income model using historical ROE and book value; "
        "they work best on capital-intensive companies with stable earnings and should be treated as "
        "a directional screen rather than a price target."
    )

    return "  \n".join(sentences)


PORTFOLIO_TICKERS = ["JPM", "BRK-B", "LMT", "WMB"]

st.set_page_config(
    page_title="Team Davis Investment Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

if "portfolio_results" not in st.session_state:
    st.session_state.portfolio_results = []
if "portfolio_errors" not in st.session_state:
    st.session_state.portfolio_errors = []
if "portfolio_analysis" not in st.session_state:
    st.session_state.portfolio_analysis = ""

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
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.title("Team Davis")
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "Navigation",
    ["📈 RIM Valuation", "📊 Earnings Analysis", "EV/Revenue Analysis", "📚 Learning"],
    label_visibility="collapsed",
)

st.sidebar.caption("JPM · BRK.B · LMT · WMB")
st.sidebar.caption("XMTR · ONDS · ARWR")
st.sidebar.markdown("---")
st.sidebar.caption("RIM Engine: JPM · BRK-B · LMT · WMB")
st.sidebar.caption("Earnings: All 32 holdings")
st.sidebar.caption("Data: FMP · yfinance")

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("Team Davis Investment Dashboard")
st.caption("Quantitative Research Tools")

# ---------------------------------------------------------------------------
# Page: RIM Valuation
# ---------------------------------------------------------------------------

if page == "📈 RIM Valuation":
    st.caption("Tickers: " + "  ·  ".join(PORTFOLIO_TICKERS))

    if st.button("Run RIM Valuation Analysis"):
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

    if st.session_state.portfolio_results:
        st.divider()
        st.subheader("Analysis")
        if st.button("Generate Analysis"):
            analysis = _generate_analysis(st.session_state.portfolio_results)
            st.session_state.portfolio_analysis = analysis
        if "portfolio_analysis" in st.session_state and st.session_state.portfolio_analysis:
            st.info(st.session_state.portfolio_analysis)

# ---------------------------------------------------------------------------
# Page: Earnings Analysis
# ---------------------------------------------------------------------------

elif page == "📊 Earnings Analysis":
    try:
        from tabs.earnings_tab import render_earnings_tab
        render_earnings_tab()
    except Exception as e:
        st.error(f"Earnings tab error: {e}")

# ---------------------------------------------------------------------------
# Page: About RIM
# ---------------------------------------------------------------------------

elif page == "EV/Revenue Analysis":
    from tabs.ev_revenue_tab import render_ev_revenue_tab
    render_ev_revenue_tab()

elif page == "📚 Learning":
    from tabs.learning_tab import render_learning_tab
    render_learning_tab()
