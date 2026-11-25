"""Trading Agent — profit-target backtester + live market (Upstox links).

Run:   streamlit run app.py
"""
from __future__ import annotations

import io
from datetime import date, datetime

import pandas as pd
import streamlit as st

from trading_agent import charts, gsheets, live, upstox
from trading_agent.backtest import (BacktestResult, final_amount_matrix, overview_table, rolling_12m_return,
                                    run_backtest, stock_summary, stock_target_hits, unaffordable_table,
                                    whole_number_table, year_target_matrix, yearly_table)
from trading_agent.charts import inr
from trading_agent.data import load_prices
from trading_agent.excel_report import default_report_name, write_excel
from trading_agent.settings import BacktestConfig
from trading_agent.strategies import STRATEGIES
from trading_agent.universe import ALIASES, CATEGORIES, display_name, yahoo_symbol

st.set_page_config(page_title="Trading Agent", page_icon="📈", layout="wide")

HIGHLIGHT = "background-color: rgba(37,125,224,0.18); font-weight: 600"
RESULT_BG = {"TARGET": "background-color: rgba(10,172,35,0.14)",
             "EXIT": "background-color: rgba(220,60,61,0.14)",
             "OPEN": "background-color: rgba(251,179,21,0.18)"}


# ───────────────────────────── helpers ─────────────────────────────

def theme() -> dict:
    kind = "light"
    try:
        kind = st.context.theme.type or "light"
    except Exception:
        pass
    return charts.THEMES.get(kind, charts.THEMES["light"])


def show(fig) -> None:
    st.plotly_chart(fig, theme=None, config={"displayModeBar": False, "displaylogo": False})


def styled(df: pd.DataFrame, best_col: str | None = None, best_value: float | None = None):
    fmt = {}
    for c in df.columns:
        if "₹" in c and "Price" not in c:
            fmt[c] = inr
        elif "Price" in c or c in ("LTP", "Day Open", "Day High", "Day Low", "5m High", "5m Low", "200 DMA",
                                   "Daily Close"):
            fmt[c] = "{:,.2f}"
        elif c == "Target %" or c.endswith("Target %"):
            fmt[c] = "{:.1f}%"
        elif "%" in c:
            fmt[c] = "{:.2f}%"
        elif "Date" in c or c == "As of":
            fmt[c] = lambda v: v.strftime("%d %b %Y") if pd.notna(v) else ""
        elif c.startswith("Avg Days"):
            fmt[c] = "{:.0f}"
    sty = df.style.format(fmt, na_rep="–")
    if best_col is not None and best_value is not None and best_col in df.columns:
        sty = sty.apply(lambda r: [HIGHLIGHT if abs(r[best_col] - best_value) < 1e-9 else "" for _ in r], axis=1)
    if "Result" in df.columns:
        sty = sty.apply(lambda r: [RESULT_BG.get(r["Result"], "") for _ in r], axis=1)
    return sty


@st.cache_resource(show_spinner=False)
def _result_store() -> dict:
    return {}


def get_result(cfg: BacktestConfig, cats: list[str], refresh: bool) -> BacktestResult:
    key = (tuple(sorted(cfg.as_dict().items())), tuple(cats), date.today().isoformat())
    store = _result_store()
    if refresh or key not in store:
        bar = st.progress(0.0, text="Starting…")
        res = run_backtest(cfg, cats, refresh=refresh,
                           progress=lambda f, m: bar.progress(min(max(f, 0.0), 1.0), text=m))
        bar.empty()
        store[key] = res
        while len(store) > 6:
            store.pop(next(iter(store)))
    return store[key]


def to_xlsx_bytes(df: pd.DataFrame, sheet: str = "Sheet1") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as xw:
        df.to_excel(xw, sheet_name=sheet, index=False)
    return buf.getvalue()


# ───────────────────────────── backtest page ─────────────────────────────

