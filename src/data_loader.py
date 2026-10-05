"""股票資料下載與代碼處理（台股 / 美股，透過 yfinance）。"""

from __future__ import annotations

import datetime as dt
import json
import urllib.request

import pandas as pd
import streamlit as st
import yfinance as yf

# 證交所每日收盤行情 OpenAPI（公開、免金鑰），用來取得「全上市股票 + 當日成交金額」，
# 讓熱門強勢股掃描可以從真正的全市場挑標的，而不是只掃下面那份手動維護的清單。
TWSE_DAILY_API = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"

# 美股沒有對等的官方免費 API，改用 NASDAQ 的公開選股器端點（約 7,000 檔，含成交量與市值）。
NASDAQ_SCREENER_API = (
    "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&download=true"
)

# 常見股票清單，供下拉選單與熱門強勢股掃描使用；使用者也可以自行輸入代碼。
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
        "2891.TW": "中信金",
        "2886.TW": "兆豐金",
        "1216.TW": "統一",
        "2002.TW": "中鋼",
        "2207.TW": "和泰車",
        "2382.TW": "廣達",
        "2357.TW": "華碩",
        "3711.TW": "日月光投控",
        "1303.TW": "南亞",
        "6505.TW": "台塑化",
        "2884.TW": "玉山金",
        "2892.TW": "第一金",
        "5880.TW": "合庫金",
        "2880.TW": "華南金",
        "2885.TW": "元大金",
        "3034.TW": "聯詠",
        "2379.TW": "瑞昱",
        "2409.TW": "友達",
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
        "BRK-B": "Berkshire Hathaway",
        "JPM": "JPMorgan Chase",
        "V": "Visa",
        "MA": "Mastercard",
        "UNH": "UnitedHealth",
        "HD": "Home Depot",
        "PG": "Procter & Gamble",
        "JNJ": "Johnson & Johnson",
        "XOM": "ExxonMobil",
        "COST": "Costco",
        "ABBV": "AbbVie",
        "MRK": "Merck",
        "AMD": "AMD",
        "NFLX": "Netflix",
        "ADBE": "Adobe",
        "CRM": "Salesforce",
        "ORCL": "Oracle",
        "BAC": "Bank of America",
    },
}

MARKET_CURRENCY = {"台股": "NT$", "美股": "US$"}


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_twse_universe(top_n: int) -> dict[str, str]:
    """從證交所 OpenAPI 取得全上市股票，依當日成交金額由高到低取前 top_n 檔。

    POPULAR_TICKERS 那份 30 檔的手動清單有兩個問題：數量太少（掃出來永遠是那幾檔），
    而且它是「知名大型股」而非「成交熱絡的股票」——實測成交金額前 12 名裡有 9 檔
    根本不在清單內。這裡改用成交金額排序，「熱門」才名符其實。

    只保留 4 碼純數字代碼（排除 00 開頭的 ETF、5 碼以上的權證與特別股）。
    抓取失敗時回傳空 dict，由呼叫端決定要不要退回內建清單。
    """
    try:
        req = urllib.request.Request(TWSE_DAILY_API, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            rows = json.load(resp)
    except Exception:
        return {}

    def turnover(row: dict) -> float:
        try:
            return float(row.get("TradeValue") or 0)
        except (TypeError, ValueError):
            return 0.0

    stocks = [
        r for r in rows
        if len(r.get("Code", "")) == 4 and r["Code"].isdigit() and not r["Code"].startswith("00")
    ]
    stocks.sort(key=turnover, reverse=True)
    return {f"{r['Code']}.TW": r.get("Name", r["Code"]) for r in stocks[:top_n]}


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_us_universe(top_n: int) -> dict[str, str]:
    """從 NASDAQ 公開選股器取得全美股，依「成交金額＝股價 × 成交量」取前 top_n 檔。

    跟台股用成交金額排序的理由相同：內建的 30 檔是知名大型股，不是成交熱絡的股票。
    排除代碼含 `^` `/` 的特別股與權證，以及股價低於 1 美元的雞蛋水餃股
    （這類標的買賣價差大、ATR 停損算出來會失真）。

    抓取失敗時回傳空 dict，由呼叫端決定要不要退回內建清單。
    """
    try:
        req = urllib.request.Request(
            NASDAQ_SCREENER_API,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            rows = json.load(resp)["data"]["rows"]
    except Exception:
        return {}

    def parse_money(value: str) -> float:
        try:
            return float(str(value).replace("$", "").replace(",", "") or 0)
        except (TypeError, ValueError):
            return 0.0

    cleaned = []
    for r in rows:
        symbol = (r.get("symbol") or "").strip()
        price = parse_money(r.get("lastsale"))
        volume = parse_money(r.get("volume"))
        if not symbol or "^" in symbol or "/" in symbol or price < 1.0:
            continue
        cleaned.append((price * volume, symbol, (r.get("name") or symbol).strip()))

    cleaned.sort(reverse=True)
    return {sym: name for _, sym, name in cleaned[:top_n]}


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


@st.cache_data(ttl=1800, show_spinner=False)
def load_universe_prices(
    tickers: tuple[str, ...],
    start_date: dt.date,
    end_date: dt.date,
) -> dict[str, pd.DataFrame]:
    """一次批次下載多檔股票的歷史資料，回傳 {代碼: DataFrame}（下載失敗的代碼會被跳過）。"""
    if not tickers:
        return {}

    raw = yf.download(
        list(tickers),
        start=start_date,
        end=end_date + dt.timedelta(days=1),
        auto_adjust=True,
        progress=False,
        group_by="ticker",
        threads=True,
    )

    result: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return result

    for ticker in tickers:
        try:
            sub = raw[ticker] if isinstance(raw.columns, pd.MultiIndex) else raw
            sub = sub.rename(columns=str.title)
            keep_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in sub.columns]
            sub = sub[keep_cols].dropna(subset=["Close"])
            if not sub.empty:
                sub.index.name = "Date"
                result[ticker] = sub
        except (KeyError, AttributeError):
            continue
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def get_company_name(ticker: str) -> str | None:
    """嘗試取得股票的公司名稱，抓不到就回傳 None（不影響主流程）。"""
    try:
        info = yf.Ticker(ticker).get_info()
        return info.get("longName") or info.get("shortName")
    except Exception:
        return None
