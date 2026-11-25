"""Profit-target sweep (5.0 % → 10.0 %) for every category + the tables built on it."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from .data import load_prices
from .engine import CategoryData, RunResult, plan_chunks, prepare_category, simulate
from .settings import BacktestConfig
from .universe import CATEGORIES, display_name

Progress = Callable[[float, str], None]
STABLE_COL = "Avg of ±0.2% Targets (₹)"


@dataclass
class CategoryResult:
    name: str
    tickers: list[str]
    failed: list[str]
    slots: int
    chunk: float
    first_date: pd.Timestamp | None
    last_date: pd.Timestamp | None
    sweep: pd.DataFrame                     # one row per profit target
    runs: dict[float, RunResult] = field(repr=False, default_factory=dict)

    @property
    def best_target(self) -> float:
        return float(self.sweep.loc[self.sweep["Final Amount (₹)"].idxmax(), "Target %"])

    @property
    def stable_target(self) -> float:
        return float(self.sweep.loc[self.sweep[STABLE_COL].idxmax(), "Target %"])

    @property
    def worst_target(self) -> float:
        return float(self.sweep.loc[self.sweep["Final Amount (₹)"].idxmin(), "Target %"])

    def run(self, target: float | None = None) -> RunResult:
        if target is None:
            target = self.best_target
        key = min(self.runs, key=lambda t: abs(t - target))
        return self.runs[key]


@dataclass
class BacktestResult:
    config: BacktestConfig
    categories: dict[str, CategoryResult]
    failed: list[str]
    data_end: pd.Timestamp | None


def run_category(name: str, tickers: list[str], prices: dict, cfg: BacktestConfig,
                 failed: list[str] | None = None, signal_cache: dict | None = None,
                 progress: Progress | None = None) -> CategoryResult:
    data: CategoryData = prepare_category(name, tickers, prices, cfg, failed, signal_cache)
    slots, chunk = plan_chunks(cfg.capital, len(data.tickers), cfg)
    runs: dict[float, RunResult] = {}
    targets = cfg.targets()
    for i, tgt in enumerate(targets):
        if progress and i % 5 == 0:
            progress(i / len(targets), f"{name}: testing {tgt:.1f}% target")
        if len(data.dates) == 0 or len(data.tickers) == 0:
            continue
        runs[tgt] = simulate(data, tgt, cfg, slots, chunk)
    sweep = pd.DataFrame([r.stats for r in runs.values()])
    if not sweep.empty:
        # A single best target can be luck; the average with its ±0.2 % neighbours shows stable zones.
        sweep.insert(2, STABLE_COL, sweep["Final Amount (₹)"].rolling(5, center=True, min_periods=3).mean())
    return CategoryResult(
        name=name, tickers=data.tickers, failed=data.failed, slots=slots, chunk=chunk,
        first_date=data.dates[0] if len(data.dates) else None,
        last_date=data.dates[-1] if len(data.dates) else None,
        sweep=sweep, runs=runs,
    )


def run_backtest(cfg: BacktestConfig, categories: list[str] | None = None, refresh: bool = False,
                 progress: Progress | None = None, universe: dict[str, list[str]] | None = None) -> BacktestResult:
    universe = universe or CATEGORIES
    categories = categories or list(universe)
    tickers = list(dict.fromkeys(t for c in categories for t in universe[c]))
    prices, failed = load_prices(tickers, refresh=refresh,
                                 progress=(lambda f, m: progress(0.25 * f, m)) if progress else None)
    signal_cache: dict = {}
    results: dict[str, CategoryResult] = {}
    for i, cat in enumerate(categories):
        def sub(f: float, m: str, i=i) -> None:
            if progress:
                progress(0.25 + 0.75 * (i + f) / len(categories), m)
        results[cat] = run_category(cat, universe[cat], prices, cfg, failed, signal_cache, sub)
    ends = [r.last_date for r in results.values() if r.last_date is not None]
    if progress:
        progress(1.0, "Done")
    return BacktestResult(config=cfg, categories=results, failed=failed, data_end=max(ends) if ends else None)


# ───────────────────────── tables used by the app and the Excel report ─────────────────────────

def overview_table(result: BacktestResult) -> pd.DataFrame:
    rows = []
    cap = result.config.capital
    for name, cr in result.categories.items():
        if cr.sweep.empty:
            continue
        best = cr.sweep.loc[cr.sweep["Final Amount (₹)"].idxmax()]
        worst = cr.sweep.loc[cr.sweep["Final Amount (₹)"].idxmin()]
        rows.append({
            "Category": name,
            "Stocks": len(cr.tickers),
            "Chunks": cr.slots,
            "₹ per Chunk": cr.chunk,
            "Best Target %": best["Target %"],
            "Final Amount (₹)": best["Final Amount (₹)"],
            "Net Profit (₹)": best["Net Profit (₹)"],
            "Return %": best["Return %"],
            "CAGR %": best["CAGR %"],
            "Max Drawdown %": best["Max Drawdown %"],
            "Trades": int(best["Trades"]),
            "Target Hits": int(best["Target Hits"]),
            "Hit Rate %": best["Hit Rate %"],
            "Most Stable Target %": cr.stable_target,
            "Worst Target %": worst["Target %"],
            "Final at Worst (₹)": worst["Final Amount (₹)"],
            "Starting Capital (₹)": cap,
        })
    return pd.DataFrame(rows)


def final_amount_matrix(result: BacktestResult) -> pd.DataFrame:
    """Rows = profit target, columns = category, values = final amount."""
    cols = {name: cr.sweep.set_index("Target %")["Final Amount (₹)"] for name, cr in result.categories.items()
            if not cr.sweep.empty}
    return pd.DataFrame(cols)


def whole_number_table(cr: CategoryResult) -> pd.DataFrame:
    """The 5, 6, 7, 8, 9, 10 % checkpoints the user asked about."""
    s = cr.sweep
    mask = np.isclose(s["Target %"] % 1, 0) | np.isclose(s["Target %"] % 1, 1)
    return s[mask].reset_index(drop=True)


def yearly_table(run: RunResult, capital: float) -> pd.DataFrame:
    eq = run.equity
    t = run.trades
    rows = []
    prev_end = capital
    last_year = int(eq.index[-1].year) if len(eq) else None
    for y in sorted(set(eq.index.year)):
        e = eq[eq.index.year == y]
        end = float(e.iloc[-1])
        pnl = end - prev_end
        in_year_exit = t["Exit Date"].dt.year == y
        closed = t["Result"] != "OPEN"
        peak = np.maximum.accumulate(np.concatenate([[prev_end], e.to_numpy()]))
        dd = float(((np.concatenate([[prev_end], e.to_numpy()]) / peak) - 1).min() * 100)
        rows.append({
            "Year": f"{y} (YTD)" if y == last_year and e.index[-1].month < 12 else str(y),
            "Start Equity (₹)": prev_end,
            "End Equity (₹)": end,
            "Year P&L (₹)": pnl,
            "Year Return %": pnl / prev_end * 100 if prev_end else 0.0,
            "Rolling Profit (₹)": end - capital,
            "Rolling Return %": (end / capital - 1) * 100,
            "Trades Opened": int((t["Entry Date"].dt.year == y).sum()),
            "Target Hits": int((in_year_exit & (t["Result"] == "TARGET")).sum()),
            "Trend Exits": int((in_year_exit & (t["Result"] == "EXIT")).sum()),
            "Booked P&L (₹)": float(t.loc[in_year_exit & closed, "Net P&L (₹)"].sum()),
            "Max Drawdown %": dd,
        })
        prev_end = end
    return pd.DataFrame(rows)


def year_target_matrix(cr: CategoryResult, capital: float) -> pd.DataFrame:
    """Rows = target %, columns = year, values = that year's return %."""
    out = {}
    for tgt, run in cr.runs.items():
        yt = yearly_table(run, capital)
        out[tgt] = yt.set_index("Year")["Year Return %"]
    df = pd.DataFrame(out).T
    df.index.name = "Target %"
    return df