def sidebar_settings() -> tuple[BacktestConfig, list[str], bool, bool]:
    base = BacktestConfig.from_env()
    this_year = date.today().year
    with st.sidebar:
        st.subheader("Backtest settings")
        with st.form("params"):
            strategy = st.radio("Strategy", ["dma", "car"], format_func=lambda k: STRATEGIES[k].name)
            start_year = st.number_input("Start year", min_value=2005, max_value=this_year,
                                         value=min(base.start_year, this_year), step=1,
                                         help="The test runs from 1 January of this year up to the latest price.")
            capital = st.number_input("Capital per category (₹)", min_value=1000, value=int(base.capital),
                                      step=10000, help="Default = BACKTEST_CAPITAL in .env (₹1,00,000).")
            chunk_opts = ["auto"] + list(range(int(base.chunk_min), int(base.chunk_max) + 1, 500))
            chunk = st.selectbox(
                "Money per trade (chunk)", chunk_opts,
                format_func=lambda v: (f"Auto: equal chunks of {inr(base.chunk_min)}–{inr(base.chunk_max)}"
                                       if v == "auto" else inr(v)),
                help="Capital is cut into equal chunks; every share in the category gets the same chunk.")
            st.markdown("**Profit targets to test**")
            c1, c2, c3 = st.columns(3)
            tmin = c1.number_input("From %", value=float(base.target_min), step=0.5, format="%.1f")
            tmax = c2.number_input("To %", value=float(base.target_max), step=0.5, format="%.1f")
            tstep = c3.number_input("Step", value=float(base.target_step), min_value=0.05, step=0.05, format="%.2f")
            cats = st.multiselect("Categories", list(CATEGORIES), default=list(CATEGORIES))
            with st.expander("Advanced"):
                fill = st.radio("Target is hit when…", ["close", "high"],
                                format_func=lambda v: {"close": "the day CLOSES above it (like hello.ipynb)",
                                                       "high": "the day's HIGH touches it (GTT / limit sell)"}[v])
                compound = st.toggle("Re-invest profits (compounding)", value=False)
                ch_pct = st.number_input("Charges % per side", value=float(base.charges_pct), min_value=0.0,
                                         step=0.01, format="%.2f", help="STT + stamp + exchange, approx.")
                ch_flat = st.number_input("₹ per sell (DP charge)", value=float(base.charges_flat), min_value=0.0,
                                          step=1.0)
                refresh = st.checkbox("Re-download prices now")
            submitted = st.form_submit_button("Run backtest", type="primary", icon=":material/play_arrow:")
        st.caption("Defaults come from the .env file. Prices: Yahoo Finance (cached for 12 h).")

    if tmax < tmin:
        tmin, tmax = tmax, tmin
    cfg = BacktestConfig.from_env(
        strategy=strategy, start_year=int(start_year), capital=float(capital),
        chunk_size=None if chunk == "auto" else float(chunk), target_min=float(tmin), target_max=float(tmax),
        target_step=float(tstep), fill_mode=fill, compounding=compound, charges_pct=float(ch_pct),
        charges_flat=float(ch_flat))
    return cfg, cats or list(CATEGORIES), refresh, submitted


