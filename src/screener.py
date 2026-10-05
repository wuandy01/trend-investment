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

# 相對強弱的回看期間（約 6 個月）、短期均線天數，以及 52 週高點的回看期間。
RS_LOOKBACK = 120
EXTENSION_MA = 20
HIGH_LOOKBACK = 250

# 大盤環境濾網：指數與判斷用的均線天數。
MARKET_INDEX = {"台股": "^TWII", "美股": "^GSPC"}
REGIME_MA = 200


@st.cache_data(ttl=3600, show_spinner=False)
def market_regime(market: str, start_date: dt.date, end_date: dt.date) -> pd.Series:
    """回傳大盤指數每日「是否站上 200 日均線」的布林序列。

    動能策略在空頭會系統性失效：實測台股 2022 年全年進場的 986 筆訊號，
    不論用哪種出場方式平均 R 都趨近於零（+0.036 ~ -0.044），而同一套規則在
    多頭期是 +0.5R 以上。空頭的解法是「不要進場」，不是「出場出得更快」。
    """
    index_ticker = MARKET_INDEX.get(market)
    if not index_ticker:
        return pd.Series(dtype=bool)
    df = data_loader.load_price_data(index_ticker, start_date - dt.timedelta(days=400), end_date)
    if df.empty:
        return pd.Series(dtype=bool)
    ma = df["Close"].rolling(REGIME_MA).mean()
    return (df["Close"] > ma).dropna()


