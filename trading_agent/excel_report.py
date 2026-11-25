"""Detailed Excel report with native (editable) Excel charts."""
from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import (BacktestResult, CategoryResult, final_amount_matrix, overview_table, stock_summary,
                       stock_target_hits, unaffordable_table, whole_number_table, year_target_matrix,
                       yearly_table)
from .strategies import STRATEGIES
from .universe import SHORT_NAMES, display_name

# Palette (validated categorical slots + neutral chrome)
BLUE, ORANGE, AQUA = "#257de0", "#f47230", "#00b894"
MUTED = "#9dc8f7"
INK, INK2, GRID = "#191919", "#666666", "#e7e7e7"
GOOD_BG, BAD_BG, OPEN_BG, BEST_BG = "#C6EFCE", "#FFC7CE", "#FFF2CC", "#cce3fd"

SWEEP_COLS = ["Target %", "Final Amount (₹)", "Avg of ±0.2% Targets (₹)", "Net Profit (₹)", "Return %", "CAGR %", "Max Drawdown %",
              "Trades", "Target Hits", "Trend Exits", "Still Open", "Hit Rate %", "Avg Days to Target",
              "Booked P&L (₹)", "Open P&L (₹)", "Charges (₹)", "Avg Capital Used %", "Days All Chunks Busy %"]


class _Fmt:
    def __init__(self, wb):
        base = {"font_name": "Calibri", "font_size": 10}
        f = lambda **kw: wb.add_format({**base, **kw})  # noqa: E731
        self.title = f(bold=True, font_size=16, font_color=INK)
        self.subtitle = f(italic=True, font_color=INK2)
        self.h2 = f(bold=True, font_size=12, font_color=INK)
        self.header = f(bold=True, font_color="#ffffff", bg_color="#1b64be", border=1, border_color="#ffffff",
                        text_wrap=True, valign="vcenter", align="center")
        self.label = f(bold=True, font_color=INK2)
        self.text = f()
        self.wrap = f(text_wrap=True, valign="top")
        self.money = f(num_format='"₹"#,##0')
        self.price = f(num_format="#,##0.00")
        self.pct = f(num_format='0.00"%"')
        self.num = f(num_format="0")
        self.dec1 = f(num_format="0.0")
        self.date = f(num_format="dd-mmm-yyyy")
        self.target = f(num_format='0.0"%"', bold=True)
        self.best = f(bg_color=BEST_BG, bold=True)
        self.good = f(bg_color=GOOD_BG)
        self.bad = f(bg_color=BAD_BG)
        self.open = f(bg_color=OPEN_BG)
        self.link = f(font_color=BLUE, underline=1)
        self.big_money = f(num_format='"₹"#,##0', bold=True, font_size=14, font_color=INK)
        self.big_pct = f(num_format='0.0"%"', bold=True, font_size=14, font_color=INK)


def _col_format(fmt: _Fmt, name: str):
    if name == "Target %" or name.endswith("Target %"):
        return fmt.target
    if "₹" in name and "Price" not in name:
        return fmt.money
    if "Price" in name:
        return fmt.price
    if "%" in name:
        return fmt.pct
    if "Date" in name:
        return fmt.date
    if name.startswith("Avg Days"):
        return fmt.dec1
    if name in ("Trades", "Target Hits", "Trend Exits", "Still Open", "Qty", "Days Held", "Stocks", "Chunks",
                "Trades Opened", "Signals Skipped"):
        return fmt.num
    return fmt.text


def _cell(ws, r, c, v, f):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        ws.write_blank(r, c, None, f)
    elif isinstance(v, (pd.Timestamp, datetime)):
        ws.write_datetime(r, c, pd.Timestamp(v).to_pydatetime(), f)
    elif isinstance(v, (np.integer,)):
        ws.write_number(r, c, int(v), f)
    elif isinstance(v, (np.floating, float, int)) and not isinstance(v, bool):
        ws.write_number(r, c, float(v), f)
    else:
        ws.write(r, c, v, f)


