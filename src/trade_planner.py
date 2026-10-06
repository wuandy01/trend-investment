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
    "停利方式",
    "股數",
    "進場理由",
    "備註",
]

# 「停利方式」欄位的固定倍數選項值；其餘值代表結構停利訊號的名稱（見 STRUCTURAL_EXITS）。
# 舊版紀錄沒有這個欄位，load_trade_log() 會補成固定倍數，維持原本的判斷方式。
FIXED_TARGET_LABEL = "固定 N 倍數"

# 舊版紀錄沒有「股數」與「進場理由」欄位，load_trade_log() 會補上空值，
# 不需要手動改既有的 CSV。
ENTRY_REASONS = [
    "突破整理",
    "回檔支撐",
    "均線多頭排列",
    "強勢股排行選入",
    "基本面轉佳",
    "其他",
]


def _exit_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """算出結構停利會用到的指標。"""
    close = df["Close"]
    out = pd.DataFrame(index=df.index)
    out["ema10"] = close.ewm(span=10).mean()
    out["ema20"] = close.ewm(span=20).mean()
    out["ma50"] = close.rolling(50).mean()
    ema12, ema26 = close.ewm(span=12).mean(), close.ewm(span=26).mean()
    macd = ema12 - ema26
    out["macd_hist"] = macd - macd.ewm(span=9).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14).mean()
    out["rsi"] = 100 - 100 / (1 + gain / loss)
    out["low10"] = df["Low"].rolling(10).min().shift(1)
    out["low20"] = df["Low"].rolling(20).min().shift(1)
    return out


# 結構停利方式。數值為台股全市場 400 檔、5,317 筆歷史訊號（2021 起、2N 停損、
# 不設啟動門檻、3 個月時間上限，也就是目前的預設設定）的實測結果。
# 共通的取捨是：出場越慢，平均 R 越高但勝率越低、連續虧損期越長。
# 「最大連虧」是所有訊號依日期排序後的最長連續虧損筆數，衡量的是心理上撐不撐得住。
STRUCTURAL_EXITS = {
    "跌破 50 日均線": {
        "fn": lambda row, ind: row["Close"] < ind["ma50"],
        "說明": "平均 +0.446R、勝率 39.7%、平均持有 36 天、最大連虧 76 次。R 最高，但十筆有六筆要認賠。",
    },
    "跌破前 20 日低點": {
        "fn": lambda row, ind: row["Low"] <= ind["low20"],
        "說明": "平均 +0.447R、勝率 36.8%、平均持有 37 天、最大連虧 82 次。R 與 50 日均線相當但更難執行。",
    },
    "跌破前 10 日低點": {
        "fn": lambda row, ind: row["Low"] <= ind["low10"],
        "說明": "平均 +0.363R、勝率 44.4%、平均持有 27 天、最大連虧 47 次。海龜法則的短期出場。",
    },
    "RSI(14) 跌破 50": {
        "fn": lambda row, ind: ind["rsi"] < 50,
        "說明": "平均 +0.360R、勝率 45.3%、平均持有 27 天、最大連虧 24 次。動能消失就走。",
    },
    "跌破 20 日 EMA": {
        "fn": lambda row, ind: row["Close"] < ind["ema20"],
        "說明": "平均 +0.348R、勝率 47.3%、平均持有 24 天、最大連虧 22 次。想多拿點 R 又不想等太久的折衷。",
    },
    "跌破 10 日 EMA": {
        "fn": lambda row, ind: row["Close"] < ind["ema10"],
        "說明": "平均 +0.241R、勝率 53.0%、平均持有 17 天、最大連虧 15 次。R 不高，但連虧期全場最短，最容易執行。",
    },
    "MACD 柱狀體轉負": {
        "fn": lambda row, ind: ind["macd_hist"] < 0,
        "說明": "平均 +0.241R、勝率 55.0%、平均持有 15 天、最大連虧 28 次。勝率最高，但連虧期是 10 日 EMA 的兩倍。",
    },
}

