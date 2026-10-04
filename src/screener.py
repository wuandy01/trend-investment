"""跨股票掃描器：依選定策略找出目前有進場訊號的熱門股，可依「剛觸發訊號」或
「訊號後漲幅最高」兩種方式排序——前者用來發現還沒漲多的新機會，後者用來確認
目前動能最強勢的股票（但也代表可能已經漲多，追高風險較高）。"""

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
) -> pd.DataFrame:
    """掃描指定市場的熱門標的，回傳「所有」目前有進場訊號的股票（未排序、未篩選筆數）。

    刻意不在這裡排序或截斷——排序方式（剛觸發 vs 漲幅最高）是使用者可切換的顯示選項，
    不該影響昂貴的下載＋掃描這一步；掃描結果快取起來，切換排序時直接用 rank_signals()
    重新排就好，不用重新下載。
    """
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

    return pd.DataFrame(rows)


def rank_signals(df: pd.DataFrame, sort_by: str = "fresh", top_n: int = 10) -> pd.DataFrame:
    """把 scan_market() 的掃描結果依指定方式排序、取前 N 名。

    sort_by："fresh" 依「持有天數」由小到大（訊號最新觸發優先，用來發現還沒漲多的新機會）；
             "strength" 依「訊號後漲幅」由高到低（目前動能最強勢，但也可能已經漲多）。
    """
    if df.empty:
        return df

    if sort_by == "strength":
        sort_col, ascending = "訊號後漲幅", False
    else:
        sort_col, ascending = "持有天數", True

    result = df.sort_values(sort_col, ascending=ascending).head(top_n).reset_index(drop=True)
    result.insert(0, "排名", range(1, len(result) + 1))
    return result
