"""海龜交易法則 (Turtle Trading Rules)：多空 ATR 突破系統，含加碼與獨立回測引擎。

跟 strategies.py 的五種策略不同，這裡支援放空與加碼（金字塔式加碼），
持倉不是單純的 0/1，而是「方向 (多/空/空手) × 單位數 (0~4)」，所以用
逐日狀態機模擬，而不是 strategies.py 那種向量化訊號。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import indicators as ind

TRADING_DAYS_PER_YEAR = 252

SYSTEMS = {
    "short": {
        "label": "短期系統（20 日突破進場／10 日反向出場）",
        "entry_days": 20,
        "exit_days": 10,
    },
    "long": {
        "label": "長期系統（55 日突破進場／20 日反向出場）",
        "entry_days": 55,
        "exit_days": 20,
    },
}


@dataclass
class TurtleResult:
    data: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict


TREND_MA_WINDOWS = (5, 20, 60, 200)


def _compute_signal_frame(
    df: pd.DataFrame,
    entry_days: int,
    exit_days: int,
    atr_period: int,
    trend_filter_mode: str = "simple",
) -> pd.DataFrame:
    """加上 N (ATR)、進場用高低點、出場用高低點、長期趨勢濾網用的均線。全部用「前一日為止」的資料，避免用到當天未來高低。

    trend_filter_mode:
      "simple" — 收盤價站上/跌破 200 日均線即認定多頭/空頭（較寬鬆，較不容易把拉回中的正常訊號濾掉）
      "strict" — 5/20/60/200 日均線需完全多頭排列（MA5>MA20>MA60>MA200）才算多頭，反向排列才算空頭（較嚴格）
    """
    out = df.copy()
    out["N"] = ind.atr(out["High"], out["Low"], out["Close"], period=atr_period)
    out["High_Entry"] = out["High"].rolling(entry_days).max().shift(1)
    out["Low_Entry"] = out["Low"].rolling(entry_days).min().shift(1)
    out["High_Exit"] = out["High"].rolling(exit_days).max().shift(1)
    out["Low_Exit"] = out["Low"].rolling(exit_days).min().shift(1)

    for w in TREND_MA_WINDOWS:
        out[f"MA{w}"] = ind.sma(out["Close"], w)
    ma5, ma20, ma60, ma200 = (out[f"MA{w}"] for w in TREND_MA_WINDOWS)

    if trend_filter_mode == "strict":
        bullish = (ma5 > ma20) & (ma20 > ma60) & (ma60 > ma200)
        bearish = (ma5 < ma20) & (ma20 < ma60) & (ma60 < ma200)
    else:
        bullish = out["Close"] > ma200
        bearish = out["Close"] < ma200

    out["Trend_Regime"] = np.select([bullish, bearish], [1, -1], default=0).astype(float)
    out.loc[ma200.isna(), "Trend_Regime"] = np.nan  # 資料不夠 200 天前，濾網視為未知，不放行任何方向

    return out


def _simulate(
    df: pd.DataFrame,
    max_units: int,
    pyramid_step: float,
    use_trend_filter: bool = False,
) -> tuple[list[int], list[int]]:
    """逐日模擬方向與持有單位數。回傳 (direction_per_day, units_per_day)，皆為「當日收盤後」的狀態。

    use_trend_filter=True 時，只有在 5/20/60/200 日均線多頭排列（MA5>MA20>MA60>MA200）才允許開多單，
    空頭排列時才允許開空單；排列不明確或資料不足 200 天時，兩個方向都不開新倉。已持有的部位不受影響，
    仍照原本的出場規則出場。
    """
    close = df["Close"].to_numpy()
    n = df["N"].to_numpy()
    high_entry = df["High_Entry"].to_numpy()
    low_entry = df["Low_Entry"].to_numpy()
    high_exit = df["High_Exit"].to_numpy()
    low_exit = df["Low_Exit"].to_numpy()
    trend_regime = df["Trend_Regime"].to_numpy()

    direction = 0  # 0 空手, 1 多, -1 空
    unit_prices: list[float] = []  # 各單位的進場價，用來判斷下一次加碼門檻

    directions_out = []
    units_out = []

    for i in range(len(close)):
        price = close[i]
        atr_now = n[i]
        valid = not (np.isnan(atr_now) or np.isnan(high_entry[i]) or np.isnan(low_entry[i]))
        long_ok = (not use_trend_filter) or trend_regime[i] == 1
        short_ok = (not use_trend_filter) or trend_regime[i] == -1

        if direction == 0:
            if valid and long_ok and price > high_entry[i]:
                direction = 1
                unit_prices = [price]
            elif valid and short_ok and price < low_entry[i]:
                direction = -1
                unit_prices = [price]
        elif direction == 1:
            if not np.isnan(low_exit[i]) and price < low_exit[i]:
                direction = 0
                unit_prices = []
            elif (
                len(unit_prices) < max_units
                and not np.isnan(atr_now)
                and price >= unit_prices[-1] + pyramid_step * atr_now
            ):
                unit_prices.append(price)
        elif direction == -1:
            if not np.isnan(high_exit[i]) and price > high_exit[i]:
                direction = 0
                unit_prices = []
            elif (
                len(unit_prices) < max_units
                and not np.isnan(atr_now)
                and price <= unit_prices[-1] - pyramid_step * atr_now
            ):
                unit_prices.append(price)

        directions_out.append(direction)
        units_out.append(len(unit_prices))

    return directions_out, units_out


def run_turtle_backtest(
    df: pd.DataFrame,
    system_key: str = "short",
    atr_period: int = 20,
    max_units: int = 4,
    pyramid_step: float = 0.5,
    initial_capital: float = 100_000.0,
    cost_pct: float = 0.1,
    use_trend_filter: bool = False,
    trend_filter_mode: str = "simple",
) -> TurtleResult:
    system = SYSTEMS[system_key]
    data = _compute_signal_frame(
        df, system["entry_days"], system["exit_days"], atr_period, trend_filter_mode=trend_filter_mode
    )

    directions, units = _simulate(
        data, max_units=max_units, pyramid_step=pyramid_step, use_trend_filter=use_trend_filter
    )
    data["Direction"] = directions
    data["Units"] = units
    data["Weight"] = data["Units"] / max_units  # 1 單位 = 1/max_units 的滿倉部位

    # 延遲一天執行，避免用到未來資料（訊號在當天收盤才確定）
    data["Executed_Direction"] = data["Direction"].shift(1).fillna(0)
    data["Executed_Weight"] = data["Weight"].shift(1).fillna(0.0)
    data["Daily_Return"] = data["Close"].pct_change().fillna(0)

    exposure = data["Executed_Direction"] * data["Executed_Weight"]
    exposure_change = exposure.diff().abs().fillna(exposure.iloc[0])
    cost = cost_pct / 100.0
    data["Strategy_Return"] = exposure * data["Daily_Return"] - exposure_change * cost

    data["Equity"] = initial_capital * (1 + data["Strategy_Return"]).cumprod()
    data["BuyHold_Equity"] = initial_capital * (1 + data["Daily_Return"]).cumprod()
    data["Drawdown"] = data["Equity"] / data["Equity"].cummax() - 1
    data["BuyHold_Drawdown"] = data["BuyHold_Equity"] / data["BuyHold_Equity"].cummax() - 1

    trades = _extract_trades(data)
    metrics = _compute_metrics(data, trades, initial_capital)

    return TurtleResult(data=data, trades=trades, metrics=metrics)


def extract_unit_events(data: pd.DataFrame) -> pd.DataFrame:
    """逐筆列出每一次「進場／加碼／出場」事件，供畫圖用標記（比 trades 更細顆粒度）。"""
    rows = []
    prev_direction = 0
    prev_units = 0
    for date, row in data.iterrows():
        direction = row["Direction"]
        units = row["Units"]
        if prev_direction == 0 and direction != 0:
            rows.append(
                {
                    "日期": date,
                    "價格": float(row["Close"]),
                    "事件": "進場",
                    "方向": "多" if direction == 1 else "空",
                }
            )
        elif prev_direction != 0 and direction == prev_direction and units > prev_units:
            rows.append(
                {
                    "日期": date,
                    "價格": float(row["Close"]),
                    "事件": "加碼",
                    "方向": "多" if direction == 1 else "空",
                }
            )
        elif prev_direction != 0 and direction == 0:
            rows.append(
                {
                    "日期": date,
                    "價格": float(row["Close"]),
                    "事件": "出場",
                    "方向": "多" if prev_direction == 1 else "空",
                }
            )
        prev_direction = direction
        prev_units = units
    return pd.DataFrame(rows)


def _extract_trades(data: pd.DataFrame) -> pd.DataFrame:
    direction = data["Direction"]
    units = data["Units"]
    change = direction.diff()
    change.iloc[0] = direction.iloc[0]

    rows = []
    current_direction = 0
    entry_dates: list = []
    entry_prices: list = []

    for date, row in data.iterrows():
        d = row["Direction"]
        if current_direction == 0 and d != 0:
            # 新倉位開始
            current_direction = d
            entry_dates = [date]
            entry_prices = [row["Close"]]
        elif current_direction != 0 and d == current_direction:
            # 可能加碼：單位數比上一筆紀錄多，代表今天加了一單位
            if len(entry_dates) < row["Units"]:
                entry_dates.append(date)
                entry_prices.append(row["Close"])
        elif current_direction != 0 and d == 0:
            # 出場
            exit_date = date
            exit_price = row["Close"]
            avg_entry = float(np.mean(entry_prices))
            ret = current_direction * (exit_price / avg_entry - 1)
            rows.append(
                {
                    "方向": "多" if current_direction == 1 else "空",
                    "首次進場日期": entry_dates[0],
                    "平均進場價": round(avg_entry, 2),
                    "加碼次數": len(entry_dates),
                    "出場日期": exit_date,
                    "出場價格": round(float(exit_price), 2),
                    "報酬率": ret,
                    "持有天數": (exit_date - entry_dates[0]).days,
                    "狀態": "已平倉",
                }
            )
            current_direction = 0
            entry_dates = []
            entry_prices = []

    # 若回測結束時仍持有部位，也列出來（標記為持有中，以最後收盤價計算目前報酬率）
    if current_direction != 0 and entry_dates:
        last_date = data.index[-1]
        last_price = float(data["Close"].iloc[-1])
        avg_entry = float(np.mean(entry_prices))
        ret = current_direction * (last_price / avg_entry - 1)
        rows.append(
            {
                "方向": "多" if current_direction == 1 else "空",
                "首次進場日期": entry_dates[0],
                "平均進場價": round(avg_entry, 2),
                "加碼次數": len(entry_dates),
                "出場日期": last_date,
                "出場價格": round(last_price, 2),
                "報酬率": ret,
                "持有天數": (last_date - entry_dates[0]).days,
                "狀態": "持有中",
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

    std = strategy_return.std(ddof=0)
    ann_vol = float(std * np.sqrt(TRADING_DAYS_PER_YEAR))
    mean_daily = float(strategy_return.mean())
    sharpe = (mean_daily / std) * np.sqrt(TRADING_DAYS_PER_YEAR) if std > 0 else 0.0

    max_drawdown = float(data["Drawdown"].min())  # MDD = (Peak - Trough) / Peak

    if len(trades) > 0:
        closed = trades[trades["狀態"] == "已平倉"]
        n_trades = len(trades)
        wins = closed[closed["報酬率"] > 0]
        losses = closed[closed["報酬率"] <= 0]
        win_rate = len(wins) / len(closed) if len(closed) > 0 else float("nan")
        gross_profit = wins["報酬率"].sum()
        gross_loss = -losses["報酬率"].sum()
        profit_factor = (
            (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else float("nan")
        )
        max_units_reached = int(trades["加碼次數"].max())
    else:
        n_trades = 0
        win_rate = float("nan")
        profit_factor = float("nan")
        max_units_reached = 0

    return {
        "最終資產": final_equity,
        "總報酬率": total_return,
        "年化報酬率 (CAGR)": cagr,
        "年化波動度": ann_vol,
        "夏普比率": sharpe,
        "最大回檔 (MDD)": max_drawdown,
        "交易次數": n_trades,
        "勝率": win_rate,
        "獲利因子": profit_factor,
        "最高加碼單位數": max_units_reached,
        "買進持有報酬率": bh_total_return,
    }