# 預設的結構停利訊號。選它不是因為 R 最高（50 日均線的 R 高出 85%），
# 而是因為它的最大連續虧損只有 15 次——全部七個選項裡最短。
# 抱久一點的規則賺得多但會連輸幾十次，中途改規則就會剛好砍掉那幾筆大的，
# 所以「撐得住」比「帳面上最佳」重要。
DEFAULT_STRUCTURAL_EXIT = "跌破 10 日 EMA"


def check_structural_outcome(
    df: pd.DataFrame,
    entry_date: dt.date,
    entry_price: float,
    stop_price: float,
    exit_key: str,
    atr_value: float,
    min_profit_n: float = 0.0,
    max_hold_days: int | None = None,
) -> dict:
    """停損用固定價位（處理「看錯了」），停利用結構訊號（處理「這段走完了」）。

    min_profit_n：獲利達到幾個 N 之後才讓結構停利生效。門檻拉高等於強迫部位
    多抱一段，R 會上升但勝率下降。實測 5,317 筆（2N 停損、跌破 10 日 EMA 停利、
    3 個月上限）：

        門檻 0N   → 平均 +0.241R、勝率 53.0%、最大連虧 15 次   ← 預設
        門檻 0.5N → 平均 +0.301R、勝率 48.4%、最大連虧 28 次
        門檻 1N   → 平均 +0.334R、勝率 43.5%、最大連虧 43 次
        門檻 2N   → 平均 +0.391R、勝率 37.3%、最大連虧 76 次

    R 一路單調上升，但連續虧損筆數上升得更快。預設取 0N 是刻意選「撐得住」
    而不是「帳面最佳」：拉高門檻賺到的 R 來自少數抱很久的部位，中途受不了
    改規則就會剛好砍掉那幾筆。而且樣本期間（2021-2026 台股）多頭佔絕大部分，
    任何「抱更久」的規則在這段資料上都會佔便宜。
    """
    rule = STRUCTURAL_EXITS.get(exit_key)
    if rule is None:
        raise KeyError(f"未知的結構停利方式：{exit_key}")

    after = df[df.index.date >= entry_date]
    if after.empty:
        return {"狀態": "無資料", "觸價日期": None, "觸價價格": None, "報酬率": None, "持有天數": 0}

    indicators = _exit_indicators(df)
    profit_gate = entry_price + min_profit_n * atr_value

    for i, (date, row) in enumerate(after.iterrows()):
        hold_days = (date.date() - entry_date).days
        if row["Low"] <= stop_price:
            return {
                "狀態": "已停損", "觸價日期": date, "觸價價格": stop_price,
                "報酬率": stop_price / entry_price - 1, "持有天數": hold_days,
            }
        ind_row = indicators.loc[date]
        if i > 0 and row["Close"] >= profit_gate and bool(rule["fn"](row, ind_row)):
            exit_price = float(row["Close"])
            return {
                "狀態": "已停利", "觸價日期": date, "觸價價格": exit_price,
                "報酬率": exit_price / entry_price - 1, "持有天數": hold_days,
            }

    latest_price = float(after["Close"].iloc[-1])
    hold_days = (after.index[-1].date() - entry_date).days
    status = "已超時" if max_hold_days is not None and hold_days > max_hold_days else "持有中"
    return {
        "狀態": status, "觸價日期": None, "觸價價格": latest_price,
        "報酬率": latest_price / entry_price - 1, "持有天數": hold_days,
    }


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


