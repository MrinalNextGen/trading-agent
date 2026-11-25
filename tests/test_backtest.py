"""Checks the backtest against a line-by-line copy of the hello.ipynb logic.

Run with:  python -m pytest -q
"""
import math

import numpy as np
import pandas as pd
import pytest

from trading_agent.backtest import run_category, yearly_table
from trading_agent.engine import plan_chunks, prepare_category, simulate
from trading_agent.settings import BacktestConfig
from trading_agent.strategies import car_consecutive_days


def random_walk(seed: int, n: int = 4200, start: str = "2008-01-01", drift: float = 0.0004) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.018, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.01, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.01, n)))
    opn = np.clip(close * (1 + rng.normal(0, 0.005, n)), low, high)
    return pd.DataFrame({"Open": opn, "High": high, "Low": low, "Close": close}, index=dates)


def notebook_dma_trades(df: pd.DataFrame, start: pd.Timestamp, target_mult: float):
    """process_nifty_segment's loop, verbatim, but starting flat at `start`."""
    df = df.copy()
    df["50 DMA"] = df["Close"].rolling(window=50).mean()
    df["100 DMA"] = df["Close"].rolling(window=100).mean()
    df["200 DMA"] = df["Close"].rolling(window=200).mean()
    in_trade, target_p = False, 0
    events = []
    for i in range(len(df)):
        if df.index[i] < start:
            continue
        p = float(df["Close"].iloc[i])
        m50, m100, m200 = float(df["50 DMA"].iloc[i]), float(df["100 DMA"].iloc[i]), float(df["200 DMA"].iloc[i])
        if pd.isna(m200):
            continue
        is_bull = (p > m50 > m100 > m200) and (p < 1.1 * m200)
        is_bear = (p < m50 < m100 < m200)
        if not in_trade:
            if is_bull:
                in_trade, target_p = True, p * target_mult
                events.append(("BUY", df.index[i]))
        else:
            if p >= target_p:
                events.append(("PROFIT", df.index[i]))
                in_trade = False
            elif is_bear:
                events.append(("EXIT", df.index[i]))
                in_trade = False
    return events


def notebook_car_consec(df_full: pd.DataFrame) -> int:
    """process_nifty_car_final on a 10-year download; returns today's Consec_Days."""
    ath_price = df_full["Close"].max()
    ath_date = df_full[df_full["Close"] == ath_price].index[0]
    df = df_full.loc[ath_date:].copy()
    df["CA"] = df["Close"].expanding().mean()
    df["CA_Diff"] = df["CA"].diff()
    count = 0
    for diff in df["CA_Diff"]:
        count = count + 1 if diff > 0 else 0
    return count


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_dma_matches_notebook_for_single_stock(seed):
    df = random_walk(seed)
    cfg = BacktestConfig(strategy="dma", start_year=2012, capital=1e9, chunk_size=1e6,
                         charges_pct=0, charges_flat=0)
    data = prepare_category("one", ["X"], {"X": df}, cfg)
    run = simulate(data, 6.28, cfg, slots=1, chunk=1e6)
    expected = notebook_dma_trades(df, pd.Timestamp("2012-01-01"), 1.0628)

    got = []
    for _, t in run.trades.iterrows():
        got.append(("BUY", t["Entry Date"]))
        if t["Result"] != "OPEN":
            got.append(("PROFIT" if t["Result"] == "TARGET" else "EXIT", t["Exit Date"]))
    assert got == expected
    assert len(expected) > 10


@pytest.mark.parametrize("seed", [11, 12, 13])
def test_car_streak_matches_notebook_on_every_sampled_day(seed):
    df = random_walk(seed, n=5200, start="2004-01-01", drift=0.0001)
    consec = car_consecutive_days(df["Close"], lookback_years=10)
    rng = np.random.default_rng(seed)
    for i in sorted(rng.choice(np.arange(300, len(df)), size=60, replace=False)):
        day = df.index[i]
        window = df.loc[day - pd.DateOffset(years=10):day]
        assert consec.iloc[i] == notebook_car_consec(window), day


def test_auto_chunks_stay_in_range():
    cfg = BacktestConfig()
    assert plan_chunks(100_000, 50, cfg) == (20, 5000.0)
    slots, chunk = plan_chunks(100_000, 12, cfg)
    assert slots == 12 and 5000 <= chunk <= 9000
    slots, chunk = plan_chunks(100_000, 5, cfg)          # few stocks: still never above ₹9k
    assert slots == math.ceil(100_000 / 9000) and chunk <= 9000
    assert plan_chunks(100_000, 50, BacktestConfig(chunk_size=8000)) == (12, 8000.0)


def test_money_adds_up_and_whole_shares_only():
    prices = {f"S{i}": random_walk(100 + i) for i in range(8)}
    prices["PRICEY"] = random_walk(200) * 100  # ~₹10,000+ a share: never fits a ₹5k chunk
    cfg = BacktestConfig(strategy="dma", start_year=2012, capital=50_000)
    cr = run_category("t", list(prices), prices, cfg)
    for tgt, run in cr.runs.items():
        t = run.trades
        assert (t["Qty"] == t["Qty"].round()).all() and (t["Qty"] > 0).all()
        assert (t["Invested (₹)"] <= cr.chunk + 1e-6).all()
        assert "PRICEY" not in set(t["Stock"])
        # final amount = capital + booked P&L + open P&L
        assert run.stats["Final Amount (₹)"] == pytest.approx(
            cfg.capital + run.stats["Booked P&L (₹)"] + run.stats["Open P&L (₹)"], rel=1e-9)
        hits = t[t["Result"] == "TARGET"]
        assert (hits["Exit Price"] >= hits["Target Price"] - 1e-9).all()
    assert "PRICEY" in cr.run().unaffordable


def test_more_chunks_never_exceeded_and_yearly_rollup():
    prices = {f"S{i}": random_walk(300 + i) for i in range(30)}
    cfg = BacktestConfig(strategy="car", start_year=2012, capital=30_000)  # 6 chunks of ₹5k
    cr = run_category("t", list(prices), prices, cfg)
    run = cr.run(7.0)
    t = run.trades
    for day in run.equity.index[::25]:
        open_on_day = ((t["Entry Date"] <= day) & ((t["Exit Date"] > day) | (t["Result"] == "OPEN"))).sum()
        assert open_on_day <= cr.slots
    yt = yearly_table(run, cfg.capital)
    assert yt["Year P&L (₹)"].sum() == pytest.approx(run.stats["Net Profit (₹)"])
    assert yt["Rolling Profit (₹)"].iloc[-1] == pytest.approx(run.stats["Net Profit (₹)"])


def test_target_grid_has_fifty_one_steps():
    t = BacktestConfig().targets()
    assert len(t) == 51 and t[0] == 5.0 and t[1] == 5.1 and t[-1] == 10.0