def _write_df(ws, fmt: _Fmt, df: pd.DataFrame, row: int, col: int, widths: dict | None = None,
              autofilter: bool = False, value_fmt=None) -> tuple[int, int]:
    """Write a header + rows. Returns (first data row, last data row).
    value_fmt, if given, formats every column except the first (used for matrices)."""
    ws.set_row(row, 30)
    for j, name in enumerate(df.columns):
        ws.write(row, col + j, str(name), fmt.header)
    col_fmts = [_col_format(fmt, str(n)) for n in df.columns]
    if value_fmt is not None:
        col_fmts = col_fmts[:1] + [value_fmt] * (len(col_fmts) - 1)
    for i, rec in enumerate(df.itertuples(index=False), start=1):
        for j, v in enumerate(rec):
            _cell(ws, row + i, col + j, v, col_fmts[j])
    if widths:
        for j, name in enumerate(df.columns):
            ws.set_column(col + j, col + j, widths.get(name, widths.get("*", 12)))
    if autofilter and len(df):
        ws.autofilter(row, col, row + len(df), col + len(df.columns) - 1)
    return row + 1, row + len(df)


def _style_chart(chart, title: str, x_title: str, y_title: str, x_fmt: str | None = None,
                 y_fmt: str | None = None, legend: bool = False):
    axis_font = {"color": INK2, "size": 9}
    chart.set_title({"name": title, "name_font": {"size": 12, "bold": True, "color": INK}})
    chart.set_x_axis({"name": x_title, "name_font": {"size": 9, "bold": False, "color": INK2}, "num_font": axis_font,
                      "line": {"color": "#c2c2c2"}, "major_gridlines": {"visible": True, "line": {"color": GRID}},
                      **({"num_format": x_fmt} if x_fmt else {})})
    chart.set_y_axis({"name": y_title, "name_font": {"size": 9, "bold": False, "color": INK2}, "num_font": axis_font,
                      "line": {"color": "#c2c2c2"}, "major_gridlines": {"visible": False},
                      **({"num_format": y_fmt} if y_fmt else {})})
    chart.set_chartarea({"border": {"none": True}})
    chart.set_plotarea({"border": {"none": True}})
    chart.set_legend({"position": "top", "font": {"color": INK2, "size": 9}} if legend else {"none": True})


def _sheet_names(name: str) -> dict:
    short = SHORT_NAMES.get(name, name.replace(" ", "")[:20])
    return {k: f"{short} {k}"[:31] for k in ("Sweep", "Yearly", "Trades")}


