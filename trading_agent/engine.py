"""Portfolio simulator: one category, one profit target, equal ₹ chunks per trade.

Rules (kept identical to hello.ipynb where the notebook defines them):
* Signals are read at the day's close and acted on at that close.
* A stock holds at most one position; a stock sold today can be bought again tomorrow.
* Capital is cut into equal chunks (₹5k–9k by default). Each trade uses one chunk and
  buys whole shares only, so a share costing more than a chunk can never be bought.
* If more stocks signal than there are free chunks, they are filled in list order.
* Open positions at the end are valued at the last close ("net total amount").
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .settings import BacktestConfig
from .strategies import compute_signals
from .universe import display_name


@dataclass
class CategoryData:
    name: str
    tickers: list[str]                 # tickers with data, in list order
    failed: list[str]                  # tickers with no price data
    dates: pd.DatetimeIndex            # trading days inside the test window
    close: np.ndarray                  # [days, stocks], NaN when the stock did not trade
    lists: tuple = field(repr=False, default=())   # fast row-wise Python lists for the loop
    entry_lists: list = field(repr=False, default_factory=list)
    listed_from: dict = field(default_factory=dict)  # first price date per ticker
    signals: dict = field(repr=False, default_factory=dict)  # full-history signal frames


def plan_chunks(capital: float, n_stocks: int, cfg: BacktestConfig) -> tuple[int, float]:
    """Number of equal chunks and ₹ per chunk.

    Auto mode picks as many chunks as there are stocks, but never a chunk below
    chunk_min or above chunk_max. ₹1,00,000 with 50 stocks -> 20 chunks of ₹5,000.
    """
    if cfg.chunk_size:
        return max(1, int(capital // cfg.chunk_size)), float(cfg.chunk_size)
    lo = max(1, math.ceil(capital / cfg.chunk_max))
    hi = max(1, math.floor(capital / cfg.chunk_min))
    slots = max(lo, min(max(n_stocks, 1), hi))
    return slots, capital / slots


def prepare_category(name: str, tickers: list[str], prices: dict[str, pd.DataFrame],
                     cfg: BacktestConfig, failed: list[str] | None = None,
                     signal_cache: dict | None = None) -> CategoryData:
    start = pd.Timestamp(year=cfg.start_year, month=1, day=1)
    have = [t for t in tickers if t in prices and prices[t] is not None and len(prices[t])]
    missing = [t for t in tickers if t not in have]

    frames, sigs = {}, {}
    for t in have:
        key = (cfg.strategy, cfg.car_lookback_years, t, len(prices[t]), prices[t].index[-1])
        if signal_cache is not None and key in signal_cache:
            sig = signal_cache[key]
        else:
            sig = compute_signals(cfg.strategy, prices[t], cfg.car_lookback_years)
            if signal_cache is not None:
                signal_cache[key] = sig
        sigs[t] = sig
        frames[t] = prices[t]

    dates = pd.DatetimeIndex([])
    for t in have:
        dates = dates.union(frames[t].index[frames[t].index >= start])
    n_d, n_s = len(dates), len(have)

    def grid(col: str, src: dict) -> np.ndarray:
        if n_s == 0:
            return np.empty((n_d, 0))
        return np.column_stack([src[t][col].reindex(dates).to_numpy(dtype=float) for t in have])

    close = grid("Close", frames)
    high = grid("High", frames)
    opn = grid("Open", frames)
    entry = np.column_stack([sigs[t]["entry"].reindex(dates, fill_value=False).to_numpy(dtype=bool) for t in have]) if n_s else np.zeros((n_d, 0), bool)
    exit_ = np.column_stack([sigs[t]["exit"].reindex(dates, fill_value=False).to_numpy(dtype=bool) for t in have]) if n_s else np.zeros((n_d, 0), bool)
    entry &= ~np.isnan(close)

    has_exit = bool(exit_.any())
    exit_rows = exit_.tolist() if has_exit else [None] * n_d
    entry_lists = [np.flatnonzero(row).tolist() for row in entry]
    return CategoryData(
        name=name, tickers=have, failed=sorted(set(missing) | set(failed or []) & set(tickers)),
        dates=dates, close=close,
        lists=(close.tolist(), high.tolist(), opn.tolist(), exit_rows),
        entry_lists=entry_lists,
        listed_from={t: frames[t].index[0] for t in have},
        signals=sigs,
    )


@dataclass
class RunResult:
    target: float
    equity: pd.Series
    invested: pd.Series
    trades: pd.DataFrame
    stats: dict
    unaffordable: dict  # ticker -> (signal days skipped, lowest price seen)


TRADE_COLUMNS = ["Stock", "Entry Date", "Entry Price", "Qty", "Invested (₹)", "Target Price",
                 "Exit Date", "Exit Price", "Result", "Days Held", "Gross P&L (₹)", "Charges (₹)",
                 "Net P&L (₹)", "Return %"]


def simulate(data: CategoryData, target_pct: float, cfg: BacktestConfig,
             slots: int, chunk: float) -> RunResult:
    close_l, high_l, open_l, exit_l = data.lists
    entry_lists = data.entry_lists
    n_days = len(data.dates)
    pct = cfg.charges_pct / 100.0
    flat = cfg.charges_flat
    mult = 1.0 + target_pct / 100.0
    use_high = cfg.fill_mode == "high"
    capital = float(cfg.capital)

    cash = capital
    realized = 0.0
    pos: dict[int, list] = {}  # s -> [qty, entry_px, target_px, entry_day, cost_incl_charges, last_px, buy_charges]
    trades: list[tuple] = []
    equity = np.empty(n_days)
    invested = np.empty(n_days)
    full_days = 0
    unaffordable: dict[int, list] = {}

    for d in range(n_days):
        crow = close_l[d]
        sold_today: set[int] = set()
        if pos:
            erow = exit_l[d]
            hrow = high_l[d]
            orow = open_l[d]
            to_sell = []
            for s, p in pos.items():
                c = crow[s]
                if c != c:  # no trade today for this stock
                    continue
                p[5] = c
                if use_high:
                    h = hrow[s]
                    if h >= p[2]:
                        o = orow[s]
                        to_sell.append((s, o if o >= p[2] else p[2], "TARGET"))
                        continue
                elif c >= p[2]:
                    to_sell.append((s, c, "TARGET"))
                    continue
                if erow is not None and erow[s]:
                    to_sell.append((s, c, "EXIT"))
            for s, px, reason in to_sell:
                qty, epx, tgt, eday, cost, _, bch = pos.pop(s)
                value = qty * px
                sch = value * pct + flat
                proceeds = value - sch
                pnl = proceeds - cost
                cash += proceeds
                realized += pnl
                trades.append((s, eday, epx, qty, cost, tgt, d, px, reason, qty * (px - epx), bch + sch, pnl))
                sold_today.add(s)

        cands = entry_lists[d]
        if cands and len(pos) < slots:
            cur_chunk = chunk if not cfg.compounding else max(0.0, (capital + realized) / slots)
            for s in cands:
                if len(pos) >= slots:
                    break
                if s in pos or s in sold_today:
                    continue
                px = crow[s]
                qty = int(cur_chunk // (px * (1.0 + pct)))
                if qty <= 0:
                    u = unaffordable.setdefault(s, [0, px])
                    u[0] += 1
                    u[1] = min(u[1], px)
                    continue
                bch = qty * px * pct
                cost = qty * px + bch
                if cost > cash + 1e-9:
                    continue
                cash -= cost
                pos[s] = [qty, px, px * mult, d, cost, px, bch]

        mv = 0.0
        for p in pos.values():
            mv += p[0] * p[5]
        equity[d] = cash + mv
        invested[d] = mv
        if len(pos) >= slots:
            full_days += 1

    last = n_days - 1
    for s, p in pos.items():  # still open at the end: marked to the last close
        qty, epx, tgt, eday, cost, lpx, bch = p
        trades.append((s, eday, epx, qty, cost, tgt, last, lpx, "OPEN", qty * (lpx - epx), bch, qty * lpx - cost))

    dates = data.dates
    names = [display_name(t) for t in data.tickers]
    rows = []
    for s, eday, epx, qty, cost, tgt, xday, xpx, reason, gross, ch, net in trades:
        rows.append((names[s], dates[eday], epx, qty, cost, tgt, dates[xday], xpx, reason,
                     int((dates[xday] - dates[eday]).days), gross, ch, net, net / cost * 100 if cost else 0.0))
    trades_df = pd.DataFrame(rows, columns=TRADE_COLUMNS).sort_values(["Entry Date", "Stock"]).reset_index(drop=True)

    eq = pd.Series(equity, index=dates, name="Equity")
    inv = pd.Series(invested, index=dates, name="Invested")
    stats = _stats(eq, inv, trades_df, capital, target_pct, full_days, n_days)
    return RunResult(target=target_pct, equity=eq, invested=inv, trades=trades_df, stats=stats,
                     unaffordable={data.tickers[s]: tuple(v) for s, v in unaffordable.items()})


def _stats(eq: pd.Series, inv: pd.Series, trades: pd.DataFrame, capital: float,
           target_pct: float, full_days: int, n_days: int) -> dict:
    final = float(eq.iloc[-1]) if len(eq) else capital
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9) if len(eq) > 1 else 1.0
    peak = eq.cummax()
    dd = (eq / peak - 1.0).min() * 100 if len(eq) else 0.0
    res = trades["Result"] if len(trades) else pd.Series(dtype=str)
    hits = int((res == "TARGET").sum())
    exits = int((res == "EXIT").sum())
    open_ = int((res == "OPEN").sum())
    total = len(trades)
    closed = trades[res != "OPEN"] if total else trades
    hit_days = trades.loc[res == "TARGET", "Days Held"] if total else pd.Series(dtype=float)
    return {
        "Target %": target_pct,
        "Final Amount (₹)": final,
        "Net Profit (₹)": final - capital,
        "Return %": (final / capital - 1) * 100,
        "CAGR %": ((final / capital) ** (1 / years) - 1) * 100 if final > 0 else -100.0,
        "Max Drawdown %": float(dd),
        "Trades": total,
        "Target Hits": hits,
        "Trend Exits": exits,
        "Still Open": open_,
        "Hit Rate %": hits / total * 100 if total else 0.0,
        "Avg Days to Target": float(hit_days.mean()) if len(hit_days) else float("nan"),
        "Booked P&L (₹)": float(closed["Net P&L (₹)"].sum()) if len(closed) else 0.0,
        "Open P&L (₹)": float(trades.loc[res == "OPEN", "Net P&L (₹)"].sum()) if total else 0.0,
        "Charges (₹)": float(trades["Charges (₹)"].sum()) if total else 0.0,
        "Avg Capital Used %": float((inv / eq.where(eq > 0)).mean() * 100) if len(eq) else 0.0,
        "Days All Chunks Busy %": full_days / max(n_days, 1) * 100,
    }
