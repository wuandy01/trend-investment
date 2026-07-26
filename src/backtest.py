"""向量化回測引擎：把策略訊號轉換成權益曲線與績效指標。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


@dataclass
class BacktestResult:
    data: pd.DataFrame
    metrics: dict
    trades: pd.DataFrame


def run_backtest(
    df: pd.DataFrame,
    initial_capital: float = 100_000.0,
    cost_pct: float = 0.1,
) -> BacktestResult:
    """執行回測。

    重要假設：策略訊號在「當日收盤」產生後，於「下一個交易日」才實際執行，
    避免使用到未來資料（look-ahead bias）。
    """
    data = df.copy()
    data["Position"] = data["Position"].fillna(0).astype(int)

    data["Executed_Position"] = data["Position"].shift(1).fillna(0).astype(int)
    data["Daily_Return"] = data["Close"].pct_change().fillna(0)

    trade_flag = data["Executed_Position"].diff().abs().fillna(data["Executed_Position"].iloc[0])
    cost = cost_pct / 100.0
    data["Strategy_Return"] = data["Executed_Position"] * data["Daily_Return"] - trade_flag * cost

    data["Equity"] = initial_capital * (1 + data["Strategy_Return"]).cumprod()
    data["BuyHold_Equity"] = initial_capital * (1 + data["Daily_Return"]).cumprod()

    running_max = data["Equity"].cummax()
    data["Drawdown"] = data["Equity"] / running_max - 1

    bh_running_max = data["BuyHold_Equity"].cummax()
    data["BuyHold_Drawdown"] = data["BuyHold_Equity"] / bh_running_max - 1

    trades = _extract_trades(data)
    metrics = _compute_metrics(data, trades, initial_capital)

    return BacktestResult(data=data, metrics=metrics, trades=trades)


def _extract_trades(data: pd.DataFrame) -> pd.DataFrame:
    position = data["Position"]
    change = position.diff()
    change.iloc[0] = position.iloc[0]

    entry_dates = data.index[change == 1]
    exit_dates = data.index[change == -1]

    still_open = len(entry_dates) > len(exit_dates)
    if still_open:
        exit_dates = exit_dates.append(pd.Index([data.index[-1]]))

    rows = []
    for i, (entry_date, exit_date) in enumerate(zip(entry_dates, exit_dates)):
        entry_price = float(data.loc[entry_date, "Close"])
        exit_price = float(data.loc[exit_date, "Close"])
        is_last_open_trade = still_open and i == len(entry_dates) - 1
        ret = exit_price / entry_price - 1
        rows.append(
            {
                "進場日期": entry_date,
                "出場日期": exit_date,
                "進場價格": round(entry_price, 2),
                "出場價格": round(exit_price, 2),
                "報酬率": ret,
                "持有天數": (exit_date - entry_date).days,
                "狀態": "持有中" if is_last_open_trade else "已平倉",
            }
        )
    return pd.DataFrame(rows)


def _compute_metrics(data: pd.DataFrame, trades: pd.DataFrame, initial_capital: float) -> dict:
    equity = data["Equity"]
    strategy_return = data["Strategy_Return"]
    final_equity = float(equity.iloc[-1])

    total_return = final_equity / initial_capital - 1
    bh_total_return = float(data["BuyHold_Equity"].iloc[-1]) / initial_capital - 1

    n_days = len(data)
    span_days = (data.index[-1] - data.index[0]).days
    years = max(span_days / 365.25, n_days / TRADING_DAYS_PER_YEAR, 1e-9)
    cagr = (final_equity / initial_capital) ** (1 / years) - 1 if final_equity > 0 else -1.0

    ann_vol = float(strategy_return.std(ddof=0) * np.sqrt(TRADING_DAYS_PER_YEAR))
    mean_daily = float(strategy_return.mean())
    sharpe = (mean_daily / strategy_return.std(ddof=0)) * np.sqrt(TRADING_DAYS_PER_YEAR) if strategy_return.std(ddof=0) > 0 else 0.0

    max_drawdown = float(data["Drawdown"].min())

    if len(trades) > 0:
        closed = trades[trades["狀態"] == "已平倉"]
        n_trades = len(trades)
        wins = closed[closed["報酬率"] > 0]
        losses = closed[closed["報酬率"] <= 0]
        win_rate = len(wins) / len(closed) if len(closed) > 0 else float("nan")
        gross_profit = wins["報酬率"].sum()
        gross_loss = -losses["報酬率"].sum()
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else float("nan")
        avg_return_per_trade = closed["報酬率"].mean() if len(closed) > 0 else float("nan")
    else:
        n_trades = 0
        win_rate = float("nan")
        profit_factor = float("nan")
        avg_return_per_trade = float("nan")

    return {
        "最終資產": final_equity,
        "總報酬率": total_return,
        "年化報酬率 (CAGR)": cagr,
        "年化波動度": ann_vol,
        "夏普比率": sharpe,
        "最大回檔": max_drawdown,
        "交易次數": n_trades,
        "勝率": win_rate,
        "獲利因子": profit_factor,
        "平均每筆報酬率": avg_return_per_trade,
        "買進持有報酬率": bh_total_return,
    }