def _summary_sheet(wb, fmt: _Fmt, result: BacktestResult):
    cfg = result.config
    info = STRATEGIES[cfg.strategy]
    ws = wb.add_worksheet("Summary")
    ws.hide_gridlines(2)
    ws.set_column(0, 0, 24)
    ws.set_column(1, 1, 16)
    ws.set_column(2, 20, 13)
    ws.write(0, 0, f"Profit-Target Backtest — {info.name}", fmt.title)
    end = result.data_end.strftime("%d %b %Y") if result.data_end is not None else "-"
    ws.write(1, 0, f"1 Jan {cfg.start_year} → {end}  ·  ₹{cfg.capital:,.0f} per category  ·  "
                   f"targets {cfg.target_min:.1f}% → {cfg.target_max:.1f}% in {cfg.target_step:.1f}% steps "
                   f"({len(cfg.targets())} tests per category)", fmt.subtitle)

    params = [
        ("Buy rule", info.buy_rule),
        ("Exit rule", info.exit_rule),
        ("Target check", "Day's CLOSE ≥ target, sold at that close (same as hello.ipynb)" if cfg.fill_mode == "close"
         else "Day's HIGH ≥ target, sold at the target price (GTT / limit order)"),
        ("Position size", f"Capital split into equal chunks of ₹{cfg.chunk_min:,.0f}–₹{cfg.chunk_max:,.0f}"
         + (f" (fixed ₹{cfg.chunk_size:,.0f})" if cfg.chunk_size else " (auto: one chunk per stock while it fits)")
         + "; whole shares only; one position per stock"),
        ("Profits", "Re-invested (chunk grows with booked profit)" if cfg.compounding else
         "Kept as cash (chunk size stays fixed)"),
        ("Charges", f"{cfg.charges_pct:.2f}% of value per side + ₹{cfg.charges_flat:,.0f} per sell"),
        ("Prices", "Yahoo Finance daily, split/dividend adjusted (yfinance)"),
        ("Generated", datetime.now().strftime("%d %b %Y %H:%M")),
    ]
    r = 3
    for k, v in params:
        ws.write(r, 0, k, fmt.label)
        ws.write(r, 1, v, fmt.text)
        r += 1

    r += 1
    ws.write(r, 0, "Best profit target per category (highest final amount)", fmt.h2)
    ov = overview_table(result)
    first, last = _write_df(ws, fmt, ov, r + 1, 0)
    for i, name in enumerate(ov["Category"]):
        ws.write_url(first + i, 0, f"internal:'{_sheet_names(name)['Sweep']}'!A1", fmt.link, name)
    ws.conditional_format(first, 5, last, 5, {"type": "data_bar", "bar_color": MUTED, "bar_solid": True})

    chart = wb.add_chart({"type": "bar"})
    chart.add_series({
        "name": "Final amount at best target",
        "categories": ["Summary", first, 0, last, 0],
        "values": ["Summary", first, 5, last, 5],
        "fill": {"color": BLUE}, "border": {"none": True}, "gap": 60,
        "data_labels": {"value": True, "num_format": '"₹"#,##0', "font": {"color": INK, "size": 9}},
    })
    _style_chart(chart, "Final amount at each category's best target", "Final amount (₹)", "", x_fmt='"₹"#,##0')
    chart.set_y_axis({"reverse": True, "num_font": {"color": INK2, "size": 9}, "line": {"color": "#c2c2c2"}})
    chart.set_size({"width": 760, "height": 60 + 46 * len(ov)})
    ws.insert_chart(last + 2, 0, chart)

    notes_row = last + 4 + int((60 + 46 * len(ov)) / 20)
    ws.write(notes_row, 0, "Read this before trusting the numbers", fmt.h2)
    notes = [
        "Stock lists are TODAY's index members, so stocks that crashed out of the index are missing (survivorship bias) — real results would likely be lower.",
        "Each category is tested on its own with the full starting capital.",
        "Open positions at the end are valued at the last close; 'Still Open' trades have not hit their target yet.",
        "Whole shares only: while one share costs more than a chunk, that stock's signals are skipped (see the Trades sheets).",
        "Past performance does not guarantee future returns. This is a research tool, not investment advice.",
    ]
    for i, n in enumerate(notes):
        ws.write(notes_row + 1 + i, 0, f"• {n}", fmt.text)
    if result.failed:
        ws.write(notes_row + 2 + len(notes), 0,
                 "No price data on Yahoo for: " + ", ".join(display_name(t) for t in result.failed), fmt.subtitle)


def _all_targets_sheet(wb, fmt: _Fmt, result: BacktestResult):
    ws = wb.add_worksheet("All Targets")
    ws.hide_gridlines(2)
    ws.write(0, 0, "Final amount for every profit target × category", fmt.title)
    ws.write(1, 0, "Highlighted cell = best target in that column. Same numbers as the bar charts on each Sweep sheet.",
             fmt.subtitle)
    m = final_amount_matrix(result).reset_index()
    m.columns = ["Target %"] + [f"{c} (₹)" for c in m.columns[1:]]
    first, last = _write_df(ws, fmt, m, 3, 0, widths={"*": 17, "Target %": 10})
    for j in range(1, len(m.columns)):
        col_letter = _col_letter(j)
        ws.conditional_format(first, j, last, j, {"type": "2_color_scale", "min_color": "#ffffff",
                                                  "max_color": "#9dc8f7"})
        ws.conditional_format(first, j, last, j, {
            "type": "formula", "criteria": f"={col_letter}{first + 1}=MAX({col_letter}${first + 1}:{col_letter}${last + 1})",
            "format": fmt.best})
    ws.freeze_panes(first, 1)


