"""Run the profit-target backtest from the command line and save the Excel report.

Examples
  python run_backtest.py                          # defaults from .env (₹1 lakh, 2018 → today, DMA)
  python run_backtest.py --start-year 2015 --capital 200000
  python run_backtest.py --strategy car --categories "Nifty 50" "My Portfolio"
  python run_backtest.py --strategy both --fill high --compound
"""
from __future__ import annotations

import argparse
import sys

from trading_agent.backtest import overview_table, run_backtest, whole_number_table
from trading_agent.excel_report import default_report_name, write_excel
from trading_agent.settings import REPORTS_DIR, BacktestConfig
from trading_agent.strategies import STRATEGIES
from trading_agent.universe import CATEGORIES


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--strategy", choices=["dma", "car", "both"], default="dma")
    p.add_argument("--start-year", type=int, help="first year of the test (default: BACKTEST_START_YEAR in .env)")
    p.add_argument("--capital", type=float, help="₹ starting money per category (default: BACKTEST_CAPITAL = 100000)")
    p.add_argument("--chunk", type=float, help="fix the ₹ per trade instead of the automatic 5k–9k chunk")
    p.add_argument("--targets", nargs=3, type=float, metavar=("MIN", "MAX", "STEP"), help="e.g. 5 10 0.1")
    p.add_argument("--categories", nargs="+", choices=list(CATEGORIES), metavar="NAME",
                   help=f"subset of: {', '.join(CATEGORIES)}")
    p.add_argument("--fill", choices=["close", "high"], help="close = like the notebook, high = GTT/limit order")
    p.add_argument("--compound", action="store_true", help="re-invest booked profit")
    p.add_argument("--refresh", action="store_true", help="re-download prices even if cached today")
    p.add_argument("--out", help="output .xlsx path (default: reports/…)")
    a = p.parse_args(argv)

    strategies = ["dma", "car"] if a.strategy == "both" else [a.strategy]
    for strat in strategies:
        overrides = dict(strategy=strat, start_year=a.start_year, capital=a.capital, chunk_size=a.chunk,
                         fill_mode=a.fill, compounding=a.compound or None)
        if a.targets:
            overrides.update(target_min=a.targets[0], target_max=a.targets[1], target_step=a.targets[2])
        cfg = BacktestConfig.from_env(**overrides)

        def progress(f: float, msg: str) -> None:
            print(f"\r[{f * 100:5.1f}%] {msg:<60}", end="", flush=True)

        print(f"\n=== {STRATEGIES[strat].name} | from {cfg.start_year} | ₹{cfg.capital:,.0f} ===")
        result = run_backtest(cfg, a.categories, refresh=a.refresh, progress=progress)
        print()
        ov = overview_table(result)
        print(ov[["Category", "Chunks", "₹ per Chunk", "Best Target %", "Final Amount (₹)", "Return %", "CAGR %",
                  "Trades", "Target Hits", "Hit Rate %"]].round(2).to_string(index=False))
        for name, cr in result.categories.items():
            wn = whole_number_table(cr)[["Target %", "Final Amount (₹)", "Target Hits", "Trend Exits", "Still Open"]]
            print(f"\n{name} — whole-number targets:\n{wn.round(0).to_string(index=False)}")
        if result.failed:
            print("\nNo Yahoo data for:", ", ".join(result.failed))
        out = a.out if (a.out and len(strategies) == 1) else REPORTS_DIR / default_report_name(result)
        print("\nExcel report:", write_excel(result, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