def rolling_12m_return(run: RunResult) -> pd.Series:
    eq = run.equity
    prior = eq.reindex(eq.index - pd.DateOffset(years=1), method="ffill")
    prior.index = eq.index
    return ((eq / prior - 1) * 100).dropna().rename("Rolling 12-month return %")


def stock_summary(run: RunResult) -> pd.DataFrame:
    t = run.trades
    if t.empty:
        return pd.DataFrame(columns=["Stock", "Trades", "Target Hits", "Trend Exits", "Still Open",
                                     "Hit Rate %", "Net P&L (₹)", "Avg Days Held"])
    g = t.groupby("Stock")
    df = pd.DataFrame({
        "Trades": g.size(),
        "Target Hits": g["Result"].apply(lambda r: int((r == "TARGET").sum())),
        "Trend Exits": g["Result"].apply(lambda r: int((r == "EXIT").sum())),
        "Still Open": g["Result"].apply(lambda r: int((r == "OPEN").sum())),
        "Net P&L (₹)": g["Net P&L (₹)"].sum(),
        "Avg Days Held": g["Days Held"].mean(),
    })
    df.insert(4, "Hit Rate %", df["Target Hits"] / df["Trades"] * 100)
    return df.sort_values("Net P&L (₹)", ascending=False).reset_index()


def stock_target_hits(cr: CategoryResult) -> pd.DataFrame:
    """Rows = stock, columns = target %, values = number of times the target was hit."""
    out = {}
    for tgt, run in cr.runs.items():
        t = run.trades
        out[tgt] = t[t["Result"] == "TARGET"].groupby("Stock").size()
    names = [display_name(x) for x in cr.tickers]
    df = pd.DataFrame(out).reindex(names).fillna(0).astype(int)
    df.index.name = "Stock"
    return df


def unaffordable_table(cr: CategoryResult) -> pd.DataFrame:
    run = cr.run()
    rows = [(display_name(t), v[0], v[1]) for t, v in run.unaffordable.items()]
    return pd.DataFrame(rows, columns=["Stock", "Signals Skipped", "Lowest Price Seen (₹)"]).sort_values(
        "Lowest Price Seen (₹)", ascending=False)
