"""Plotly charts for the Streamlit app (validated palette, light + dark)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"

THEMES = {
    "light": dict(surface="#ffffff", ink="#191919", ink2="#666666", grid="#e7e7e7", base="#c2c2c2",
                  s1="#257de0", s2="#f47230", s3="#00b894", muted="#9dc8f7", neg="#ec4647", mid="#efefef",
                  seq=["#f4f9ff", "#b6d5f8", "#6aabf1", "#257de0", "#1757ac", "#0e3d7e"]),
    "dark": dict(surface="#191919", ink="#efefef", ink2="#9c9c9c", grid="#303030", base="#474747",
                 s1="#338ced", s2="#e76624", s3="#00a888", muted="#1b64be", neg="#eb6263", mid="#383838",
                 seq=["#1d232b", "#0f4a96", "#1b64be", "#338ced", "#84b9f3", "#cce3fd"]),
}


def inr(x: float | None, decimals: int = 0) -> str:
    """₹1,48,258 — Indian digit grouping."""
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "–"
    neg = x < 0
    s = f"{abs(x):.{decimals}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{'-' if neg else ''}₹{whole}{'.' + frac if frac else ''}"


def inr_short(x: float | None) -> str:
    """₹1.48 L / ₹2.10 Cr — compact Indian units for headline numbers."""
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "–"
    a = abs(x)
    sign = "-" if x < 0 else ""
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:.2f} L"
    return inr(x)


def _inr_ticks(lo: float, hi: float, n: int = 6) -> tuple[list[float], list[str]]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / max(n - 1, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=10 * mag)
    start = math.floor(lo / step) * step
    vals = [start + i * step for i in range(int((hi - start) / step) + 2)]
    return vals, [inr(v) for v in vals]


def _base(fig: go.Figure, t: dict, height: int, title: str | None = None, legend: bool = False) -> go.Figure:
    fig.update_layout(
        template="none", height=height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=t["ink2"], size=12),
        title=dict(text=title, font=dict(size=15, color=t["ink"]), x=0, xanchor="left", y=0.99, yanchor="top")
        if title else None,
        margin=dict(l=10, r=24, t=52 if title else 16, b=10),
        hoverlabel=dict(bgcolor=t["surface"], bordercolor=t["base"], font=dict(family=FONT, color=t["ink"], size=12)),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0, font=dict(color=t["ink2"]),
                    bgcolor="rgba(0,0,0,0)"),
        barcornerradius=4,
    )
    fig.update_xaxes(gridcolor=t["grid"], linecolor=t["base"], zeroline=False, ticks="", automargin=True,
                     title_font=dict(color=t["ink2"], size=12), tickfont=dict(color=t["ink2"]))
    fig.update_yaxes(gridcolor=t["grid"], linecolor=t["base"], zeroline=False, ticks="", automargin=True,
                     title_font=dict(color=t["ink2"], size=12), tickfont=dict(color=t["ink2"]))
    return fig


def sweep_chart(sweep: pd.DataFrame, t: dict, selected: float | None = None, title: str | None = None,
                height: int | None = None) -> go.Figure:
    """x = net total amount at the end, y = profit target. Best target highlighted."""
    s = sweep.sort_values("Target %")
    best = s.loc[s["Final Amount (₹)"].idxmax()]
    custom = np.stack([s["Net Profit (₹)"].map(inr), s["Target Hits"], s["Trades"], s["Hit Rate %"],
                       s["Final Amount (₹)"].map(inr)], axis=-1)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=s["Final Amount (₹)"], y=s["Target %"], mode="lines+markers", name="Final amount",
        line=dict(color=t["muted"], width=2), marker=dict(size=8, color=t["muted"], line=dict(color=t["surface"], width=2)),
        customdata=custom,
        hovertemplate="<b>Target %{y:.1f}%</b><br>Final amount %{customdata[4]}<br>Profit %{customdata[0]}"
                      "<br>Hits %{customdata[1]} of %{customdata[2]} trades (%{customdata[3]:.0f}%)<extra></extra>"))
    if selected is not None and abs(selected - best["Target %"]) > 1e-9:
        sel = s.iloc[(s["Target %"] - selected).abs().argmin()]
        fig.add_trace(go.Scatter(
            x=[sel["Final Amount (₹)"]], y=[sel["Target %"]], mode="markers+text", name="Selected",
            marker=dict(size=14, color="rgba(0,0,0,0)", line=dict(color=t["ink"], width=2)),
            text=[f"selected {sel['Target %']:.1f}%"], textposition="middle left",
            textfont=dict(color=t["ink2"], size=12), hoverinfo="skip"))
    fig.add_trace(go.Scatter(
        x=[best["Final Amount (₹)"]], y=[best["Target %"]], mode="markers+text", name="Best",
        marker=dict(size=14, color=t["s1"], line=dict(color=t["surface"], width=2)),
        text=[f"  best {best['Target %']:.1f}%"], textposition="middle right",
        textfont=dict(color=t["ink"], size=13), hoverinfo="skip"))
    lo, hi = float(s["Final Amount (₹)"].min()), float(s["Final Amount (₹)"].max())
    pad = max((hi - lo) * 0.06, 500)
    tv, tt = _inr_ticks(lo - pad, hi + pad, 5)
    span = (hi - lo) + 2 * pad
    fig = _base(fig, t, height or max(480, 11 * len(s) + 120), title)
    fig.update_xaxes(title_text="Net total amount at the end (₹)", tickvals=tv, ticktext=tt,
                     range=[lo - pad, hi + pad + span * 0.18], showgrid=True)
    fig.update_yaxes(title_text="Profit target (%)", ticksuffix="%", dtick=0.5, showgrid=True,
                     range=[s["Target %"].min() - 0.15, s["Target %"].max() + 0.15])
    return fig


def overview_small_multiples(sweeps: dict[str, pd.DataFrame], t: dict, cols: int = 3) -> go.Figure:
    """One small panel per category (same axes) — x = final amount, y = target."""
    names = list(sweeps)
    rows = math.ceil(len(names) / cols)
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=names,
                        horizontal_spacing=0.06, vertical_spacing=0.16 if rows > 1 else 0.05)
    lo = min(float(s["Final Amount (₹)"].min()) for s in sweeps.values())
    hi = max(float(s["Final Amount (₹)"].max()) for s in sweeps.values())
    ylo = min(float(s["Target %"].min()) for s in sweeps.values())
    yhi = max(float(s["Target %"].max()) for s in sweeps.values())
    for i, name in enumerate(names):
        s = sweeps[name].sort_values("Target %")
        r, c = i // cols + 1, i % cols + 1
        best = s.loc[s["Final Amount (₹)"].idxmax()]
        fig.add_trace(go.Scatter(
            x=s["Final Amount (₹)"], y=s["Target %"], mode="lines+markers",
            line=dict(color=t["muted"], width=1.5), marker=dict(size=5, color=t["muted"]),
            customdata=s["Final Amount (₹)"].map(inr),
            hovertemplate=f"<b>{name}</b><br>Target %{{y:.1f}}%<br>Final %{{customdata}}<extra></extra>"), r, c)
        fig.add_trace(go.Scatter(
            x=[best["Final Amount (₹)"]], y=[best["Target %"]], mode="markers",
            marker=dict(size=11, color=t["s1"], line=dict(color=t["surface"], width=2)),
            hovertemplate=f"<b>{name} best</b><br>Target {best['Target %']:.1f}%<br>Final {inr(best['Final Amount (₹)'])}<extra></extra>"),
            r, c)
        fig.add_annotation(x=best["Final Amount (₹)"], y=best["Target %"], xref=f"x{i + 1 if i else ''}",
                           yref=f"y{i + 1 if i else ''}", text=f"{best['Target %']:.1f}%", showarrow=False,
                           xanchor="right", xshift=-9, font=dict(color=t["ink"], size=11))
    fig = _base(fig, t, 300 * rows + 40)
    fig.update_annotations(font_color=t["ink"], selector=dict(xref="paper"))
    tv, tt = _inr_ticks(lo, hi, 4)
    pad = max((hi - lo) * 0.06, 500)
    # identical explicit ranges on every panel, so the panels compare like-for-like
    fig.update_xaxes(tickvals=tv, ticktext=tt, range=[lo - pad, hi + pad], showgrid=True)
    fig.update_yaxes(ticksuffix="%", dtick=1, range=[ylo - 0.25, yhi + 0.25], showgrid=True)
    for i in range(len(names)):
        if i % cols:
            fig.update_yaxes(showticklabels=False, row=i // cols + 1, col=i % cols + 1)
    return fig


def outcomes_chart(sweep: pd.DataFrame, t: dict, has_exits: bool, height: int | None = None) -> go.Figure:
    """Stacked bars: how many trades hit the target / exited on trend / are still open."""
    s = sweep.sort_values("Target %")
    fig = go.Figure()
    parts = [("Target Hits", "Hit the target", t["s1"])]
    if has_exits:
        parts.append(("Trend Exits", "Trend exit", t["s2"]))
    parts.append(("Still Open", "Still open", t["s3"]))
    for col, label, color in parts:
        fig.add_trace(go.Bar(y=s["Target %"], x=s[col], orientation="h", name=label,
                             marker=dict(color=color, line=dict(color=t["surface"], width=1)),
                             hovertemplate=f"Target %{{y:.1f}}%<br>{label}: %{{x}}<extra></extra>"))
    fig = _base(fig, t, height or max(480, 11 * len(s) + 120), "Which trades hit the target", legend=True)
    fig.update_layout(barmode="stack", bargap=0.18, legend=dict(y=1.005), margin=dict(t=78))
    fig.update_xaxes(title_text="Number of trades", showgrid=True)
    fig.update_yaxes(title_text="Profit target (%)", ticksuffix="%", dtick=0.5, showgrid=False)
    return fig


def yearly_pnl_chart(yt: pd.DataFrame, t: dict) -> go.Figure:
    colors = [t["s1"] if v >= 0 else t["neg"] for v in yt["Year P&L (₹)"]]
    fig = go.Figure(go.Bar(
        x=yt["Year"], y=yt["Year P&L (₹)"], marker=dict(color=colors, line=dict(width=0)),
        customdata=np.stack([yt["Year P&L (₹)"].map(inr), yt["Year Return %"], yt["Target Hits"]], axis=-1),
        hovertemplate="<b>%{x}</b><br>P&L %{customdata[0]} (%{customdata[1]:.1f}%)<br>Target hits %{customdata[2]}<extra></extra>"))
    fig = _base(fig, t, 330, "Profit / loss in each year")
    fig.update_layout(bargap=0.35)
    lo, hi = float(min(0, yt["Year P&L (₹)"].min())), float(max(0, yt["Year P&L (₹)"].max()))
    tv, tt = _inr_ticks(lo, hi, 5)
    fig.update_yaxes(tickvals=tv, ticktext=tt, showgrid=True, zeroline=True, zerolinecolor=t["base"])
    fig.update_xaxes(type="category", showgrid=False)
    return fig


def rolling_profit_chart(equity: pd.Series, capital: float, t: dict) -> go.Figure:
    prof = equity - capital
    ye = prof.groupby(prof.index.year).tail(1)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=prof.index, y=prof.values, mode="lines", line=dict(color=t["s1"], width=2),
                             customdata=[inr(v) for v in prof.values],
                             hovertemplate="%{x|%d %b %Y}<br>Profit so far %{customdata}<extra></extra>"))
    fig.add_trace(go.Scatter(x=ye.index, y=ye.values, mode="markers", marker=dict(size=8, color=t["s1"],
                             line=dict(color=t["surface"], width=2)),
                             customdata=[inr(v) for v in ye.values],
                             hovertemplate="Year end %{x|%Y}<br>Rolling profit %{customdata}<extra></extra>"))
    fig.add_annotation(x=prof.index[-1], y=prof.values[-1], text=f"{inr(prof.values[-1])}", showarrow=False,
                       xanchor="right", yanchor="bottom", yshift=8, font=dict(color=t["ink"], size=12))
    fig = _base(fig, t, 330, "Rolling profit (₹ gained since the start)")
    lo, hi = float(min(0, prof.min())), float(max(0, prof.max()))
    tv, tt = _inr_ticks(lo, hi, 5)
    fig.update_yaxes(tickvals=tv, ticktext=tt, showgrid=True, zeroline=True, zerolinecolor=t["base"])
    fig.update_xaxes(showgrid=False, range=[prof.index[0], prof.index[-1]])
    fig.update_layout(margin=dict(t=64))
    return fig


def rolling_return_chart(series: pd.Series, t: dict) -> go.Figure:
    fig = go.Figure(go.Scatter(x=series.index, y=series.values, mode="lines", line=dict(color=t["s1"], width=2),
                               hovertemplate="%{x|%d %b %Y}<br>Last 12 months %{y:.1f}%<extra></extra>"))
    fig = _base(fig, t, 300, "Rolling 12-month return")
    fig.update_yaxes(ticksuffix="%", showgrid=True, zeroline=True, zerolinecolor=t["base"])
    fig.update_xaxes(showgrid=False)
    return fig


def capital_use_chart(equity: pd.Series, invested: pd.Series, t: dict) -> go.Figure:
    pct = (invested / equity.where(equity > 0) * 100).fillna(0)
    fig = go.Figure(go.Scatter(x=pct.index, y=pct.values, mode="lines", line=dict(color=t["s1"], width=1.5),
                               fill="tozeroy", fillcolor="rgba(37,125,224,0.12)",
                               hovertemplate="%{x|%d %b %Y}<br>%{y:.0f}% of the account in stocks<extra></extra>"))
    fig = _base(fig, t, 260, "Share of the account invested")
    fig.update_yaxes(ticksuffix="%", range=[0, 100], showgrid=True)
    fig.update_xaxes(showgrid=False)
    return fig


def year_target_heatmap(matrix: pd.DataFrame, t: dict) -> go.Figure:
    m = matrix.copy()
    vmax = float(np.nanmax(np.abs(m.values))) if m.size else 1.0
    fig = go.Figure(go.Heatmap(
        z=m.values, x=[str(c) for c in m.columns], y=m.index.values, zmid=0, zmin=-vmax, zmax=vmax,
        colorscale=[[0, t["neg"]], [0.5, t["mid"]], [1, t["s1"]]], xgap=2, ygap=1,
        colorbar=dict(title=dict(text="Year return", font=dict(color=t["ink2"])), ticksuffix="%",
                      tickfont=dict(color=t["ink2"]), outlinewidth=0, thickness=12),
        hovertemplate="Target %{y:.1f}% · %{x}<br>Return %{z:.1f}%<extra></extra>"))
    fig = _base(fig, t, max(420, 11 * len(m) + 110), "Return in each year, for every profit target")
    fig.update_yaxes(title_text="Profit target (%)", ticksuffix="%", dtick=0.5, showgrid=False)
    fig.update_xaxes(type="category", showgrid=False, side="top")
    return fig


def stock_hits_heatmap(hits: pd.DataFrame, t: dict) -> go.Figure:
    h = hits.copy()
    h = h.loc[h.sum(axis=1).sort_values(ascending=True).index]
    seq = t["seq"]
    scale = [[i / (len(seq) - 1), c] for i, c in enumerate(seq)]
    fig = go.Figure(go.Heatmap(
        z=h.values, x=[f"{c:.1f}%" for c in h.columns], y=h.index, colorscale=scale, xgap=1, ygap=1,
        colorbar=dict(title=dict(text="Hits", font=dict(color=t["ink2"])), tickfont=dict(color=t["ink2"]),
                      outlinewidth=0, thickness=12),
        hovertemplate="%{y} at %{x}: %{z} target hits<extra></extra>"))
    fig = _base(fig, t, max(360, 16 * len(h) + 120), "Target hits per stock (most hits at the top)")
    fig.update_xaxes(type="category", showgrid=False, tickangle=-90, dtick=5)
    fig.update_yaxes(showgrid=False, tickfont=dict(size=10))
    return fig


def best_by_category_chart(ov: pd.DataFrame, capital: float, t: dict) -> go.Figure:
    o = ov.sort_values("Final Amount (₹)")
    labels = [f"{c} · {tg:.1f}%" for c, tg in zip(o["Category"], o["Best Target %"])]
    fig = go.Figure(go.Bar(
        y=labels, x=o["Final Amount (₹)"], orientation="h", marker=dict(color=t["s1"], line=dict(width=0)),
        text=[inr(v) for v in o["Final Amount (₹)"]],
        textposition="outside", textfont=dict(color=t["ink"], size=12), cliponaxis=False,
        hovertemplate="%{y}<br>Final %{text}<extra></extra>"))
    fig = _base(fig, t, 70 + 44 * len(o), "Final amount at each category's best target")
    hi = float(o["Final Amount (₹)"].max())
    tv, tt = _inr_ticks(0, hi, 4)
    fig.update_xaxes(tickvals=tv, ticktext=tt, range=[0, hi * 1.3], showgrid=True)
    fig.update_yaxes(showgrid=False)
    fig.add_vline(x=capital, line=dict(color=t["ink2"], width=1), annotation_text="start",
                  annotation_position="top", annotation_font=dict(color=t["ink2"], size=11))
    fig.update_layout(bargap=0.35)
    return fig