def _col_letter(idx: int) -> str:
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def _sweep_sheet(wb, fmt: _Fmt, cr: CategoryResult, cfg, names: dict):
    ws = wb.add_worksheet(names["Sweep"])
    ws.hide_gridlines(2)
    best = cr.sweep.loc[cr.sweep["Final Amount (₹)"].idxmax()]
    ws.write(0, 0, f"{cr.name} — profit-target sweep", fmt.title)
    ws.write(1, 0, f"{len(cr.tickers)} stocks · {cr.slots} chunks × ₹{cr.chunk:,.0f} · ₹{cfg.capital:,.0f} start · "
                   f"{cr.first_date:%d %b %Y} → {cr.last_date:%d %b %Y}", fmt.subtitle)
    ws.write_url(1, 12, f"internal:'{names['Yearly']}'!A1", fmt.link, "Year-wise →")
    ws.write_url(1, 14, f"internal:'{names['Trades']}'!A1", fmt.link, "Trades & stocks →")
    ws.write_url(1, 17, "internal:'Summary'!A1", fmt.link, "← Summary")

    # headline tiles
    tiles = [("Best target", best["Target %"], fmt.big_pct), ("Final amount", best["Final Amount (₹)"], fmt.big_money),
             ("Net profit", best["Net Profit (₹)"], fmt.big_money), ("CAGR", best["CAGR %"], fmt.big_pct),
             ("Hit rate", best["Hit Rate %"], fmt.big_pct), ("Max drawdown", best["Max Drawdown %"], fmt.big_pct)]
    for i, (lab, val, f) in enumerate(tiles):
        ws.write(3, i * 2, lab, fmt.label)
        _cell(ws, 4, i * 2, val, f)

    # 5, 6, 7, 8, 9, 10 checkpoints
    ws.write(6, 0, "Whole-number checkpoints (5 % → 10 %)", fmt.h2)
    wn = whole_number_table(cr)[["Target %", "Final Amount (₹)", "Net Profit (₹)", "Return %", "Trades",
                                 "Target Hits", "Trend Exits", "Still Open", "Hit Rate %", "Avg Days to Target"]]
    f1, l1 = _write_df(ws, fmt, wn, 7, 0)
    ws.conditional_format(f1, 0, l1, len(wn.columns) - 1, {
        "type": "formula", "criteria": f"=$B{f1 + 1}=MAX($B${f1 + 1}:$B${l1 + 1})", "format": fmt.best})

    # full sweep
    top = l1 + 3
    ws.write(top, 0, f"All {len(cr.sweep)} targets (best row highlighted)", fmt.h2)
    sw = cr.sweep[SWEEP_COLS]
    f2, l2 = _write_df(ws, fmt, sw, top + 1, 0, autofilter=True)
    ws.conditional_format(f2, 0, l2, len(SWEEP_COLS) - 1, {
        "type": "formula", "criteria": f"=$B{f2 + 1}=MAX($B${f2 + 1}:$B${l2 + 1})", "format": fmt.best})
    ws.set_column(0, 0, 10)
    ws.set_column(1, len(SWEEP_COLS) - 1, 12.5)
    ws.freeze_panes(f2, 1)

    # chart: x = net total amount at the end, y = profit target (as requested).
    # A connected dot plot, because the amounts don't start at zero (bars would exaggerate gaps).
    n = len(sw)
    sheet = names["Sweep"]
    chart = wb.add_chart({"type": "scatter", "subtype": "straight_with_markers"})
    chart.add_series({
        "name": "Final amount",
        "categories": [sheet, f2, 1, l2, 1],      # x = final amount
        "values": [sheet, f2, 0, l2, 0],          # y = target %
        "line": {"color": MUTED, "width": 1.5},
        "marker": {"type": "circle", "size": 5, "fill": {"color": MUTED}, "border": {"color": MUTED}},
    })
    chart.add_series({                             # the best target, drawn on top
        "name": f"Best: {best['Target %']:.1f}%",
        "categories": [sheet, 4, 2, 4, 2],         # tile cell: final amount
        "values": [sheet, 4, 0, 4, 0],             # tile cell: best target
        "line": {"none": True},
        "marker": {"type": "circle", "size": 10, "fill": {"color": BLUE}, "border": {"color": "#ffffff"}},
        "data_labels": {"series_name": True, "position": "right", "font": {"color": INK, "size": 9, "bold": True}},
    })
    lo = float(cr.sweep["Final Amount (₹)"].min())
    hi = float(cr.sweep["Final Amount (₹)"].max())
    pad = max((hi - lo) * 0.08, 500.0)
    _style_chart(chart, f"Net total amount by profit target — best {best['Target %']:.1f}%",
                 "Net total amount at the end (₹)", "Profit target (%)")
    chart.set_x_axis({"name": "Net total amount at the end (₹)", "num_format": '"₹"#,##0',
                      "min": math.floor((lo - pad) / 1000) * 1000, "max": math.ceil((hi + pad) / 1000) * 1000,
                      "major_gridlines": {"visible": True, "line": {"color": GRID}},
                      "num_font": {"color": INK2, "size": 9}, "name_font": {"size": 9, "color": INK2},
                      "line": {"color": "#c2c2c2"}})
    chart.set_y_axis({"name": "Profit target (%)", "num_format": '0.0"%"', "min": cfg.target_min,
                      "max": cfg.target_max, "major_unit": 0.5,
                      "major_gridlines": {"visible": True, "line": {"color": GRID}},
                      "num_font": {"color": INK2, "size": 9}, "name_font": {"size": 9, "color": INK2},
                      "line": {"color": "#c2c2c2"}})
    chart.set_legend({"none": True})
    chart.set_size({"width": 620, "height": max(520, 12 * n + 90)})
    chart_col = len(SWEEP_COLS) + 1            # right of the widest table, so nothing is covered
    ws.insert_chart(3, chart_col, chart)

    # chart: what happened to the trades at each target
    chart2 = wb.add_chart({"type": "bar", "subtype": "stacked"})
    for col_name, color in (("Target Hits", BLUE), ("Trend Exits", ORANGE), ("Still Open", AQUA)):
        if cfg.strategy == "car" and col_name == "Trend Exits":
            continue
        j = SWEEP_COLS.index(col_name)
        chart2.add_series({"name": col_name, "categories": [names["Sweep"], f2, 0, l2, 0],
                           "values": [names["Sweep"], f2, j, l2, j], "fill": {"color": color},
                           "border": {"color": "#ffffff", "width": 0.75}, "gap": 40})
    _style_chart(chart2, "Which trades hit the target", "Number of trades", "Profit target (%)",
                 y_fmt='0.0"%"', legend=True)
    chart2.set_size({"width": 520, "height": max(520, 12 * n + 90)})
    ws.insert_chart(3, chart_col + 10, chart2)