def _strength_metrics(price_data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """算出整個股票池每檔的相對強弱與短期乖離率。

    相對強弱＝近 RS_LOOKBACK 個交易日報酬在「整個掃描池」中的百分位。這是整個
    排行榜唯一真正衡量「強勢」的指標——在此之前排序只看訊號新舊，所以掃出來的
    常常是剛好穿越均線的牛皮股，跟強勢無關。

    距52週高＝現價相對過去一年最高價的距離（0 代表正在創新高，-0.2 代表比高點低 20%）。
    這是實測中最強的單一篩選指標：在半年報酬前 30% 的母體裡，套用實際交易規則
    （2N 停損 + 跌破 50MA 停利）後，距高點 3% 以內的平均 +1.491R，
    低於高點 25% 以上的只有 +0.388R，差距接近四倍。

    乖離率＝現價相對 EXTENSION_MA 日均線的距離，保留為參考欄位。原本用它當「不追高」
    的濾網是錯的——實測越延伸的未來表現越好（高於 20MA 20% 以上 +1.087R，
    高於 0~8% 只有 +0.907R，低於 20MA 更只有 +0.427R）。動能的經驗法則是
    「買高、賣更高」，刻意挑回檔的等於挑動能正在衰退的那一群。
    """
    rows = []
    for ticker, df in price_data.items():
        if len(df) < RS_LOOKBACK + 5:
            continue
        close = df["Close"]
        ma = close.rolling(EXTENSION_MA).mean().iloc[-1]
        past = close.iloc[-RS_LOOKBACK]
        if past <= 0 or ma != ma or ma <= 0:
            continue
        year_high = float(df["High"].iloc[-HIGH_LOOKBACK:].max())
        rows.append({
            "代碼": ticker,
            "半年報酬": close.iloc[-1] / past - 1,
            "乖離率": close.iloc[-1] / ma - 1,
            "距52週高": close.iloc[-1] / year_high - 1 if year_high > 0 else float("nan"),
        })
    if not rows:
        return pd.DataFrame(columns=["代碼", "半年報酬", "乖離率", "距52週高", "相對強弱"])

    metrics = pd.DataFrame(rows)
    metrics["相對強弱"] = metrics["半年報酬"].rank(pct=True)
    return metrics


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

    universe_size 為 None 時用內建的精選清單；給定數字時改抓全市場、依成交金額取前 N 檔
    （台股用證交所 OpenAPI，美股用 NASDAQ 公開選股器）。抓取失敗則退回內建清單。
    """
    fallback = data_loader.POPULAR_TICKERS.get(market, {})
    if not universe_size:
        return fallback, False

    if market == "台股":
        universe = data_loader.fetch_twse_universe(universe_size)
    elif market == "美股":
        universe = data_loader.fetch_us_universe(universe_size)
    else:
        return fallback, False

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

    result = pd.DataFrame(rows)
    # 相對強弱要以「整個掃描池」為母體計算，不能只排有訊號的那些，
    # 否則百分位會失真（有訊號的股票本來就偏強）。
    metrics = _strength_metrics(price_data)
    if not metrics.empty:
        result = result.merge(metrics, on="代碼", how="left")

    regime = market_regime(market, start_date, end_date)
    if not regime.empty:
        regime_by_date = {d.date(): bool(v) for d, v in regime.items()}
        result["進場時大盤多頭"] = result["進場日期"].map(
            lambda d: regime_by_date.get(d.date() if hasattr(d, "date") else d)
        )
    return result


def rank_signals(
    df: pd.DataFrame,
    sort_by: str = "fresh",
    top_n: int = 10,
    max_runup: float | None = None,
    max_age_days: int | None = None,
    min_rs: float | None = None,
    max_extension: float | None = None,
    max_below_high: float | None = None,
    require_bull_regime: bool = False,
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
    stats = {
        "候選": int(len(df)), "大盤空頭濾掉": 0, "不夠強濾掉": 0,
        "離高點太遠濾掉": 0, "追高濾掉": 0, "漲幅濾掉": 0, "太舊濾掉": 0,
    }
    if df.empty:
        return df, stats

    filtered = df
    if require_bull_regime and "進場時大盤多頭" in filtered.columns:
        keep = filtered["進場時大盤多頭"].isna() | filtered["進場時大盤多頭"].astype(bool)
        stats["大盤空頭濾掉"] = int((~keep).sum())
        filtered = filtered[keep]

    # NaN 代表資料不足以判斷（例如上市未滿半年），不能視為通過——否則新上市股會
    # 繞過強弱與位置這兩道濾網直接進榜。
    if min_rs is not None and "相對強弱" in filtered.columns and not filtered.empty:
        keep = filtered["相對強弱"].notna() & (filtered["相對強弱"] >= min_rs)
        stats["不夠強濾掉"] = int((~keep).sum())
        filtered = filtered[keep]

    if max_below_high is not None and "距52週高" in filtered.columns and not filtered.empty:
        keep = filtered["距52週高"].notna() & (filtered["距52週高"] >= -max_below_high)
        stats["離高點太遠濾掉"] = int((~keep).sum())
        filtered = filtered[keep]

    # 乖離率濾網保留但預設不啟用——實測越延伸表現越好，刻意限制反而挑到動能衰退的。
    if max_extension is not None and "乖離率" in filtered.columns and not filtered.empty:
        keep = filtered["乖離率"].isna() | (filtered["乖離率"].abs() <= max_extension)
        stats["追高濾掉"] = int((~keep).sum())
        filtered = filtered[keep]

    if max_runup is not None and "訊號前漲幅" in filtered.columns and not filtered.empty:
        keep = filtered["訊號前漲幅"].isna() | (filtered["訊號前漲幅"] <= max_runup)
        stats["漲幅濾掉"] = int((~keep).sum())
        filtered = filtered[keep]

    if max_age_days is not None and not filtered.empty:
        keep = filtered["持有天數"] <= max_age_days
        stats["太舊濾掉"] = int((~keep).sum())
        filtered = filtered[keep]

    if filtered.empty:
        return filtered, stats

    if sort_by == "rs" and "相對強弱" in filtered.columns:
        sort_col, ascending = "相對強弱", False
    elif sort_by == "strength":
        sort_col, ascending = "訊號後漲幅", False
    else:
        sort_col, ascending = "持有天數", True

    result = filtered.sort_values(sort_col, ascending=ascending).head(top_n).reset_index(drop=True)
    result.insert(0, "排名", range(1, len(result) + 1))
    return result, stats
