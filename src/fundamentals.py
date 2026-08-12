"""從 Yahoo Finance 抓取基本面財務數據：公司資訊、關鍵比率、三大財報、分析師評等。"""

from __future__ import annotations

import pandas as pd
import streamlit as st
import yfinance as yf

INCOME_ITEMS = ["Total Revenue", "Gross Profit", "Operating Income", "EBITDA", "Net Income", "Diluted EPS"]
BALANCE_ITEMS = [
    "Total Assets",
    "Total Liabilities Net Minority Interest",
    "Stockholders Equity",
    "Total Debt",
    "Cash And Cash Equivalents",
    "Working Capital",
]
CASHFLOW_ITEMS = ["Operating Cash Flow", "Capital Expenditure", "Free Cash Flow", "Cash Dividends Paid"]

ITEM_LABELS_ZH = {
    "Total Revenue": "營業收入",
    "Gross Profit": "毛利",
    "Operating Income": "營業利益",
    "EBITDA": "EBITDA",
    "Net Income": "淨利",
    "Diluted EPS": "稀釋 EPS",
    "Total Assets": "總資產",
    "Total Liabilities Net Minority Interest": "總負債",
    "Stockholders Equity": "股東權益",
    "Total Debt": "有息負債",
    "Cash And Cash Equivalents": "現金及約當現金",
    "Working Capital": "營運資金",
    "Operating Cash Flow": "營業現金流",
    "Capital Expenditure": "資本支出",
    "Free Cash Flow": "自由現金流",
    "Cash Dividends Paid": "現金股利發放",
}

PER_SHARE_ITEMS = {"Diluted EPS"}


@st.cache_data(ttl=3600, show_spinner=False)
def get_company_info(ticker: str) -> dict:
    """公司基本資料與關鍵比率（yfinance .info）。抓不到時回傳空 dict。"""
    try:
        return yf.Ticker(ticker).get_info() or {}
    except Exception:
        return {}


@st.cache_data(ttl=3600, show_spinner=False)
def get_financial_statements(ticker: str) -> dict[str, pd.DataFrame]:
    """三大財報（損益表／資產負債表／現金流量表）的精選常用項目，年度資料。"""
    t = yf.Ticker(ticker)

    def _safe(df_getter, items):
        try:
            df = df_getter()
            return df.reindex(items).dropna(how="all")
        except Exception:
            return pd.DataFrame()

    return {
        "income": _safe(lambda: t.financials, INCOME_ITEMS),
        "balance": _safe(lambda: t.balance_sheet, BALANCE_ITEMS),
        "cashflow": _safe(lambda: t.cashflow, CASHFLOW_ITEMS),
    }


@st.cache_data(ttl=3600, show_spinner=False)
def get_recommendations(ticker: str) -> pd.DataFrame:
    """分析師評等歷史（strongBuy/buy/hold/sell/strongSell 人數）。"""
    try:
        rec = yf.Ticker(ticker).recommendations
        return rec if rec is not None else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def format_large_number(value) -> str:
    """把大數字轉成「億／萬／兆」等易讀單位（中文財經慣例）。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    sign = "-" if value < 0 else ""
    abs_v = abs(value)
    if abs_v >= 1e12:
        return f"{sign}{abs_v / 1e12:,.2f} 兆"
    if abs_v >= 1e8:
        return f"{sign}{abs_v / 1e8:,.2f} 億"
    if abs_v >= 1e4:
        return f"{sign}{abs_v / 1e4:,.2f} 萬"
    return f"{sign}{abs_v:,.2f}"


def format_statement_table(df: pd.DataFrame) -> pd.DataFrame:
    """把財報 DataFrame 轉成適合顯示的格式：年份欄名、中文項目名、易讀數字。"""
    if df.empty:
        return df
    columns = [c.strftime("%Y") if hasattr(c, "strftime") else str(c) for c in df.columns]
    rows = {}
    for idx in df.index:
        if idx in PER_SHARE_ITEMS:
            formatted = [f"{v:,.2f}" if pd.notna(v) else "—" for v in df.loc[idx]]
        else:
            formatted = [format_large_number(v) for v in df.loc[idx]]
        rows[ITEM_LABELS_ZH.get(idx, idx)] = formatted
    return pd.DataFrame.from_dict(rows, orient="index", columns=columns)
