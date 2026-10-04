"""停損停利參數自動掃描：對整個股票池的「所有歷史進場訊號」（不只是目前活著的那一筆），
批次套用一整組（停損倍數 × 停利倍數）組合，統計各組合的歷史勝率／期望報酬，
取代憑感覺手動調 2N/5N 這種單一組合。

跟 screener.py 的差異：screener 只抓「目前還在持有狀態」的最新一筆訊號，用於選股；
這裡抓的是「策略在歷史上觸發過的每一筆訊號」，用於估計某組停損停利參數的長期表現，
樣本數通常大上一個數量級，統計上才有意義。
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from . import data_loader
from . import indicators as ind
from . import strategies as strategies_module
from . import trade_planner


def _historical_entries(df: pd.DataFrame, strategy, params: dict, atr_period: int) -> list[dict]:
    """找出一檔股票在整段資料期間、依指定策略觸發過的所有進場點，
    並預先算好每個進場點當下的 N (ATR)，供後續掃描重複使用而不必重算。"""
    if len(df) < atr_period + 5:
        return []
    try:
        signal_df = strategy.compute(df, params)
    except Exception:
        return []

    position = signal_df["Position"].fillna(0).astype(int)
    change = position.diff()
    change.iloc[0] = position.iloc[0]
    entry_dates = signal_df.index[change == 1]
    if len(entry_dates) == 0:
        return []

    atr_series = ind.atr(df["High"], df["Low"], df["Close"], period=atr_period)

    entries = []
    for entry_date in entry_dates:
        entry_price = float(signal_df.loc[entry_date, "Close"])
        if entry_price <= 0:
            continue
        n_valid = atr_series.loc[:entry_date].dropna()
        if n_valid.empty:
            continue
        entries.append({"entry_date": entry_date.date(), "entry_price": entry_price, "n_atr": float(n_valid.iloc[-1])})
    return entries


@st.cache_data(ttl=1800, show_spinner=False)
def collect_universe_entries(
    market: str,
    strategy_key: str,
    params_items: tuple[tuple[str, float], ...],
    start_date: dt.date,
    end_date: dt.date,
    atr_period: int,
) -> list[dict]:
    """掃描整個市場股票池，回傳所有歷史進場點（含各自的價格資料，供後續模擬觸價用）。

    回傳的每筆 dict 含 "代碼"／"名稱"／"entry_date"／"entry_price"／"n_atr"；
    價格資料另外用 data_loader 的快取取得，這裡不重複帶著整個 DataFrame 序列化，
    避免快取變得笨重。
    """
    strategy = strategies_module.get_strategy(strategy_key)
    params = dict(params_items)
    universe = data_loader.POPULAR_TICKERS.get(market, {})
    tickers = tuple(universe.keys())
    price_data = data_loader.load_universe_prices(tickers, start_date, end_date)

    all_entries = []
    for ticker, df in price_data.items():
        for e in _historical_entries(df, strategy, params, atr_period):
            all_entries.append({"代碼": ticker, "名稱": universe.get(ticker, ""), **e})
    return all_entries


def scan_bracket_grid(
    market: str,
    strategy_key: str,
    params_items: tuple[tuple[str, float], ...],
    start_date: dt.date,
    end_date: dt.date,
    atr_period: int,
    stop_mults: list[float],
    target_mults: list[float],
    max_hold_days: int | None = None,
) -> tuple[pd.DataFrame, int]:
    """對指定市場的所有歷史進場訊號，測試每一組（停損倍數, 停利倍數）組合的表現。

    回傳 (結果表, 總進場點數)。結果表依「平均報酬率」由高到低排序。
    """
    entries = collect_universe_entries(market, strategy_key, params_items, start_date, end_date, atr_period)
    if not entries:
        return pd.DataFrame(), 0

    universe = data_loader.POPULAR_TICKERS.get(market, {})
    tickers = tuple(universe.keys())
    price_data = data_loader.load_universe_prices(tickers, start_date, end_date)

    rows = []
    for stop_mult in stop_mults:
        for target_mult in target_mults:
            resolved_returns = []
            wins = 0
            for e in entries:
                df = price_data.get(e["代碼"])
                if df is None:
                    continue
                bracket = trade_planner.compute_bracket(e["entry_price"], e["n_atr"], "多", stop_mult, target_mult)
                outcome = trade_planner.check_bracket_outcome(
                    df,
                    e["entry_date"],
                    e["entry_price"],
                    bracket["停損價"],
                    bracket["停利價"],
                    "多",
                    max_hold_days=max_hold_days,
                )
                if outcome["狀態"] in ("已停利", "已停損"):
                    resolved_returns.append(outcome["報酬率"])
                    if outcome["狀態"] == "已停利":
                        wins += 1

            if not resolved_returns:
                continue
            n_resolved = len(resolved_returns)
            win_rate = wins / n_resolved
            avg_return = sum(resolved_returns) / n_resolved
            gross_profit = sum(r for r in resolved_returns if r > 0)
            gross_loss = -sum(r for r in resolved_returns if r <= 0)
            if gross_loss > 0:
                profit_factor = gross_profit / gross_loss
            elif gross_profit > 0:
                profit_factor = float("inf")
            else:
                profit_factor = float("nan")

            rows.append(
                {
                    "停損倍數": stop_mult,
                    "停利倍數": target_mult,
                    "風報比": target_mult / stop_mult,
                    "已解決筆數": n_resolved,
                    "勝率": win_rate,
                    "平均報酬率": avg_return,
                    "獲利因子": profit_factor,
                }
            )

    result = pd.DataFrame(rows)
    if result.empty:
        return result, len(entries)
    return result.sort_values("平均報酬率", ascending=False).reset_index(drop=True), len(entries)
