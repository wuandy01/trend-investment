"""用 Plotly 畫技術分析圖與權益曲線圖。

配色採用台灣慣例：紅漲、綠跌。
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

UP_COLOR = "#d64541"
DOWN_COLOR = "#2e8b57"
OVERLAY_COLORS = ["#1f77b4", "#ff7f0e", "#9467bd", "#8c564b"]
PANEL_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c"]


def build_price_chart(data: pd.DataFrame, strategy, trades: pd.DataFrame, title: str) -> go.Figure:
    panel_groups = strategy.panel_cols
    n_panels = len(panel_groups)
    total_rows = 2 + n_panels

    if n_panels == 0:
        row_heights = [0.75, 0.25]
    else:
        panel_h = 0.32 / n_panels
        price_h = 1 - 0.16 - panel_h * n_panels
        row_heights = [price_h, 0.16] + [panel_h] * n_panels

    fig = make_subplots(
        rows=total_rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=row_heights,
    )

    fig.add_trace(
        go.Candlestick(
            x=data.index,
            open=data["Open"],
            high=data["High"],
            low=data["Low"],
            close=data["Close"],
            name=title,
            increasing_line_color=UP_COLOR,
            decreasing_line_color=DOWN_COLOR,
            increasing_fillcolor=UP_COLOR,
            decreasing_fillcolor=DOWN_COLOR,
        ),
        row=1,
        col=1,
    )

    for i, col in enumerate(strategy.overlay_cols):
        if col not in data.columns:
            continue
        fig.add_trace(
            go.Scatter(
                x=data.index,
                y=data[col],
                name=col,
                line=dict(width=1.3, color=OVERLAY_COLORS[i % len(OVERLAY_COLORS)]),
            ),
            row=1,
            col=1,
        )

    if strategy.band_fill:
        upper_col, lower_col = strategy.band_fill
        if upper_col in data.columns and lower_col in data.columns:
            fig.add_trace(
                go.Scatter(
                    x=data.index,
                    y=data[upper_col],
                    line=dict(width=0),
                    showlegend=False,
                    hoverinfo="skip",
                ),
                row=1,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=data.index,
                    y=data[lower_col],
                    line=dict(width=0),
                    fill="tonexty",
                    fillcolor="rgba(31,119,180,0.10)",
                    showlegend=False,
                    hoverinfo="skip",
                ),
                row=1,
                col=1,
            )

    if trades is not None and len(trades) > 0:
        fig.add_trace(
            go.Scatter(
                x=trades["進場日期"],
                y=trades["進場價格"],
                mode="markers",
                name="買進",
                marker=dict(symbol="triangle-up", size=12, color=UP_COLOR, line=dict(width=1, color="white")),
            ),
            row=1,
            col=1,
        )
        closed = trades[trades["狀態"] == "已平倉"]
        if len(closed) > 0:
            fig.add_trace(
                go.Scatter(
                    x=closed["出場日期"],
                    y=closed["出場價格"],
                    mode="markers",
                    name="賣出",
                    marker=dict(symbol="triangle-down", size=12, color=DOWN_COLOR, line=dict(width=1, color="white")),
                ),
                row=1,
                col=1,
            )

    vol_colors = [UP_COLOR if c >= o else DOWN_COLOR for o, c in zip(data["Open"], data["Close"])]
    fig.add_trace(
        go.Bar(x=data.index, y=data["Volume"], name="成交量", marker_color=vol_colors, showlegend=False),
        row=2,
        col=1,
    )

    for panel_idx, cols in enumerate(panel_groups):
        row = 3 + panel_idx
        for j, col in enumerate(cols):
            if col not in data.columns:
                continue
            if col == "Histogram":
                bar_colors = [UP_COLOR if v >= 0 else DOWN_COLOR for v in data[col].fillna(0)]
                fig.add_trace(
                    go.Bar(x=data.index, y=data[col], name=col, marker_color=bar_colors, showlegend=False),
                    row=row,
                    col=1,
                )
            else:
                fig.add_trace(
                    go.Scatter(
                        x=data.index,
                        y=data[col],
                        name=col,
                        line=dict(width=1.3, color=PANEL_COLORS[j % len(PANEL_COLORS)]),
                    ),
                    row=row,
                    col=1,
                )
        if cols == ["RSI"]:
            fig.add_hline(y=70, line_dash="dot", line_color="#999999", row=row, col=1)
            fig.add_hline(y=30, line_dash="dot", line_color="#999999", row=row, col=1)

    fig.update_layout(
        height=560 + n_panels * 150,
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", y=1.03, x=0),
        margin=dict(l=10, r=10, t=30, b=10),
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="價格", row=1, col=1)
    fig.update_yaxes(title_text="量", row=2, col=1)
    return fig


def build_equity_chart(data: pd.DataFrame) -> go.Figure:
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.68, 0.32],
    )

    fig.add_trace(
        go.Scatter(x=data.index, y=data["Equity"], name="策略權益", line=dict(color=UP_COLOR, width=2)),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=data.index,
            y=data["BuyHold_Equity"],
            name="買進持有",
            line=dict(color="#888888", width=1.5, dash="dot"),
        ),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Scatter(
            x=data.index,
            y=data["Drawdown"] * 100,
            name="策略回檔 %",
            line=dict(color=UP_COLOR, width=1),
            fill="tozeroy",
            fillcolor="rgba(214,69,65,0.15)",
        ),
        row=2,
        col=1,
    )

    fig.update_layout(
        height=520,
        legend=dict(orientation="h", y=1.05, x=0),
        margin=dict(l=10, r=10, t=40, b=10),
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="資產價值", row=1, col=1)
    fig.update_yaxes(title_text="回檔 (%)", row=2, col=1)
    return fig
