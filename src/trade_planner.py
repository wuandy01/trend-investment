"""進出場點規劃器：固定停損停利 (ATR bracket)，含本機交易紀錄儲存。

跟海龜法則不同，這裡不追蹤移動停利，就是「進場價 ± N×ATR」兩條固定線，
價格先碰到哪條就出場，適合想要一眼看到明確停損停利價位的用法。

交易紀錄用本機 CSV 儲存（data/trade_log.csv），僅在本機執行時可靠；部署到
Streamlit Cloud 等雲端平台後，檔案系統通常是暫時性的，重新部署就會消失，
不能當作長期保存交易紀錄的地方。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from . import indicators as ind

TRADE_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "trade_log.csv"

TRADE_LOG_COLUMNS = [
    "記錄時間",
    "代碼",
    "方向",
    "進場日期",
    "進場價",
    "N (ATR)",
    "停損倍數",
    "停利倍數",
    "停損價",
    "停利價",
    "備註",
]


def compute_bracket(
    entry_price: float,
    atr_value: float,
    direction: str,
    stop_mult: float,
    target_mult: float,
) -> dict:
    """算出停損價、停利價、風報比。direction 為 '多' 或 '空'。"""
    sign = 1 if direction == "多" else -1
    stop_price = entry_price - sign * stop_mult * atr_value
    target_price = entry_price + sign * target_mult * atr_value
    return {
        "停損價": stop_price,
        "停利價": target_price,
        "風報比": target_mult / stop_mult if stop_mult > 0 else float("nan"),
    }


def get_atr_at_date(df: pd.DataFrame, date: dt.date, atr_period: int) -> float | None:
    """回傳指定日期（或往前最近一個交易日）的 ATR 值。"""
    atr_series = ind.atr(df["High"], df["Low"], df["Close"], period=atr_period).dropna()
    valid = atr_series[atr_series.index.date <= date]
    if valid.empty:
        return None
    return float(valid.iloc[-1])


def check_bracket_outcome(
    df: pd.DataFrame,
    entry_date: dt.date,
    entry_price: float,
    stop_price: float,
    target_price: float,
    direction: str,
    max_hold_days: int | None = None,
) -> dict:
    """檢查進場後，價格是先碰到停損還是停利（用 High/Low 判斷，取先發生的那個）。

    entry_price 由呼叫端明確傳入（可能是使用者手動修改過的實際成交價），
    報酬率一律以這個價格為基準計算，才會跟畫面上顯示的停損停利價位一致。

    max_hold_days：若設定，且進場後一直沒觸價，超過這個天數（日曆天）時狀態會
    標成「已超時」，提醒使用者回來重新評估——純粹是提醒，不會真的幫你出場。
    """
    after = df[df.index.date >= entry_date]
    if after.empty:
        return {"狀態": "無資料", "觸價日期": None, "觸價價格": None, "報酬率": None, "持有天數": 0}

    sign = 1 if direction == "多" else -1
    for date, row in after.iterrows():
        hit_stop = (row["Low"] <= stop_price) if sign == 1 else (row["High"] >= stop_price)
        hit_target = (row["High"] >= target_price) if sign == 1 else (row["Low"] <= target_price)
        hold_days = (date.date() - entry_date).days
        # 同一天兩者都可能觸價時，保守起見優先算停損（無法從日 K 判斷盤中先後順序）
        if hit_stop:
            return {
                "狀態": "已停損",
                "觸價日期": date,
                "觸價價格": stop_price,
                "報酬率": sign * (stop_price / entry_price - 1),
                "持有天數": hold_days,
            }
        if hit_target:
            return {
                "狀態": "已停利",
                "觸價日期": date,
                "觸價價格": target_price,
                "報酬率": sign * (target_price / entry_price - 1),
                "持有天數": hold_days,
            }

    latest_date = after.index[-1]
    latest_price = float(after["Close"].iloc[-1])
    hold_days = (latest_date.date() - entry_date).days
    status = "持有中"
    if max_hold_days is not None and hold_days > max_hold_days:
        status = "已超時"
    return {
        "狀態": status,
        "觸價日期": None,
        "觸價價格": latest_price,
        "報酬率": sign * (latest_price / entry_price - 1),
        "持有天數": hold_days,
    }


def summarize_holding_period(
    df: pd.DataFrame,
    entry_date: dt.date,
    entry_price: float,
    direction: str,
    as_of_date: dt.date | None = None,
) -> dict | None:
    """整理進場後到目前（或到 as_of_date，例如已觸價的那天）為止的價量表現。

    最大有利／不利變動（MFE／MAE）依方向調整正負號，正值＝對部位有利、
    負值＝帳面虧損，方便看這筆交易帳面最深虧過多少、最高賺過多少。
    量能變化＝持有期間平均成交量，相對進場前 20 個交易日均量的變化幅度，
    用來判斷進場後量能是放大還是萎縮。
    """
    period = df[df.index.date >= entry_date]
    if as_of_date is not None:
        period = period[period.index.date <= as_of_date]
    if period.empty:
        return None

    period_high = float(period["High"].max())
    period_low = float(period["Low"].min())
    if direction == "多":
        mfe = period_high / entry_price - 1
        mae = period_low / entry_price - 1
    else:
        mfe = entry_price / period_low - 1
        mae = entry_price / period_high - 1

    before = df[df.index.date < entry_date].tail(20)
    avg_volume_before = float(before["Volume"].mean()) if not before.empty else float("nan")
    avg_volume_period = float(period["Volume"].mean())
    volume_change = (
        avg_volume_period / avg_volume_before - 1
        if avg_volume_before == avg_volume_before and avg_volume_before > 0
        else float("nan")
    )

    return {
        "交易天數": len(period),
        "期間最高價": period_high,
        "期間最低價": period_low,
        "最大有利變動": mfe,
        "最大不利變動": mae,
        "期間平均成交量": avg_volume_period,
        "進場前20日均量": avg_volume_before,
        "量能變化": volume_change,
        "逐日資料": period[["Open", "High", "Low", "Close", "Volume"]].round(2),
    }


def load_trade_log() -> pd.DataFrame:
    if not TRADE_LOG_PATH.exists():
        return pd.DataFrame(columns=TRADE_LOG_COLUMNS)
    return pd.read_csv(TRADE_LOG_PATH, parse_dates=["進場日期"])


def append_trade_record(record: dict) -> None:
    TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_row = pd.DataFrame([record], columns=TRADE_LOG_COLUMNS)
    df = load_trade_log()
    df = new_row if df.empty else pd.concat([df, new_row], ignore_index=True)
    df.to_csv(TRADE_LOG_PATH, index=False)
