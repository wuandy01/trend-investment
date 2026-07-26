"""五種技術分析策略的訊號產生邏輯。

每個策略都會回傳「加上指標欄位」的 DataFrame，並附上一個 Position 欄位
（1 = 持有多單，0 = 空手）。是否要延遲一天執行（避免用到未來資料）交由
backtest.py 處理，這裡只負責訊號本身。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from . import indicators as ind


@dataclass
class ParamSpec:
    key: str
    label: str
    default: float
    min_value: float
    max_value: float
    step: float
    is_int: bool = True


@dataclass
class Strategy:
    key: str
    name: str
    description: str
    params: list[ParamSpec]
    compute: Callable[[pd.DataFrame, dict], pd.DataFrame]
    overlay_cols: list[str] = field(default_factory=list)
    panel_cols: list[list[str]] = field(default_factory=list)  # 每個子面板一組欄位
    band_fill: tuple[str, str] | None = None


def _stateful_position(enter: pd.Series, exit_: pd.Series) -> pd.Series:
    """依「進場條件」與「出場條件」逐日推算持倉狀態（0/1）。

    一旦觸發進場就持續持有，直到觸發出場條件為止，中間不重複進出場。
    """
    enter_arr = enter.fillna(False).to_numpy()
    exit_arr = exit_.fillna(False).to_numpy()
    position = [0] * len(enter_arr)
    state = 0
    for i in range(len(enter_arr)):
        if state == 0 and enter_arr[i]:
            state = 1
        elif state == 1 and exit_arr[i]:
            state = 0
        position[i] = state
    return pd.Series(position, index=enter.index)


def ma_cross_strategy(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = df.copy()
    short_w = int(params["short_window"])
    long_w = int(params["long_window"])
    df["MA_Short"] = ind.sma(df["Close"], short_w)
    df["MA_Long"] = ind.sma(df["Close"], long_w)
    df["Position"] = (df["MA_Short"] > df["MA_Long"]).astype(int)
    df.loc[df["MA_Short"].isna() | df["MA_Long"].isna(), "Position"] = 0
    return df


def rsi_strategy(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = df.copy()
    period = int(params["period"])
    oversold = params["oversold"]
    overbought = params["overbought"]
    df["RSI"] = ind.rsi(df["Close"], period)

    enter = (df["RSI"] < oversold) & (df["RSI"].shift(1) >= oversold)
    exit_ = (df["RSI"] > overbought) & (df["RSI"].shift(1) <= overbought)
    df["Position"] = _stateful_position(enter, exit_)
    df.loc[df["RSI"].isna(), "Position"] = 0
    return df


def macd_strategy(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = df.copy()
    fast = int(params["fast"])
    slow = int(params["slow"])
    signal = int(params["signal"])
    macd_df = ind.macd(df["Close"], fast=fast, slow=slow, signal=signal)
    df["MACD"] = macd_df["MACD"]
    df["Signal"] = macd_df["Signal"]
    df["Histogram"] = macd_df["Histogram"]
    df["Position"] = (df["MACD"] > df["Signal"]).astype(int)
    df.loc[df["MACD"].isna() | df["Signal"].isna(), "Position"] = 0
    return df


def bollinger_strategy(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = df.copy()
    window = int(params["window"])
    num_std = params["num_std"]
    bb = ind.bollinger_bands(df["Close"], window=window, num_std=num_std)
    df["Mid"] = bb["Mid"]
    df["Upper"] = bb["Upper"]
    df["Lower"] = bb["Lower"]

    close = df["Close"]
    enter = (close > df["Lower"]) & (close.shift(1) <= df["Lower"].shift(1))
    exit_ = (close > df["Upper"]) & (close.shift(1) <= df["Upper"].shift(1))
    df["Position"] = _stateful_position(enter, exit_)
    df.loc[df["Mid"].isna(), "Position"] = 0
    return df


def atr_channel_strategy(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = df.copy()
    ema_period = int(params["ema_period"])
    atr_period = int(params["atr_period"])
    multiplier = params["multiplier"]

    df["ATR"] = ind.atr(df["High"], df["Low"], df["Close"], period=atr_period)
    df["Mid"] = ind.ema(df["Close"], ema_period)
    df["Upper"] = df["Mid"] + multiplier * df["ATR"]
    df["Lower"] = df["Mid"] - multiplier * df["ATR"]

    close = df["Close"]
    enter = (close > df["Upper"]) & (close.shift(1) <= df["Upper"].shift(1))
    exit_ = (close < df["Lower"]) & (close.shift(1) >= df["Lower"].shift(1))
    df["Position"] = _stateful_position(enter, exit_)
    df.loc[df["Mid"].isna() | df["ATR"].isna(), "Position"] = 0
    return df


STRATEGIES: dict[str, Strategy] = {
    "ma_cross": Strategy(
        key="ma_cross",
        name="均線交叉 (MA Cross)",
        description="短期均線由下往上穿越長期均線時做多，跌破時出場，經典的趨勢跟隨策略。",
        params=[
            ParamSpec("short_window", "短期均線天數", 20, 2, 60, 1),
            ParamSpec("long_window", "長期均線天數", 60, 10, 240, 1),
        ],
        compute=ma_cross_strategy,
        overlay_cols=["MA_Short", "MA_Long"],
    ),
    "rsi": Strategy(
        key="rsi",
        name="RSI 超買超賣",
        description="RSI 跌破超賣線後進場，站上超買線後出場，屬於區間型的逆勢策略。",
        params=[
            ParamSpec("period", "RSI 天數", 14, 2, 50, 1),
            ParamSpec("oversold", "超賣門檻", 30, 5, 45, 1),
            ParamSpec("overbought", "超買門檻", 70, 55, 95, 1),
        ],
        compute=rsi_strategy,
        panel_cols=[["RSI"]],
    ),
    "macd": Strategy(
        key="macd",
        name="MACD",
        description="MACD 線由下往上穿越訊號線時做多，由上往下跌破時出場，判斷動能與趨勢轉折。",
        params=[
            ParamSpec("fast", "快線 EMA", 12, 2, 50, 1),
            ParamSpec("slow", "慢線 EMA", 26, 5, 100, 1),
            ParamSpec("signal", "訊號線 EMA", 9, 2, 50, 1),
        ],
        compute=macd_strategy,
        panel_cols=[["MACD", "Signal", "Histogram"]],
    ),
    "bollinger": Strategy(
        key="bollinger",
        name="布林通道 (Bollinger Bands)",
        description="股價向上突破下軌後進場（觸底反彈），觸及上軌後出場（獲利了結），屬於均值回歸策略。",
        params=[
            ParamSpec("window", "均線天數", 20, 5, 60, 1),
            ParamSpec("num_std", "標準差倍數", 2.0, 0.5, 4.0, 0.1, is_int=False),
        ],
        compute=bollinger_strategy,
        overlay_cols=["Mid", "Upper", "Lower"],
        band_fill=("Upper", "Lower"),
    ),
    "atr_channel": Strategy(
        key="atr_channel",
        name="ATR 通道突破",
        description="以 EMA 為中軌、ATR 波動幅度為通道寬度；價格向上突破上軌進場，跌破下軌出場，屬於波動率突破的趨勢策略。",
        params=[
            ParamSpec("ema_period", "中軌 EMA 天數", 20, 5, 60, 1),
            ParamSpec("atr_period", "ATR 天數", 14, 5, 50, 1),
            ParamSpec("multiplier", "ATR 倍數", 2.0, 0.5, 5.0, 0.1, is_int=False),
        ],
        compute=atr_channel_strategy,
        overlay_cols=["Mid", "Upper", "Lower"],
        panel_cols=[["ATR"]],
        band_fill=("Upper", "Lower"),
    ),
}


def list_strategy_options() -> list[tuple[str, str]]:
    """回傳 (key, 顯示名稱) 供下拉選單使用。"""
    return [(s.key, s.name) for s in STRATEGIES.values()]


def get_strategy(key: str) -> Strategy:
    return STRATEGIES[key]
