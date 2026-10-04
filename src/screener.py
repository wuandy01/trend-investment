"""跨股票掃描器：依選定策略找出目前有進場訊號的熱門股，可依「剛觸發訊號」或
「訊號後漲幅最高」兩種方式排序——前者用來發現還沒漲多的新機會，後者用來確認
目前動能最強勢的股票（但也代表可能已經漲多，追高風險較高）。

另外提供「訊號前漲幅」濾網：趨勢跟隨策略本質上是等趨勢被確認才進場，確認需要
時間，所以訊號天生落後。實測台股 30 檔 3 年（均線交叉 20/60）：訊號觸發時距離
前波低點已經過了 30 個交易日、平均已漲 12.5%，其中 24% 的訊號是在股票已經漲
超過 20% 之後才出現。把均線調快救不了這件事（5/20 反而更落後），只能靠這道
濾網把「漲太多才確認」的訊號擋掉。
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from . import data_loader
from . import strategies as strategies_module

# 計算「訊號前漲幅」時往回看幾個交易日找前波低點。
RUNUP_LOOKBACK = 60


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

    entry_pos = signal_df.index.get_loc(entry_date)
    window = df.iloc[max(0, entry_pos - RUNUP_LOOKBACK): entry_pos + 1]
    swing_low = float(window["Low"].min()) if not window.empty else float("nan")
    runup = entry_price / swing_low - 1 if swing_low > 0 else float("nan")

    return {
        "進場日期": entry_date,
        "進場價格": entry_price,
        "現價": latest_price,
        "訊號前漲幅": runup,
        "訊號後漲幅": latest_price / entry_price - 1,
        "持有天數": (signal_df.index[-1] - entry_date).days,
    }


def resolve_universe(market: str, universe_size: int | None) -> tuple[dict[str, str], bool]:
    """決定要掃哪些股票。回傳 (股票池, 是否已退回內建清單)。

    universe_size 為 None 時用內建的精選清單；台股給定數字時改抓證交所全市場、
    依成交金額取前 N 檔。美股沒有對等的免費全市場 API，一律用內建清單。
    """
    fallback = data_loader.POPULAR_TICKERS.get(market, {})
    if market != "台股" or not universe_size:
        return fallback, False

    universe = data_loader.fetch_twse_universe(universe_size)
    if not universe:
        return fallback, True
    return universe, False


@st.cache_data(ttl=1800, show_spinner=False)
def scan_market(
    market: str,
    strategy_key: str,
    params_items: tuple[tuple[str, float], ...],
    start_date: dt.date,
    end_date: dt.date,
    universe_size: int | None = None,
) -> pd.DataFrame:
    """掃描指定市場的熱門標的，回傳「所有」目前有進場訊號的股票（未排序、未篩選筆數）。

    刻意不在這裡排序或截斷——排序方式（剛觸發 vs 漲幅最高）是使用者可切換的顯示選項，
    不該影響昂貴的下載＋掃描這一步；掃描結果快取起來，切換排序時直接用 rank_signals()
    重新排就好，不用重新下載。
    """
    strategy = strategies_module.get_strategy(strategy_key)
    params = dict(params_items)

    universe, _ = resolve_universe(market, universe_size)
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


def rank_signals(
    df: pd.DataFrame,
    sort_by: str = "fresh",
    top_n: int = 10,
    max_runup: float | None = None,
    max_age_days: int | None = None,
) -> tuple[pd.DataFrame, dict]:
    """把 scan_market() 的掃描結果依指定方式排序、取前 N 名。

    sort_by："fresh" 依「持有天數」由小到大（訊號最新觸發優先，用來發現還沒漲多的新機會）；
             "strength" 依「訊號後漲幅」由高到低（目前動能最強勢，但也可能已經漲多）。
    max_runup：訊號前漲幅上限（排除訊號觸發時已從前波低點漲太多的標的）。
    max_age_days：訊號年齡上限。只排序不過濾的話，候選不足時會拿幾個月前的舊訊號來補滿
                  前 10 名，看起來就像「排行榜永遠是那幾檔」——因為那些訊號本來就沒變。

    回傳 (排名後的結果, 統計 dict)。排序與過濾都在這裡做、不在 scan_market，
    這樣切換設定時直接重算就好，不必重新下載整個股票池。
    """
    stats = {"候選": int(len(df)), "漲幅濾掉": 0, "太舊濾掉": 0}
    if df.empty:
        return df, stats

    filtered = df
    if max_runup is not None and "訊號前漲幅" in filtered.columns:
        keep = filtered["訊號前漲幅"].isna() | (filtered["訊號前漲幅"] <= max_runup)
        stats["漲幅濾掉"] = int((~keep).sum())
        filtered = filtered[keep]

    if max_age_days is not None and not filtered.empty:
        keep = filtered["持有天數"] <= max_age_days
        stats["太舊濾掉"] = int((~keep).sum())
        filtered = filtered[keep]

    if filtered.empty:
        return filtered, stats

    if sort_by == "strength":
        sort_col, ascending = "訊號後漲幅", False
    else:
        sort_col, ascending = "持有天數", True

    result = filtered.sort_values(sort_col, ascending=ascending).head(top_n).reset_index(drop=True)
    result.insert(0, "排名", range(1, len(result) + 1))
    return result, stats
