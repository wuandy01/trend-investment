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


SHORT_SIDE_COLOR = "#2e5090"


def build_turtle_chart(data: pd.DataFrame, events: pd.DataFrame, title: str) -> go.Figure:
    """海龜交易法則專用圖：K 線 + 進出場高低點 + 多空進出場標記 + 成交量 + N (ATR)。"""
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.6, 0.16, 0.24],
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

    ma_specs = [
        ("MA5", "MA5", "#c9c9c9"),
        ("MA20", "MA20", "#9e9e9e"),
        ("MA60", "MA60", "#6e6e6e"),
        ("MA200", "MA200", "#333333"),
    ]
    for col, label, color in ma_specs:
        if col in data.columns:
            fig.add_trace(
                go.Scatter(
                    x=data.index,
                    y=data[col],
                    name=label,
                    line=dict(width=1, color=color),
                    opacity=0.8,
                ),
                row=1,
                col=1,
            )

    band_specs = [
        ("High_Entry", "進場高點", UP_COLOR, "dot"),
        ("Low_Entry", "進場低點", SHORT_SIDE_COLOR, "dot"),
        ("High_Exit", "空方出場點", SHORT_SIDE_COLOR, "dash"),
        ("Low_Exit", "多方出場點", UP_COLOR, "dash"),
    ]
    for col, label, color, dash in band_specs:
        if col in data.columns:
            fig.add_trace(
                go.Scatter(
                    x=data.index,
                    y=data[col],
                    name=label,
                    line=dict(width=1, color=color, dash=dash),
                    opacity=0.7,
                ),
                row=1,
                col=1,
            )

    if events is not None and len(events) > 0:
        long_entries = events[(events["方向"] == "多") & (events["事件"] != "出場")]
        short_entries = events[(events["方向"] == "空") & (events["事件"] != "出場")]
        exits = events[events["事件"] == "出場"]

        if len(long_entries) > 0:
            fig.add_trace(
                go.Scatter(
                    x=long_entries["日期"],
                    y=long_entries["價格"],
                    mode="markers",
                    name="多方進場/加碼",
                    marker=dict(symbol="triangle-up", size=11, color=UP_COLOR, line=dict(width=1, color="white")),
                ),
                row=1,
                col=1,
            )
        if len(short_entries) > 0:
            fig.add_trace(
                go.Scatter(
                    x=short_entries["日期"],
                    y=short_entries["價格"],
                    mode="markers",
                    name="空方進場/加碼",
                    marker=dict(
                        symbol="triangle-down", size=11, color=SHORT_SIDE_COLOR, line=dict(width=1, color="white")
                    ),
                ),
                row=1,
                col=1,
            )
        if len(exits) > 0:
            fig.add_trace(
                go.Scatter(
                    x=exits["日期"],
                    y=exits["價格"],
                    mode="markers",
                    name="出場（平倉）",
                    marker=dict(symbol="x", size=9, color="#666666"),
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

    if "N" in data.columns:
        fig.add_trace(
            go.Scatter(x=data.index, y=data["N"], name="N (ATR)", line=dict(width=1.3, color=PANEL_COLORS[0])),
            row=3,
            col=1,
        )

    fig.update_layout(
        height=680,
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", y=1.05, x=0),
        margin=dict(l=10, r=10, t=30, b=10),
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="價格", row=1, col=1)
    fig.update_yaxes(title_text="量", row=2, col=1)
    fig.update_yaxes(title_text="N", row=3, col=1)
    return fig


STOP_COLOR = "#c0392b"
TARGET_COLOR = "#1e8449"


def build_bracket_chart(
    data: pd.DataFrame,
    entry_date,
    entry_price: float,
    stop_price: float,
    target_price: float,
    outcome: dict,
    title: str,
) -> go.Figure:
    """停損停利規劃圖：K 線 + 進場點 + 停損／停利水平線 + 觸價標記（若已觸價）。"""
    fig = go.Figure()

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
        )
    )

    fig.add_hline(y=stop_price, line=dict(color=STOP_COLOR, width=1.5, dash="dash"), annotation_text="停損")
    fig.add_hline(y=target_price, line=dict(color=TARGET_COLOR, width=1.5, dash="dash"), annotation_text="停利")

    fig.add_trace(
        go.Scatter(
            x=[entry_date],
            y=[entry_price],
            mode="markers",
            name="進場",
            marker=dict(symbol="diamond", size=13, color="#f39c12", line=dict(width=1, color="white")),
        )
    )

    if outcome.get("狀態") in {"已停利", "已停損"}:
        marker_color = TARGET_COLOR if outcome["狀態"] == "已停利" else STOP_COLOR
        fig.add_trace(
            go.Scatter(
                x=[outcome["觸價日期"]],
                y=[outcome["觸價價格"]],
                mode="markers",
                name=outcome["狀態"],
                marker=dict(symbol="x", size=13, color=marker_color, line=dict(width=2, color=marker_color)),
            )
        )

    fig.update_layout(
        height=480,
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", y=1.05, x=0),
        margin=dict(l=10, r=10, t=30, b=10),
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="價格")
    return fig


def build_param_heatmap(scan_df: pd.DataFrame, value_col: str, title: str) -> go.Figure:
    """停損停利參數掃描結果的熱力圖：X 軸停利倍數、Y 軸停損倍數、色階為指定欄位（例如平均報酬率）。"""
    pivot = scan_df.pivot(index="停損倍數", columns="停利倍數", values=value_col).sort_index(ascending=False)
    text = pivot.map(lambda v: f"{v * 100:.1f}%" if pd.notna(v) else "")

    fig = go.Figure(
        data=go.Heatmap(
            z=pivot.values,
            x=[str(c) for c in pivot.columns],
            y=[str(i) for i in pivot.index],
            text=text.values,
            texttemplate="%{text}",
            colorscale="RdYlGn",
            zmid=0,
            colorbar=dict(title=title, tickformat=".0%"),
            hovertemplate="停損 %{y}N｜停利 %{x}N<br>" + title + ": %{z:.2%}<extra></extra>",
        )
    )
    fig.update_layout(
        height=420,
        margin=dict(l=10, r=10, t=30, b=10),
        xaxis_title="停利倍數（×N）",
        yaxis_title="停損倍數（×N）",
    )
    return fig
