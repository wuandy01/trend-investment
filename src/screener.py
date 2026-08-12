"""跨股票掃描器：依選定策略找出目前有進場訊號、且訊號後漲幅最強的熱門股。"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from . import data_loader
from . import strategies as strategies_module


def _latest_signal(df: pd.DataFrame, strategy: strategies_module.Strategy, params: dict) -> dict | None:
    """若該股目前正處於策略的多單持有狀態，回傳進場資訊；否則回傳 None。"""
    if len(df) < 5:
        return None
    try:
        signal_df = strategy.compute(df, params)
    except Exception:
        return None

    position = signal_df["Position"]
    if position.empty or position.iloc[-1] != 1:
        return None

    change = position.diff()
    change.iloc[0] = position.iloc[0]
    entry_dates = signal_df.index[change == 1]
    if len(entry_dates) == 0:
        return None

    entry_date = entry_dates[-1]
    entry_price = float(signal_df.loc[entry_date, "Close"])
    latest_price = float(signal_df["Close"].iloc[-1])
    if entry_price <= 0:
        return None

    return {
        "進場日期": entry_date,
        "進場價格": entry_price,
        "現價": latest_price,
        "訊號後漲幅": latest_price / entry_price - 1,
        "持有天數": (signal_df.index[-1] - entry_date).days,
    }


@st.cache_data(ttl=1800, show_spinner=False)
def scan_market(
    market: str,
    strategy_key: str,
    params_items: tuple[tuple[str, float], ...],
    start_date: dt.date,
    end_date: dt.date,
    top_n: int = 10,
) -> pd.DataFrame:
    """掃描指定市場的熱門標的，回傳依「訊號後漲幅」排序的前 N 名。"""
    strategy = strategies_module.get_strategy(strategy_key)
    params = dict(params_items)

    universe = data_loader.POPULAR_TICKERS.get(market, {})
    tickers = tuple(universe.keys())
    price_data = data_loader.load_universe_prices(tickers, start_date, end_date)

    rows = []
    for ticker, df in price_data.items():
        info = _latest_signal(df, strategy, params)
        if info is None:
            continue
        rows.append({"代碼": ticker, "名稱": universe.get(ticker, ""), **info})

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows).sort_values("訊號後漲幅", ascending=False).head(top_n)
    result.insert(0, "排名", range(1, len(result) + 1))
    return result.reset_index(drop=True)
