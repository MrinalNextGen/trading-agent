"""Settings loaded from the .env file in the repo root."""
from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
CACHE_DIR = ROOT / "data_cache"
REPORTS_DIR = ROOT / "reports"


def load_env() -> None:
    """(Re)load .env so values saved while the app runs are picked up."""
    load_dotenv(ENV_PATH, override=True)


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class BacktestConfig:
    strategy: str = "dma"          # "dma" (50/100/200 DMA trend) or "car" (cumulative-average recovery)
    start_year: int = 2018         # test runs from 1 Jan of this year up to the latest price
    capital: float = 100_000.0     # ₹ starting money for EACH category
    chunk_min: float = 5_000.0     # equal chunk per trade must stay inside this range…
    chunk_max: float = 9_000.0
    chunk_size: float | None = None  # …or pin one exact chunk size (None = auto)
    target_min: float = 5.0        # profit-target sweep, in %
    target_max: float = 10.0
    target_step: float = 0.1
    charges_pct: float = 0.1       # % of trade value per side (STT, stamp, exchange)
    charges_flat: float = 16.0     # ₹ per sell (DP charge)
    fill_mode: str = "close"       # "close" = target hit when the day CLOSES above it (like hello.ipynb)
                                   # "high"  = GTT/limit sell, filled when the day's HIGH touches it
    compounding: bool = False      # True = chunk grows/shrinks with booked profit
    car_lookback_years: int = 10   # hello.ipynb finds the high inside a 10-year download

    @classmethod
    def from_env(cls, **overrides) -> "BacktestConfig":
        load_env()
        base = cls(
            start_year=int(env_float("BACKTEST_START_YEAR", cls.start_year)),
            capital=env_float("BACKTEST_CAPITAL", cls.capital),
            chunk_min=env_float("BACKTEST_CHUNK_MIN", cls.chunk_min),
            chunk_max=env_float("BACKTEST_CHUNK_MAX", cls.chunk_max),
            target_min=env_float("BACKTEST_TARGET_MIN", cls.target_min),
            target_max=env_float("BACKTEST_TARGET_MAX", cls.target_max),
            target_step=env_float("BACKTEST_TARGET_STEP", cls.target_step),
            charges_pct=env_float("BACKTEST_CHARGES_PCT", cls.charges_pct),
            charges_flat=env_float("BACKTEST_CHARGES_FLAT", cls.charges_flat),
        )
        clean = {k: v for k, v in overrides.items() if v is not None}
        return replace(base, **clean)

    def targets(self) -> list[float]:
        step = max(self.target_step, 0.01)
        n = int(math.floor((self.target_max - self.target_min) / step + 1e-9)) + 1
        return [round(self.target_min + i * step, 2) for i in range(max(n, 1))]

    def as_dict(self) -> dict:
        return asdict(self)