def insight_lines(res: BacktestResult, ov: pd.DataFrame) -> list[str]:
    cfg = res.config
    lines = []
    top = ov.loc[ov["Final Amount (₹)"].idxmax()]
    lines.append(f"**Best overall:** {top['Category']} at a **{top['Best Target %']:.1f}%** target → "
                 f"{inr(top['Final Amount (₹)'])} ({top['Return %']:+.1f}% total, {top['CAGR %']:.1f}% a year).")
    edge = ov[ov["Best Target %"] >= cfg.target_max - cfg.target_step * 2]
    if len(edge) >= max(2, len(ov) // 2):
        lines.append(f"In **{len(edge)} of {len(ov)}** categories the best target sits at the top of your range — "
                     f"profits may keep rising above {cfg.target_max:.1f}%. Try a wider range in the sidebar.")
    spread = (ov["Best Target %"] - ov["Most Stable Target %"]).abs()
    if (spread > 0.5).any():
        names = ", ".join(ov.loc[spread > 0.5, "Category"])
        lines.append(f"The single best target is noisy in {names}: compare it with the **most stable** target "
                     "(best average of the target and its ±0.2% neighbours).")
    stuck = []
    for name, cr in res.categories.items():
        run = cr.run()
        if run.stats["Still Open"] >= cr.slots and cr.slots > 0:
            stuck.append(name)
    if stuck:
        lines.append(f"All chunks are tied up in open trades at the end in {len(stuck)} categor"
                     f"{'y' if len(stuck) == 1 else 'ies'} — those trades have not reached the target yet.")
    return lines


def checkpoint_matrix(res: BacktestResult, field: str) -> pd.DataFrame:
    out = {}
    for name, cr in res.categories.items():
        wn = whole_number_table(cr).set_index("Target %")[field]
        out[name] = wn
    df = pd.DataFrame(out)
    df.index = [f"{v:.0f}%" for v in df.index]
    df.index.name = "Target"
    return df


def overview_tab(res: BacktestResult, t: dict) -> None:
    ov = overview_table(res)
    if ov.empty:
        st.warning("No results — check that the categories have price data.")
        return
    for line in insight_lines(res, ov):
        st.markdown(f"- {line}")

    left, right = st.columns([1, 1.25])
    with left:
        show(charts.best_by_category_chart(ov, res.config.capital, t))
    with right:
        st.markdown("**Whole-number checkpoints — final amount** (best in each row highlighted)")
        fin = checkpoint_matrix(res, "Final Amount (₹)")
        st.dataframe(fin.style.format(inr).highlight_max(axis=1, props=HIGHLIGHT))
        st.markdown("**…and how many trades hit the target**")
        st.dataframe(checkpoint_matrix(res, "Target Hits").style.format("{:.0f}"))

    st.markdown("##### Net total amount vs profit target — every category (same axes)")
    sweeps = {n: cr.sweep for n, cr in res.categories.items() if not cr.sweep.empty}
    show(charts.overview_small_multiples(sweeps, t, cols=3 if len(sweeps) > 4 else 2))

    st.markdown("##### Best target per category")
    cols = ["Category", "Stocks", "Chunks", "₹ per Chunk", "Best Target %", "Most Stable Target %",
            "Final Amount (₹)", "Net Profit (₹)", "Return %", "CAGR %", "Max Drawdown %", "Trades", "Target Hits",
            "Hit Rate %", "Worst Target %", "Final at Worst (₹)"]
    st.dataframe(styled(ov[cols]), hide_index=True)
    with st.expander("Final amount for every target × category (table)"):
        m = final_amount_matrix(res)
        m.index = [f"{v:.1f}%" for v in m.index]
        st.dataframe(m.style.format(inr).highlight_max(axis=0, props=HIGHLIGHT))


def deep_dive_tab(res: BacktestResult, t: dict) -> None:
    cfg = res.config
    names = [n for n, cr in res.categories.items() if not cr.sweep.empty]
    c1, c2 = st.columns([1, 2])
    cat = c1.selectbox("Category", names, key="dd_cat")
    cr = res.categories[cat]
    targets = sorted(cr.runs)
    tgt = c2.select_slider("Profit target to inspect", options=targets, value=cr.best_target,
                           format_func=lambda v: f"{v:.1f}%", key=f"dd_tgt_{cfg.strategy}_{cat}")
    run = cr.run(tgt)
    s = run.stats
    st.caption(f"{len(cr.tickers)} stocks · {cr.slots} equal chunks of {inr(cr.chunk)} · "
               f"best target **{cr.best_target:.1f}%** · most stable **{cr.stable_target:.1f}%** · "
               f"worst {cr.worst_target:.1f}% · at {run.target:.1f}%: **{s['Target Hits']} of {s['Trades']}** "
               f"trades hit the target, {s['Trend Exits']} trend exits, {s['Still Open']} still open")

    k = st.columns(6)
    k[0].metric("Final amount", charts.inr_short(s["Final Amount (₹)"]), f"{s['Return %']:+.1f}%", border=True,
                help=inr(s["Final Amount (₹)"]))
    k[1].metric("Net profit", charts.inr_short(s["Net Profit (₹)"]), border=True, help=inr(s["Net Profit (₹)"]))
    k[2].metric("Per year (CAGR)", f"{s['CAGR %']:.1f}%", border=True)
    k[3].metric("Hit rate", f"{s['Hit Rate %']:.0f}%", border=True,
                help=f"{s['Target Hits']} of {s['Trades']} trades reached the {run.target:.1f}% target")
    k[4].metric("Worst fall", f"{s['Max Drawdown %']:.1f}%", border=True, help="Maximum drawdown of the account")
    k[5].metric("Days to target", "–" if pd.isna(s["Avg Days to Target"]) else f"{s['Avg Days to Target']:.0f}",
                border=True, help="Average calendar days a winning trade was held")

    view = st.segmented_control("Show", ["Target sweep", "Year-wise", "Trades & stocks"], default="Target sweep",
                                key="dd_view", label_visibility="collapsed") or "Target sweep"
    if view == "Target sweep":
        a, b = st.columns([1.35, 1])
        with a:
            show(charts.sweep_chart(cr.sweep, t, selected=tgt, title=f"{cat}: net total amount by profit target"))
        with b:
            show(charts.outcomes_chart(cr.sweep, t, has_exits=cfg.strategy == "dma"))
        st.markdown("**5 % → 10 % checkpoints**")
        wn = whole_number_table(cr)[["Target %", "Final Amount (₹)", "Net Profit (₹)", "Return %", "Trades",
                                     "Target Hits", "Trend Exits", "Still Open", "Hit Rate %", "Avg Days to Target"]]
        st.dataframe(styled(wn, "Final Amount (₹)", wn["Final Amount (₹)"].max()), hide_index=True)
        with st.expander(f"All {len(cr.sweep)} targets", expanded=False):
            st.dataframe(styled(cr.sweep, "Target %", cr.best_target), hide_index=True)

    elif view == "Year-wise":
        yt = yearly_table(run, cfg.capital)
        st.dataframe(styled(yt), hide_index=True)
        a, b = st.columns(2)
        with a:
            show(charts.yearly_pnl_chart(yt, t))
        with b:
            show(charts.rolling_profit_chart(run.equity, cfg.capital, t))
        a, b = st.columns(2)
        with a:
            r12 = rolling_12m_return(run)
            if len(r12):
                show(charts.rolling_return_chart(r12, t))
        with b:
            show(charts.capital_use_chart(run.equity, run.invested, t))
        show(charts.year_target_heatmap(year_target_matrix(cr, cfg.capital), t))

    else:
        tr = run.trades
        f1, f2 = st.columns([1, 2])
        pick = f1.multiselect("Result", ["TARGET", "EXIT", "OPEN"], default=["TARGET", "EXIT", "OPEN"],
                              key=f"res_{cat}")
        stocks = f2.multiselect("Stocks", sorted(tr["Stock"].unique()), key=f"stk_{cat}")
        view = tr[tr["Result"].isin(pick)]
        if stocks:
            view = view[view["Stock"].isin(stocks)]
        st.caption(f"{len(view)} trades at the {run.target:.1f}% target · green = hit the target · "
                   "red = trend exit · yellow = still open (valued at the last close)")
        st.dataframe(styled(view), hide_index=True, height=420)
        a, b = st.columns([1, 1])
        with a:
            st.markdown("**Per stock**")
            st.dataframe(styled(stock_summary(run)), hide_index=True, height=420)
            un = unaffordable_table(cr)
            if len(un):
                st.markdown(f"**Signals skipped — one share cost more than the {inr(cr.chunk)} chunk**")
                st.dataframe(styled(un), hide_index=True)
        with b:
            show(charts.stock_hits_heatmap(stock_target_hits(cr), t))


def data_tab(res: BacktestResult) -> None:
    cfg = res.config
    st.markdown("##### Excel report")
    st.write("One workbook with a summary, the full target sweep, year-wise results, every trade and per-stock "
             "hits for each category — with Excel charts.")
    key = f"xlsx_{id(res)}"
    if st.button("Build Excel report", icon=":material/table_view:"):
        with st.spinner("Writing the workbook…"):
            buf = io.BytesIO()
            write_excel(res, buf)
            st.session_state[key] = buf.getvalue()
    if key in st.session_state:
        st.download_button("Download Excel", st.session_state[key], file_name=default_report_name(res),
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           type="primary", icon=":material/download:")

    st.markdown("##### Data used")
    rows = []
    for name, cr in res.categories.items():
        for tk in CATEGORIES.get(name, []):
            run = cr.run() if cr.runs else None
            status = "used" if tk in cr.tickers else "no data"
            if run and tk in run.unaffordable:
                status = "price above chunk"
            rows.append({"Category": name, "Stock": display_name(tk), "Yahoo symbol": yahoo_symbol(tk),
                         "Status": status})
    cov = pd.DataFrame(rows)
    st.dataframe(cov, hide_index=True, height=300)
    if ALIASES:
        st.caption("Renamed symbols: " + ", ".join(f"{display_name(a)} → {display_name(b)}" for a, b in ALIASES.items()))

    st.markdown("##### How the test works")
    info = STRATEGIES[cfg.strategy]
    st.markdown(f"""
- **Buy:** {info.buy_rule}. Signals use daily closes and are acted on at that close.
- **Sell:** {info.exit_rule}. Each target from {cfg.target_min:.1f}% to {cfg.target_max:.1f}% (step {cfg.target_step:.1f}%) is a separate test.
- **Money:** each category starts with {inr(cfg.capital)}, cut into equal chunks of {inr(cfg.chunk_min)}–{inr(cfg.chunk_max)}. One chunk per trade, whole shares only, one position per stock. When more stocks signal than there are free chunks, the earlier stocks in your list get them.
- **Charges:** {cfg.charges_pct:.2f}% per side + {inr(cfg.charges_flat)} per sell.
- **Net total amount** = cash + open positions at the last close.
- **Caution:** the lists are *today's* index members, so stocks that fell out of the index are missing (survivorship bias) — real-life results would likely be lower. Past results don't guarantee future returns.
""")


def backtest_page() -> None:
    cfg, cats, refresh, submitted = sidebar_settings()
    ss = st.session_state
    if submitted or "result" not in ss:
        ss["result"] = get_result(cfg, cats, refresh)
    res: BacktestResult = ss["result"]
    rc = res.config
    t = theme()
    st.title("Profit-target backtester")
    end = f"{res.data_end:%d %b %Y}" if res.data_end is not None else "–"
    st.caption(f"{STRATEGIES[rc.strategy].name} · 1 Jan {rc.start_year} → {end} · {inr(rc.capital)} per category · "
               f"targets {rc.target_min:.1f}% → {rc.target_max:.1f}% every {rc.target_step:.1f}% "
               f"({len(rc.targets())} tests per category)"
               + (" · profits re-invested" if rc.compounding else "")
               + (" · target checked on the day's high" if rc.fill_mode == "high" else ""))
    if res.failed:
        st.caption("No price data for: " + ", ".join(display_name(x) for x in res.failed))
    view = st.segmented_control("View", ["Overview", "Category deep-dive", "Excel & data"], default="Overview",
                                key="main_view", label_visibility="collapsed") or "Overview"
    if view == "Overview":
        overview_tab(res, t)
    elif view == "Category deep-dive":
        deep_dive_tab(res, t)
    else:
        data_tab(res)


# ───────────────────────────── live page ─────────────────────────────

@st.cache_data(ttl=6 * 3600, show_spinner=False)
def instrument_map(tickers: tuple[str, ...]) -> tuple[dict, list]:
    return upstox.instrument_keys(list(tickers), upstox.load_instruments())


@st.cache_data(ttl=3600, show_spinner=False)
def strategy_status(tickers: tuple[str, ...]) -> pd.DataFrame:
    prices, _ = load_prices(list(tickers))
    return live.strategy_status_table(prices, tickers)


def live_page() -> None:
    st.title("Live market — Upstox")
    st.caption("Runs in the browser on Mac, Windows or Linux using the Upstox links (login → token → quotes) "
               "and the public instrument list. No Excel, xlwings or appscript needed.")
    creds = upstox.credentials()
    token = creds["access_token"]
    valid = upstox.token_is_valid(token)
    exp = upstox.token_expiry(token) if token else None

    if not creds["api_key"] or not creds["api_secret"]:
        st.error("Add UPSTOX_API_KEY and UPSTOX_API_SECRET to the .env file (see .env.example).")
    elif valid:
        st.success("Connected — today's token is saved in .env" + (f" (valid until {exp:%d %b, %H:%M})" if exp else ""),
                   icon=":material/check_circle:")
    else:
        st.warning("Not connected. Log in once a day — Upstox tokens expire around 3:30 AM.",
                   icon=":material/key:")

    with st.expander("Log in to Upstox", expanded=not valid, icon=":material/login:"):
        st.markdown(
            "1. Click **Open Upstox login** and sign in.\n"
            f"2. Upstox then sends your browser to `{creds['redirect_uri']}?code=…` — the page itself won't load; "
            "that's expected. Copy the whole address from the address bar.\n"
            "3. Paste it below and press **Save token**. It is stored in `.env` for the app and the notebooks.")
        if creds["api_key"]:
            st.link_button("Open Upstox login", upstox.login_url(creds["api_key"], creds["redirect_uri"]),
                           type="primary", icon=":material/open_in_new:")
        with st.form("token_form", clear_on_submit=True):
            pasted = st.text_input("Redirected address (or just the code)", type="password")
            if st.form_submit_button("Save token"):
                try:
                    tok = upstox.exchange_code(upstox.extract_code(pasted), creds["api_key"], creds["api_secret"],
                                               creds["redirect_uri"])
                    upstox.save_token(tok)
                    st.success("Token saved.")
                    st.rerun()
                except upstox.UpstoxError as e:
                    st.error(str(e))
                except Exception as e:  # network problems etc.
                    st.error(f"Could not reach Upstox: {e}")

    c1, c2, c3, c4 = st.columns([2, 1, 1, 1], vertical_alignment="bottom")
    cat = c1.selectbox("Watchlist", list(CATEGORIES))
    every = c2.selectbox("Refresh every", [5, 10, 15, 30, 60], index=1, format_func=lambda s: f"{s} seconds")
    method = gsheets.available_method()
    push = c3.toggle("Google Sheet", value=False, disabled=method is None,
                     help="Needs GOOGLE_SHEETS_WEBAPP_URL (no JSON file — see apps_script/LivePush.gs) "
                          "or a service_account.json in .env.")
    auto = c4.toggle("Live updates", value=False, disabled=not valid)

    tickers = tuple(CATEGORIES[cat])
    try:
        keys, missing = instrument_map(tickers)
    except Exception as e:
        st.error(f"Could not download the Upstox instrument list: {e}")
        return
    with st.spinner("Reading daily signals…"):
        status = strategy_status(tickers)
    if missing:
        st.caption("Not on Upstox: " + ", ".join(display_name(m) for m in missing))

    if status.empty:
        st.warning("No daily price data for this watchlist yet.")
        return
    if not valid:
        st.info("Log in above to see live prices. Meanwhile, here are today's strategy signals from daily closes.")
        view = status.assign(Stock=status["Ticker"].map(display_name)).drop(columns=["Ticker"])
        st.dataframe(styled(view[["Stock"] + [c for c in view.columns if c != "Stock"]]), hide_index=True,
                     height=min(36 * len(view) + 40, 760))
        return

    @st.fragment(run_every=every if auto else None)
    def board() -> None:
        try:
            q = upstox.fetch_quotes(upstox.credentials()["access_token"], keys)
        except upstox.UpstoxError as e:
            st.error(str(e))
            return
        except Exception as e:
            st.error(f"Network problem talking to Upstox: {e}")
            return
        if q.empty:
            st.warning("Upstox returned no quotes.")
            return
        q = upstox.update_five_minute_bars(st.session_state.setdefault("bars5m", {}), q)
        table = live.build_live_table(q, status)
        st.dataframe(styled(table), hide_index=True, height=min(38 * len(table) + 40, 760))
        st.caption(f"Updated {datetime.now():%H:%M:%S} · {len(table)} stocks"
                   + (f" · refreshing every {every} s" if auto else " · switch on Live updates to auto-refresh"))
        if push:
            try:
                st.caption(gsheets.push(table))
            except Exception as e:
                st.warning(f"Google Sheet: {e}")
        st.download_button("Download this table (Excel)", to_xlsx_bytes(table, "Live"),
                           file_name=f"live_{cat.replace(' ', '_')}_{datetime.now():%Y%m%d_%H%M}.xlsx",
                           icon=":material/download:")

    board()


if __name__ == "__main__":  # `streamlit run app.py` runs this; tests can import the pages
    st.navigation([
        st.Page(backtest_page, title="Backtest", icon=":material/query_stats:", default=True),
        st.Page(live_page, title="Live market", icon=":material/sensors:"),
    ]).run()