def _yearly_sheet(wb, fmt: _Fmt, cr: CategoryResult, cfg, names: dict):
    ws = wb.add_worksheet(names["Yearly"])
    ws.hide_gridlines(2)
    run = cr.run()
    ws.write(0, 0, f"{cr.name} — year-wise performance at the best target ({run.target:.1f}%)", fmt.title)
    ws.write(1, 0, "Rolling profit = total profit from the start up to the end of that year. "
                   "Year P&L includes the change in value of open positions.", fmt.subtitle)
    ws.write_url(1, 14, f"internal:'{names['Sweep']}'!A1", fmt.link, "← Sweep")
    yt = yearly_table(run, cfg.capital)
    f1, l1 = _write_df(ws, fmt, yt, 3, 0, widths={"*": 13, "Year": 11})
    ws.conditional_format(f1, 3, l1, 3, {"type": "cell", "criteria": ">=", "value": 0, "format": fmt.good})
    ws.conditional_format(f1, 3, l1, 3, {"type": "cell", "criteria": "<", "value": 0, "format": fmt.bad})

    c1 = wb.add_chart({"type": "column"})
    c1.add_series({"name": "Year P&L", "categories": [names["Yearly"], f1, 0, l1, 0],
                   "values": [names["Yearly"], f1, 3, l1, 3], "fill": {"color": BLUE},
                   "invert_if_negative": True, "invert_if_negative_color": "#ec4647", "gap": 60})
    _style_chart(c1, "Profit / loss in each year", "", "₹", y_fmt='"₹"#,##0')
    c1.set_y_axis({"num_format": '"₹"#,##0', "major_gridlines": {"visible": True, "line": {"color": GRID}},
                   "num_font": {"color": INK2, "size": 9}, "line": {"none": True}})
    c1.set_x_axis({"num_font": {"color": INK2, "size": 9}, "line": {"color": "#c2c2c2"}})
    c1.set_size({"width": 620, "height": 300})
    ws.insert_chart(l1 + 2, 0, c1)

    c2 = wb.add_chart({"type": "line"})
    c2.add_series({"name": "Rolling profit", "categories": [names["Yearly"], f1, 0, l1, 0],
                   "values": [names["Yearly"], f1, 5, l1, 5], "line": {"color": BLUE, "width": 2},
                   "marker": {"type": "circle", "size": 6, "fill": {"color": BLUE}, "border": {"color": "#ffffff"}}})
    _style_chart(c2, "Rolling (cumulative) profit at each year end", "", "₹", y_fmt='"₹"#,##0')
    c2.set_y_axis({"num_format": '"₹"#,##0', "major_gridlines": {"visible": True, "line": {"color": GRID}},
                   "num_font": {"color": INK2, "size": 9}, "line": {"none": True}})
    c2.set_x_axis({"num_font": {"color": INK2, "size": 9}, "line": {"color": "#c2c2c2"}})
    c2.set_size({"width": 620, "height": 300})
    ws.insert_chart(l1 + 2, 8, c2)

    # month-end equity curve
    top = l1 + 19
    ws.write(top, 0, "Month-end account value (cash + open positions)", fmt.h2)
    me = run.equity.resample("ME").last().dropna()
    inv = run.invested.resample("ME").last().reindex(me.index)
    eq_df = pd.DataFrame({"Month End Date": me.index, "Account Value (₹)": me.values,
                          "In Stocks (₹)": inv.values, "Cash (₹)": me.values - inv.values})
    f2, l2 = _write_df(ws, fmt, eq_df, top + 1, 0)
    c3 = wb.add_chart({"type": "line"})
    c3.add_series({"name": "Account value", "categories": [names["Yearly"], f2, 0, l2, 0],
                   "values": [names["Yearly"], f2, 1, l2, 1], "line": {"color": BLUE, "width": 2}})
    _style_chart(c3, "Account value over time", "", "₹", y_fmt='"₹"#,##0')
    c3.set_x_axis({"date_axis": True, "num_format": "yyyy", "major_unit": 1, "major_unit_type": "years",
                   "num_font": {"color": INK2, "size": 9}, "line": {"color": "#c2c2c2"},
                   "major_gridlines": {"visible": False}})
    c3.set_y_axis({"num_format": '"₹"#,##0', "major_gridlines": {"visible": True, "line": {"color": GRID}},
                   "num_font": {"color": INK2, "size": 9}, "line": {"none": True}})
    c3.set_size({"width": 900, "height": 320})
    ws.insert_chart(top + 1, 5, c3)

    # year × target matrix
    top2 = max(l2 + 3, top + 20)
    ws.write(top2, 0, "Return % in each year for every profit target (blue = gain, red = loss)", fmt.h2)
    m = year_target_matrix(cr, cfg.capital).reset_index()
    f3, l3 = _write_df(ws, fmt, m, top2 + 1, 0, value_fmt=fmt.pct)
    ws.conditional_format(f3, 1, l3, len(m.columns) - 1, {
        "type": "3_color_scale", "min_color": "#ec4647", "mid_type": "num", "mid_value": 0, "mid_color": "#efefef",
        "max_color": "#257de0"})
    ws.set_column(0, 0, 13)