def compute_position_size(
    capital: float,
    risk_pct: float,
    entry_price: float,
    stop_price: float,
    lot_size: int = 1,
) -> dict | None:
    """固定比例風險部位計算 (fixed fractional position sizing)。

    核心概念：先決定「這筆交易最多願意虧多少錢」，再由停損距離反推該買多少股，
    而不是先決定買多少股、再看會虧多少。

        部位股數 = (總資金 × 單筆風險%) ÷ (進場價 − 停損價)

    這樣不論停損設得寬或窄，每筆交易的風險金額都一樣。這件事很重要，因為
    參數掃描的結論是「3N 停損通常優於 2N」，但如果部位大小不跟著調整，
    單純把停損拉寬只會讓每次虧損變大——寬停損要能用，必須搭配這個計算。

    lot_size：台股整張交易為 1000（零股為 1），美股為 1。不足一個單位的部分
    無條件捨去，所以實際風險金額會略低於預算。
    """
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0 or capital <= 0 or risk_pct <= 0 or entry_price <= 0:
        return None

    risk_budget = capital * risk_pct
    raw_shares = risk_budget / stop_distance
    shares = int(raw_shares // lot_size) * lot_size

    if shares <= 0:
        return {
            "風險預算": risk_budget,
            "每股風險": stop_distance,
            "建議股數": 0,
            "部位市值": 0.0,
            "佔用資金比例": 0.0,
            "實際風險金額": 0.0,
            "資金不足": True,
        }

    position_value = shares * entry_price
    return {
        "風險預算": risk_budget,
        "每股風險": stop_distance,
        "建議股數": shares,
        "部位市值": position_value,
        "佔用資金比例": position_value / capital,
        "實際風險金額": shares * stop_distance,
        "資金不足": position_value > capital,
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


def parse_entry_dates(series: pd.Series) -> pd.Series:
    """把「進場日期」欄位轉成 datetime，容忍同一個檔案裡混用的日期格式。

    舊版的 append_trade_record 會把讀進來的 datetime 和新寫入的 date 混在一起存，
    導致 CSV 裡同時有 "2026-07-01 00:00:00" 和 "2026-09-28" 兩種格式，
    pandas 預設會依第一列推斷格式、碰到第二種就整個噴 ValueError。
    """
    return pd.to_datetime(series, format="mixed", errors="coerce")


def load_trade_log() -> pd.DataFrame:
    """讀取交易紀錄。舊版檔案缺少的欄位會自動補上空值，不需要手動改 CSV。"""
    if not TRADE_LOG_PATH.exists():
        return pd.DataFrame(columns=TRADE_LOG_COLUMNS)
    df = pd.read_csv(TRADE_LOG_PATH)
    if "進場日期" in df.columns:
        df["進場日期"] = parse_entry_dates(df["進場日期"])
    for col in TRADE_LOG_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    return df[TRADE_LOG_COLUMNS]


def evaluate_trade_log(
    log: pd.DataFrame,
    price_data: dict[str, pd.DataFrame],
    max_hold_days: int | None = None,
) -> pd.DataFrame:
    """對交易紀錄裡的每一筆，用最新價格資料重新判斷目前狀態。

    price_data：{代碼: 價格 DataFrame}，由呼叫端負責下載（這個模組刻意不碰網路）。
    缺少價格資料的標的狀態會標成「無資料」，不會讓整張表失敗。

    「距停損」欄位是給未平倉部位看的——數字越小代表越接近被停損出場，
    可以據此排序，一眼看出哪些部位最危險。
    """
    if log.empty:
        return pd.DataFrame()

    rows = []
    for _, r in log.iterrows():
        ticker = r["代碼"]
        entry_date = pd.to_datetime(r["進場日期"]).date()
        entry_price = float(r["進場價"])
        stop_price = float(r["停損價"])
        direction = r["方向"]

        # 結構停利的紀錄沒有固定停利價，必須用當初選的訊號重新判斷；
        # 若誤用固定價位會報出使用者從未設定過的「已停利」。
        exit_style = r.get("停利方式")
        exit_style = exit_style if isinstance(exit_style, str) and exit_style else FIXED_TARGET_LABEL
        is_structural = exit_style in STRUCTURAL_EXITS
        target_price = None if is_structural else float(r["停利價"])

        df = price_data.get(ticker)
        if df is None or df.empty:
            rows.append({
                "代碼": ticker, "方向": direction, "進場日期": entry_date,
                "進場價": entry_price, "停損價": stop_price,
                "停利方式": exit_style if is_structural else f"{exit_style}（{target_price:,.2f}）",
                "現價": None, "狀態": "無資料", "報酬率": None,
                "持有天數": None, "距停損": None, "股數": r.get("股數"),
                "部位市值": None, "進場理由": r.get("進場理由"), "備註": r.get("備註"),
            })
            continue

        if is_structural:
            n_atr = r.get("N (ATR)")
            outcome = check_structural_outcome(
                df, entry_date, entry_price, stop_price, exit_style,
                float(n_atr) if pd.notna(n_atr) else 0.0,
                max_hold_days=max_hold_days,
            )
        else:
            outcome = check_bracket_outcome(
                df, entry_date, entry_price, stop_price, target_price,
                direction, max_hold_days=max_hold_days,
            )
        current_price = outcome["觸價價格"]

        # 距停損：現價還要往不利方向走多少 % 才會觸及停損。已平倉的不適用。
        distance_to_stop = None
        if outcome["狀態"] in ("持有中", "已超時") and current_price:
            if direction == "多":
                distance_to_stop = (current_price - stop_price) / current_price
            else:
                distance_to_stop = (stop_price - current_price) / current_price

        shares = r.get("股數")
        shares = float(shares) if pd.notna(shares) else None
        rows.append({
            "代碼": ticker,
            "方向": direction,
            "進場日期": entry_date,
            "進場價": entry_price,
            "停損價": stop_price,
            "停利方式": exit_style if is_structural else f"{exit_style}（{target_price:,.2f}）",
            "現價": round(current_price, 2) if current_price else None,
            "狀態": outcome["狀態"],
            "報酬率": outcome["報酬率"],
            "持有天數": outcome["持有天數"],
            "距停損": distance_to_stop,
            "股數": shares,
            "部位市值": round(shares * current_price, 2) if shares and current_price else None,
            "進場理由": r.get("進場理由"),
            "備註": r.get("備註"),
        })

    return pd.DataFrame(rows)


OPEN_STATUSES = ("持有中", "已超時")
CLOSED_STATUSES = ("已停利", "已停損")


def summarize_by_reason(evaluated: pd.DataFrame) -> pd.DataFrame:
    """依進場理由彙總已平倉交易的表現。

    這是決策日誌的重點：看見自己哪一類判斷長期是賺的、哪一類是賠的。
    樣本數少的時候不要過度解讀，所以筆數一併列出。
    """
    if evaluated.empty:
        return pd.DataFrame()

    closed = evaluated[evaluated["狀態"].isin(CLOSED_STATUSES)].copy()
    if closed.empty:
        return pd.DataFrame()

    closed["進場理由"] = closed["進場理由"].fillna("(未填)")
    grouped = closed.groupby("進場理由").agg(
        筆數=("報酬率", "size"),
        勝率=("狀態", lambda s: (s == "已停利").mean()),
        平均報酬率=("報酬率", "mean"),
        平均持有天數=("持有天數", "mean"),
    ).reset_index()
    return grouped.sort_values("筆數", ascending=False)


def append_trade_record(record: dict) -> None:
    """附加一筆交易紀錄。

    進場日期一律正規化成 YYYY-MM-DD 再寫檔。不這樣做的話，讀進來的 datetime
    會被寫成帶時間的格式、而新加的 date 不帶時間，同一個檔案就出現兩種格式，
    下次讀取時解析會失敗。
    """
    TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_row = pd.DataFrame([record], columns=TRADE_LOG_COLUMNS)
    df = load_trade_log()
    if df.empty:
        df = new_row
    else:
        # 全空的欄位（例如結構停利時的「停利價」）在 concat 時會觸發 dtype 推斷的
        # FutureWarning，兩邊都先丟掉再接，最後用 reindex 補回完整欄位。
        parts = [p.dropna(axis=1, how="all") for p in (df, new_row)]
        df = pd.concat(parts, ignore_index=True).reindex(columns=TRADE_LOG_COLUMNS)
    df["進場日期"] = parse_entry_dates(df["進場日期"]).dt.strftime("%Y-%m-%d")
    df.to_csv(TRADE_LOG_PATH, index=False)
