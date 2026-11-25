"""Tables for the Live market page (kept out of app.py so they can be tested)."""
from __future__ import annotations

import pandas as pd

from .strategies import compute_signals

LIVE_COLUMNS = ["Symbol", "LTP", "Change %", "5m High", "5m Low", "5m Candle", "Day High", "Day Low",
                "DMA signal", "vs 200 DMA %", "CAR signal", "CAR streak (days)", "Volume"]


def strategy_status_table(prices: dict[str, pd.DataFrame], tickers) -> pd.DataFrame:
    """Today's DMA / CAR state for each stock, from daily closes."""
    rows = []
    for tk in tickers:
        df = prices.get(tk)
        if df is None or len(df) < 2:
            continue
        d = compute_signals("dma", df)
        c = compute_signals("car", df)
        consec = int(c["consec_days"].iloc[-1])
        dma200 = d["dma200"].iloc[-1]
        rows.append({
            "Ticker": tk,
            "Daily Close": float(df["Close"].iloc[-1]),
            "As of": df.index[-1],
            "DMA signal": "BUY zone" if d["entry"].iloc[-1] else ("Bear – exit" if d["exit"].iloc[-1] else "–"),
            "200 DMA": float(dma200) if pd.notna(dma200) else None,
            "CAR streak (days)": consec,
            "CAR signal": "BUY SIGNAL" if consec > 0 and consec % 10 == 0 else
                          (f"in {10 - consec % 10} days" if consec > 0 else "falling"),
        })
    return pd.DataFrame(rows, columns=["Ticker", "Daily Close", "As of", "DMA signal", "200 DMA",
                                       "CAR streak (days)", "CAR signal"])


def build_live_table(quotes: pd.DataFrame, status: pd.DataFrame) -> pd.DataFrame:
    """Live quotes (with 5-minute bars) joined to the strategy state."""
    table = quotes.merge(status, on="Ticker", how="left")
    table["vs 200 DMA %"] = (pd.to_numeric(table["LTP"], errors="coerce") /
                             pd.to_numeric(table["200 DMA"], errors="coerce") - 1) * 100
    for col in LIVE_COLUMNS:
        if col not in table:
            table[col] = None
    return table[LIVE_COLUMNS]