def _trades_sheet(wb, fmt: _Fmt, cr: CategoryResult, cfg, names: dict):
    ws = wb.add_worksheet(names["Trades"])
    ws.hide_gridlines(2)
    run = cr.run()
    ws.write(0, 0, f"{cr.name} — every trade at the best target ({run.target:.1f}%)", fmt.title)
    ws.write(1, 0, "Green = target hit · Red = trend exit · Yellow = still open (valued at last close)", fmt.subtitle)
    ws.write_url(1, 10, f"internal:'{names['Sweep']}'!A1", fmt.link, "← Sweep")
    t = run.trades.copy()
    f1, l1 = _write_df(ws, fmt, t, 3, 0, widths={"*": 12, "Stock": 14, "Result": 9}, autofilter=True)
    res_col = _col_letter(list(t.columns).index("Result"))
    last_col = len(t.columns) - 1
    for val, f in (("TARGET", fmt.good), ("EXIT", fmt.bad), ("OPEN", fmt.open)):
        ws.conditional_format(f1, 0, l1, last_col, {"type": "formula", "criteria": f'=${res_col}{f1 + 1}="{val}"',
                                                    "format": f})
    ws.freeze_panes(f1, 1)

    c0 = len(t.columns) + 1
    ws.write(3, c0, "Per stock", fmt.h2)
    ss = stock_summary(run)
    f2, l2 = _write_df(ws, fmt, ss, 4, c0, widths={"*": 11, "Stock": 14})
    if len(ss):
        ws.conditional_format(f2, c0 + 6, l2, c0 + 6, {"type": "cell", "criteria": ">=", "value": 0, "format": fmt.good})
        ws.conditional_format(f2, c0 + 6, l2, c0 + 6, {"type": "cell", "criteria": "<", "value": 0, "format": fmt.bad})

    un = unaffordable_table(cr)
    top = l2 + 3
    if len(un):
        ws.write(top, c0, f"Signals skipped: one share cost more than the ₹{cr.chunk:,.0f} chunk", fmt.h2)
        _, l3 = _write_df(ws, fmt, un, top + 1, c0)
        top = l3 + 3

    ws.write(top, c0, "Target hits per stock at every profit target", fmt.h2)
    hits = stock_target_hits(cr).reset_index()
    hits.columns = ["Stock"] + [f"{c:.1f}%" for c in hits.columns[1:]]
    f4, l4 = _write_df(ws, fmt, hits, top + 1, c0, value_fmt=fmt.num)
    ws.conditional_format(f4, c0 + 1, l4, c0 + len(hits.columns) - 1, {
        "type": "2_color_scale", "min_color": "#ffffff", "max_color": "#519ced"})
    ws.set_column(c0 + 8, c0 + len(hits.columns) - 1, 6)


def write_excel(result: BacktestResult, path):
    """path can be a file path or a BytesIO (used by the Streamlit download button)."""
    if isinstance(path, (str, Path)):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="xlsxwriter") as xw:
        wb = xw.book
        fmt = _Fmt(wb)
        _summary_sheet(wb, fmt, result)
        _all_targets_sheet(wb, fmt, result)
        for name, cr in result.categories.items():
            if cr.sweep.empty:
                continue
            names = _sheet_names(name)
            _sweep_sheet(wb, fmt, cr, result.config, names)
            _yearly_sheet(wb, fmt, cr, result.config, names)
            _trades_sheet(wb, fmt, cr, result.config, names)
    return path


def default_report_name(result: BacktestResult) -> str:
    cfg = result.config
    end = result.data_end.year if result.data_end is not None else datetime.now().year
    return f"Backtest_{cfg.strategy.upper()}_{cfg.start_year}-{end}_{datetime.now():%Y%m%d_%H%M}.xlsx"
