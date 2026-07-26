"""股票資料下載與代碼處理（台股 / 美股，透過 yfinance）。"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st
import yfinance as yf

# 常見股票清單，供下拉選單使用；使用者也可以自行輸入代碼。
POPULAR_TICKERS = {
    "台股": {
        "2330.TW": "台積電",
        "2317.TW": "鴻海",
        "2454.TW": "聯發科",
        "2412.TW": "中華電",
        "2308.TW": "台達電",
        "2882.TW": "國泰金",
        "2881.TW": "富邦金",
        "1301.TW": "台塑",
        "2603.TW": "長榮",
        "3008.TW": "大立光",
        "0050.TW": "元大台灣50",
        "0056.TW": "元大高股息",
    },
    "美股": {
        "AAPL": "Apple",
        "MSFT": "Microsoft",
        "GOOGL": "Alphabet",
        "AMZN": "Amazon",
        "NVDA": "NVIDIA",
        "TSLA": "Tesla",
        "META": "Meta",
        "AVGO": "Broadcom",
        "SPY": "S&P 500 ETF",
        "QQQ": "Nasdaq 100 ETF",
        "VOO": "Vanguard S&P 500 ETF",
        "VTI": "Vanguard Total Market ETF",
    },
}

MARKET_CURRENCY = {"台股": "NT$", "美股": "US$"}


def normalize_ticker(raw_code: str, market: str) -> str:
    """把使用者輸入轉換成 yfinance 認得的代碼。"""
    code = raw_code.strip().upper()
    if not code:
        return code
    if market == "台股":
        # 允許輸入純數字代碼（例如 2330），自動補上 .TW
        if code.replace(".TW", "").replace(".TWO", "").isdigit():
            if not (code.endswith(".TW") or code.endswith(".TWO")):
                code = f"{code}.TW"
    return code


@st.cache_data(ttl=3600, show_spinner=False)
def load_price_data(
    ticker: str,
    start_date: dt.date,
    end_date: dt.date,
) -> pd.DataFrame:
    """下載指定股票的歷史 OHLCV 資料，並回傳整理過的 DataFrame。"""
    if not ticker:
        return pd.DataFrame()

    df = yf.download(
        ticker,
        start=start_date,
        end=end_date + dt.timedelta(days=1),
        auto_adjust=True,
        progress=False,
        multi_level_index=False,
    )

    if df is None or df.empty:
        return pd.DataFrame()

    # yfinance 有時會回傳 MultiIndex 欄位（即使單一代碼），保險起見攤平。
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.rename(columns=str.title)
    keep_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[keep_cols].dropna(subset=["Close"])
    df.index.name = "Date"
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def get_company_name(ticker: str) -> str | None:
    """嘗試取得股票的公司名稱，抓不到就回傳 None（不影響主流程）。"""
    try:
        info = yf.Ticker(ticker).get_info()
        return info.get("longName") or info.get("shortName")
    except Exception:
        return None
