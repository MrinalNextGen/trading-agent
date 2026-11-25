"""The two strategies from hello.ipynb, turned into day-by-day buy/exit signals.

Both are computed only from data available up to each day (no peeking ahead),
so they can be replayed in a backtest.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class StrategyInfo:
    key: str
    name: str
    buy_rule: str
    exit_rule: str


STRATEGIES = {
    "dma": StrategyInfo(
        key="dma",
        name="DMA Trend (50/100/200)",
        buy_rule="Close > 50 DMA > 100 DMA > 200 DMA and Close < 1.10 × 200 DMA (process_nifty_segment)",
        exit_rule="Sell at the profit target, or exit when Close < 50 DMA < 100 DMA < 200 DMA",
    ),
    "car": StrategyInfo(
        key="car",
        name="CAR – Cumulative Average Recovery",
        buy_rule="Average price since the 10-year high has risen 10, 20, 30… days in a row (process_nifty_car_final)",
        exit_rule="Sell at the profit target only (the notebook has no exit rule)",
    ),
}


def dma_signals(df: pd.DataFrame) -> pd.DataFrame:
    """process_nifty_segment: bullish stack near the 200 DMA = BUY, bearish stack = EXIT."""
    close = df["Close"]
    m50 = close.rolling(50).mean()
    m100 = close.rolling(100).mean()
    m200 = close.rolling(200).mean()
    bull = (close > m50) & (m50 > m100) & (m100 > m200) & (close < 1.1 * m200)
    bear = (close < m50) & (m50 < m100) & (m100 < m200)
    return pd.DataFrame({"entry": bull.fillna(False), "exit": bear.fillna(False),
                         "dma50": m50, "dma100": m100, "dma200": m200}, index=df.index)


def car_consecutive_days(close: pd.Series, lookback_years: int = 10) -> pd.Series:
    """Consecutive days the cumulative average (since the highest close of the last
    `lookback_years`) has risen — exactly what hello.ipynb shows when run on each day."""
    c = close.to_numpy(dtype=float)
    n = len(c)
    dates = close.index
    window_start = np.searchsorted(dates.values, (dates - pd.DateOffset(years=lookback_years)).values, side="left")
    csum = np.concatenate([[0.0], np.cumsum(c)])

    consec = np.zeros(n, dtype=np.int64)
    dq: deque[int] = deque()  # indices with decreasing closes -> front is the earliest window max
    prev_ath = -1
    for t in range(n):
        while dq and c[dq[-1]] < c[t]:
            dq.pop()
        dq.append(t)
        while dq[0] < window_start[t]:
            dq.popleft()
        a = dq[0]
        if a == t:
            consec[t] = 0
        elif a == prev_ath:
            ca_prev = (csum[t] - csum[a]) / (t - a)          # mean of closes a..t-1
            consec[t] = consec[t - 1] + 1 if c[t] > ca_prev else 0  # CA rises iff today > previous CA
        else:  # the high rolled out of the window: rebuild the streak from the new high
            seg = c[a:t + 1]
            ca = np.cumsum(seg) / np.arange(1, len(seg) + 1)
            up = np.diff(ca) > 0
            rev = up[::-1]
            consec[t] = len(rev) if rev.all() else int(np.argmin(rev))
        prev_ath = a
    return pd.Series(consec, index=close.index)


def car_signals(df: pd.DataFrame, lookback_years: int = 10) -> pd.DataFrame:
    """process_nifty_car_final: BUY SIGNAL on day 10, 20, 30… of a rising streak. No exit rule."""
    consec = car_consecutive_days(df["Close"], lookback_years)
    entry = (consec > 0) & (consec % 10 == 0)
    return pd.DataFrame({"entry": entry, "exit": False, "consec_days": consec}, index=df.index)


def compute_signals(strategy: str, df: pd.DataFrame, car_lookback_years: int = 10) -> pd.DataFrame:
    if strategy == "dma":
        return dma_signals(df)
    if strategy == "car":
        return car_signals(df, car_lookback_years)
    raise ValueError(f"unknown strategy {strategy!r}")
