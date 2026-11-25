"""Daily price history from Yahoo Finance, cached as CSV in data_cache/."""
from __future__ import annotations

import re
import time
from typing import Callable, Iterable

import pandas as pd
import yfinance as yf

from .settings import CACHE_DIR
from .universe import yahoo_symbol

FIELDS = ["Open", "High", "Low", "Close"]


def _cache_path(ticker: str):
    return CACHE_DIR / (re.sub(r"[^A-Za-z0-9._-]", "_", ticker) + ".csv")


def _clean(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or df.empty or "Close" not in df:
        return None
    df = df[[c for c in FIELDS if c in df.columns]].copy()
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.index = pd.to_datetime(df.index).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.dropna(subset=["Close"])
    df = df[df["Close"] > 0]
    for col in ("Open", "High", "Low"):  # today's partial bar can miss these
        if col in df:
            df[col] = df[col].fillna(df["Close"])
    return df if len(df) else None


def _read_cache(ticker: str) -> pd.DataFrame | None:
    path = _cache_path(ticker)
    if not path.exists():
        return None
    try:
        return _clean(pd.read_csv(path, index_col=0, parse_dates=True))
    except Exception:
        return None


def _is_fresh(ticker: str, max_age_hours: float) -> bool:
    path = _cache_path(ticker)
    return path.exists() and (time.time() - path.stat().st_mtime) < max_age_hours * 3600


def _download_batch(tickers: list[str]) -> dict[str, pd.DataFrame | None]:
    symbols = [yahoo_symbol(t) for t in tickers]
    out: dict[str, pd.DataFrame | None] = {}
    try:
        raw = yf.download(symbols, period="max", interval="1d", auto_adjust=True,
                          group_by="ticker", threads=True, progress=False)
    except Exception:
        raw = None
    for t, sym in zip(tickers, symbols):
        df = None
        if raw is not None and not raw.empty:
            try:
                df = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                df = None
        df = _clean(df)
        if df is None:  # one retry through the single-ticker endpoint
            try:
                df = _clean(yf.Ticker(sym).history(period="max", interval="1d", auto_adjust=True))
            except Exception:
                df = None
        out[t] = df
    return out


def load_prices(tickers: Iterable[str], refresh: bool = False, max_age_hours: float = 12,
                progress: Callable[[float, str], None] | None = None) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Return ({ticker: OHLC DataFrame}, [tickers that could not be downloaded])."""
    CACHE_DIR.mkdir(exist_ok=True)
    tickers = list(dict.fromkeys(tickers))
    prices: dict[str, pd.DataFrame] = {}
    to_fetch: list[str] = []
    for t in tickers:
        cached = None if refresh or not _is_fresh(t, max_age_hours) else _read_cache(t)
        if cached is not None:
            prices[t] = cached
        else:
            to_fetch.append(t)

    failed: list[str] = []
    batch_size = 40
    for i in range(0, len(to_fetch), batch_size):
        batch = to_fetch[i:i + batch_size]
        if progress:
            progress(i / max(len(to_fetch), 1), f"Downloading prices {i + 1}-{i + len(batch)} of {len(to_fetch)}…")
        for t, df in _download_batch(batch).items():
            if df is not None:
                df.to_csv(_cache_path(t))
                prices[t] = df
            else:
                stale = _read_cache(t)  # keep yesterday's data rather than nothing
                if stale is not None:
                    prices[t] = stale
                else:
                    failed.append(t)
    if progress:
        progress(1.0, "Prices ready")
    return prices, failed
