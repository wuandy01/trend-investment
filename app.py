"""趨勢投資回測系統 —— Streamlit 主程式。"""

from __future__ import annotations

import datetime as dt
import math

import pandas as pd
import streamlit as st

from src import (
    backtest,
    charts,
    data_loader,
    fundamentals,
    kol_feed,
    param_scan,
    screener,
    strategies,
    trade_planner,
    turtle,
)

st.set_page_config(page_title="趨勢投資回測系統", layout="wide")


def format_metric(key: str, value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A"
    if isinstance(value, float) and math.isinf(value):
        return "∞"
    if key in {
        "總報酬率",
        "年化報酬率 (CAGR)",
        "年化波動度",
        "最大回檔",
        "最大回檔 (MDD)",
        "勝率",
        "平均每筆報酬率",
        "買進持有報酬率",
    }:
        return f"{value * 100:,.2f}%"
    if key == "最終資產":
        return f"{value:,.0f}"
    if key in {"夏普比率", "獲利因子"}:
        return f"{value:,.2f}"
    if key in {"交易次數", "最高加碼單位數"}:
        return f"{int(value)}"
    return str(value)


def format_ranking_table(df):
    display = df.copy()
    display["訊號後漲幅"] = (display["訊號後漲幅"] * 100).round(2).astype(str) + "%"
    for col, fmt in [
        ("訊號前漲幅", "{:.1f}%"),
        ("乖離率", "{:+.1f}%"),
        ("距52週高", "{:+.1f}%"),
        ("半年報酬", "{:+.1f}%"),
        ("相對強弱", "前 {:.0f}%"),
    ]:
        if col not in display.columns:
            continue
        if col == "相對強弱":
            display[col] = display[col].apply(
                lambda v: fmt.format((1 - v) * 100) if pd.notna(v) else "N/A"
            )
        else:
            display[col] = display[col].apply(
                lambda v, f=fmt: f.format(v * 100) if pd.notna(v) else "N/A"
            )
    display["進場日期"] = display["進場日期"].dt.date
    display["現價"] = display["現價"].round(2)
    display["進場價格"] = display["進場價格"].round(2)
    return display


# 觸發出場訊號的標的要一眼看得出來，不然清單一長就得逐列核對「狀態」欄位。
# 顏色刻意用飽和一點的色階：太淡的底色在表格裡等於沒有標。
STATUS_STYLES = {
    "已停損": ("#ffb3ba", "🔴"),   # 紅：已觸停損，要處理
    "已停利": ("#a8e6a3", "🟢"),   # 綠：已觸停利，要處理
    "已超時": ("#ffe08a", "🟡"),   # 黃：超過持有上限，該重新評估
}


def mark_status(df: pd.DataFrame) -> pd.DataFrame:
    """在「狀態」欄位前面加上色塊 emoji。

    底色由 Styler 負責，但 Styler 在某些表格（例如開啟列選取的）會被淡化或忽略，
    emoji 則一定看得到，兩者並用才不會漏掉訊號。
    """
    if "狀態" not in df.columns:
        return df
    out = df.copy()
    out["狀態"] = out["狀態"].map(lambda s: f"{STATUS_STYLES[s][1]} {s}" if s in STATUS_STYLES else s)
    return out


def style_by_status(df: pd.DataFrame):
    """依「狀態」欄位把整列上色；沒有狀態欄位就原樣回傳。"""
    if "狀態" not in df.columns:
        return df

    def row_color(row):
        status = str(row["狀態"]).lstrip("🔴🟢🟡 ")
        color = STATUS_STYLES.get(status, ("", ""))[0]
        return [f"background-color: {color}" if color else "" for _ in row]

    return df.style.apply(row_color, axis=1)


def render_ticker_picker(market: str, popular: dict, key_prefix: str) -> str:
    """股票代碼輸入元件。

    主要是一個可以直接打任何代碼的文字框，上面搭配一個「熱門股快選」下拉選單
    （選了會自動代入文字框）。不能反過來只給下拉選單——下拉選單只能篩選內建的
    熱門股清單，打清單以外的代碼（例如冷門的 8039）會直接顯示「No results」，
    讓人誤以為系統查不到那檔股票，其實只是清單沒收錄而已。
    """
    text_key = f"{key_prefix}_ticker_text_{market}"
    quick_key = f"{key_prefix}_quick_pick_{market}"

    if text_key not in st.session_state:
        st.session_state[text_key] = next(iter(popular), "")

    def _apply_quick_pick() -> None:
        choice = st.session_state.get(quick_key)
        if choice:
            st.session_state[text_key] = choice.split()[0]

    quick_options = [f"{code}  {name}" for code, name in popular.items()]
    st.selectbox(
        "熱門股快選（選了自動代入下方欄位，也可以跳過直接輸入代碼）",
        quick_options,
        index=None,
        placeholder="點此快速選擇...",
        key=quick_key,
        on_change=_apply_quick_pick,
    )

    placeholder = "例如 2330 或 2330.TW" if market == "台股" else "例如 AAPL"
    return st.text_input("股票代碼（可直接輸入任何代碼）", key=text_key, placeholder=placeholder)


def render_fundamentals_tab(ticker: str, currency: str, latest_close: float) -> None:
    info = fundamentals.get_company_info(ticker)
    if not info:
        st.info("查無此股票的基本面資料。")
        return

    col1, col2, col3 = st.columns(3)
    col1.metric("產業", info.get("sector") or "—")
    col2.metric("市值", fundamentals.format_large_number(info.get("marketCap")))
    employees = info.get("fullTimeEmployees")
    col3.metric("員工人數", f"{employees:,}" if employees is not None else "—")

    st.markdown("**關鍵比率**")
    trailing_pe = info.get("trailingPE")
    forward_pe = info.get("forwardPE")
    price_to_book = info.get("priceToBook")
    dividend_yield = info.get("dividendYield")
    r1 = st.columns(4)
    r1[0].metric("P/E (TTM)", f"{trailing_pe:.2f}" if trailing_pe is not None else "—")
    r1[1].metric("P/E (預估)", f"{forward_pe:.2f}" if forward_pe is not None else "—")
    r1[2].metric("P/B", f"{price_to_book:.2f}" if price_to_book is not None else "—")
    r1[3].metric("股息率", f"{dividend_yield:.2f}%" if dividend_yield is not None else "—")

    roe = info.get("returnOnEquity")
    gross_margin = info.get("grossMargins")
    revenue_growth = info.get("revenueGrowth")
    debt_to_equity = info.get("debtToEquity")
    r2 = st.columns(4)
    r2[0].metric("ROE", f"{roe * 100:.1f}%" if roe is not None else "—")
    r2[1].metric("毛利率", f"{gross_margin * 100:.1f}%" if gross_margin is not None else "—")
    r2[2].metric("營收成長 (YoY)", f"{revenue_growth * 100:.1f}%" if revenue_growth is not None else "—")
    r2[3].metric("負債/權益", f"{debt_to_equity:.1f}%" if debt_to_equity is not None else "—")

    if info.get("longBusinessSummary"):
        with st.expander("公司簡介"):
            st.write(info["longBusinessSummary"])

    st.divider()
    statements = fundamentals.get_financial_statements(ticker)
    stmt_income, stmt_balance, stmt_cashflow = st.tabs(["損益表", "資產負債表", "現金流量表"])
    for tab, key, empty_msg in [
        (stmt_income, "income", "查無損益表資料。"),
        (stmt_balance, "balance", "查無資產負債表資料。"),
        (stmt_cashflow, "cashflow", "查無現金流量表資料。"),
    ]:
        with tab:
            table = fundamentals.format_statement_table(statements[key])
            if table.empty:
                st.info(empty_msg)
            else:
                st.dataframe(table, width="stretch")
    st.caption("財報單位已自動換算為萬／億／兆，年度資料，最新一期在最左側。")

    st.divider()
    rec = fundamentals.get_recommendations(ticker)
    target_mean = info.get("targetMeanPrice")
    n_analysts = info.get("numberOfAnalystOpinions")
    if not rec.empty or target_mean:
        st.markdown("**分析師評等**")
        if not rec.empty:
            latest = rec.iloc[0]
            rec_cols = st.columns(5)
            for col, key, rec_label in zip(
                rec_cols,
                ["strongBuy", "buy", "hold", "sell", "strongSell"],
                ["強力買進", "買進", "持有", "賣出", "強力賣出"],
            ):
                col.metric(rec_label, int(latest.get(key, 0)))
        if target_mean:
            diff_pct = (target_mean / latest_close - 1) * 100
            st.caption(
                f"分析師平均目標價：{currency}{target_mean:,.2f}"
                f"（{n_analysts or '—'} 位分析師），與目前股價相差 {diff_pct:+.1f}%"
            )
    else:
        st.info("查無分析師評等資料。")


def render_backtest_page() -> None:
    st.title("趨勢投資回測系統")
    st.caption("選擇股票、設定回測區間與策略參數，檢視技術分析圖、基本面與回測績效。")

    with st.sidebar:
        st.header("回測設定")

        market = st.radio("市場", ["台股", "美股"], horizontal=True)
        popular = data_loader.POPULAR_TICKERS[market]
        currency = data_loader.MARKET_CURRENCY[market]

        raw_code = render_ticker_picker(market, popular, "bt")
        ticker = data_loader.normalize_ticker(raw_code, market)

        st.divider()
        today = dt.date.today()
        default_start = today - dt.timedelta(days=365 * 3)
        start_date = st.date_input("起始日期", value=default_start, max_value=today)
        end_date = st.date_input("結束日期", value=today, max_value=today)

        st.divider()
        strategy_options = strategies.list_strategy_options()
        strategy_key = st.selectbox(
            "回測策略",
            options=[k for k, _ in strategy_options],
            format_func=lambda k: dict(strategy_options)[k],
        )
        strategy = strategies.get_strategy(strategy_key)
        st.caption(strategy.description)

        params = {}
        for p in strategy.params:
            if p.is_int:
                params[p.key] = st.slider(
                    p.label, int(p.min_value), int(p.max_value), int(p.default), step=int(p.step)
                )
            else:
                params[p.key] = st.slider(
                    p.label, float(p.min_value), float(p.max_value), float(p.default), step=float(p.step)
                )

        st.divider()
        initial_capital = st.number_input("起始資金", min_value=1_000, value=100_000, step=10_000)
        cost_pct = st.slider("每次進出場成本 (%)", 0.0, 1.0, 0.10, step=0.05)

    if not ticker:
        st.info("請在左側選擇或輸入股票代碼。")
        st.stop()

    if start_date >= end_date:
        st.error("起始日期必須早於結束日期。")
        st.stop()

    with st.spinner(f"下載 {ticker} 資料中..."):
        price_df = data_loader.load_price_data(ticker, start_date, end_date)

    if price_df.empty:
        st.error(f"查無「{ticker}」的資料，請確認股票代碼是否正確，或調整日期區間。")
        st.stop()

    if len(price_df) < 30:
        st.warning("這段區間的資料筆數過少，回測結果可能不具參考性，建議拉長日期區間。")

    company_name = data_loader.get_company_name(ticker)
    label = f"{ticker}　{company_name}" if company_name else ticker

    signal_df = strategy.compute(price_df, params)
    result = backtest.run_backtest(signal_df, initial_capital=initial_capital, cost_pct=cost_pct)

    latest_close = price_df["Close"].iloc[-1]
    st.subheader(label)
    st.caption(
        f"資料區間：{price_df.index[0].date()} ～ {price_df.index[-1].date()}"
        f"（{len(price_df)} 個交易日）　最新收盤價：{currency}{latest_close:,.2f}　策略：{strategy.name}"
    )

    m = result.metrics
    row1 = st.columns(4)
    row1[0].metric("總報酬率", format_metric("總報酬率", m["總報酬率"]))
    row1[1].metric("買進持有報酬率", format_metric("買進持有報酬率", m["買進持有報酬率"]))
    row1[2].metric("年化報酬率 (CAGR)", format_metric("年化報酬率 (CAGR)", m["年化報酬率 (CAGR)"]))
    row1[3].metric("夏普比率", format_metric("夏普比率", m["夏普比率"]))

    row2 = st.columns(4)
    row2[0].metric("最大回檔", format_metric("最大回檔", m["最大回檔"]))
    row2[1].metric("勝率", format_metric("勝率", m["勝率"]))
    row2[2].metric("交易次數", format_metric("交易次數", m["交易次數"]))
    row2[3].metric("獲利因子", format_metric("獲利因子", m["獲利因子"]))

    tab_chart, tab_equity, tab_trades, tab_ranking, tab_fundamentals = st.tabs(
        ["技術分析圖", "權益曲線", "交易明細", "熱門強勢股排行", "財務數據"]
    )

    with tab_chart:
        fig = charts.build_price_chart(result.data, strategy, result.trades, label)
        st.plotly_chart(fig, use_container_width=True)
        st.caption("紅色為上漲／買進，綠色為下跌／賣出（台股慣例）。可用滑鼠拖曳縮放、雙擊還原。")

    with tab_equity:
        fig_eq = charts.build_equity_chart(result.data)
        st.plotly_chart(fig_eq, use_container_width=True)

    with tab_trades:
        if len(result.trades) == 0:
            st.info("這段區間內策略沒有產生任何交易訊號。")
        else:
            display_trades = result.trades.copy()
            display_trades["報酬率"] = (display_trades["報酬率"] * 100).round(2).astype(str) + "%"
            display_trades["進場日期"] = display_trades["進場日期"].dt.date
            display_trades["出場日期"] = display_trades["出場日期"].dt.date
            st.dataframe(display_trades, width="stretch", hide_index=True)
            st.caption("交易明細為進出場價格的原始報酬率（未扣除成本），整體績效指標已計入每次進出場成本。")

    with tab_ranking:
        n_tw = len(data_loader.POPULAR_TICKERS["台股"])
        n_us = len(data_loader.POPULAR_TICKERS["美股"])
        st.caption(
            f"根據左側目前選擇的策略「{strategy.name}」與參數，分別掃描台股與美股（{n_us} 檔）"
            f"標的，找出目前有進場訊號的股票。"
        )
        universe_mode = st.radio(
            "台股掃描範圍",
            ["全市場依成交金額取前 N 檔（推薦）", f"內建精選 {n_tw} 檔（快）"],
            key="ranking_universe_mode",
            help=(
                "內建清單只有 30 檔、其中 11 檔是金融股與 ETF，彼此高度連動，"
                "所以掃出來永遠是那幾檔。改用證交所全市場資料依當日成交金額排序，"
                "「熱門」才名符其實。美股沒有對等的免費全市場 API，仍使用內建清單。"
            ),
        )
        universe_size = None
        if universe_mode.startswith("全市場"):
            universe_size = st.slider(
                "掃描檔數（依成交金額由高到低）", 100, 600, 400, step=100, key="ranking_universe_size"
            )
            st.caption(
                "證交所公開 API 每日更新，排除 ETF 與權證後約有 1,000 檔上市普通股。"
                "掃 400 檔約需 20 秒。"
            )

        require_bull = st.checkbox(
            "只在大盤多頭時進場（200 日均線濾網）",
            value=True,
            key="ranking_require_bull",
            help=(
                "只保留「訊號觸發當天大盤站上 200 日均線」的標的。實測台股 2022 空頭年的 986 筆訊號，"
                "不論用哪種出場方式平均 R 都趨近於零；同一套規則在多頭期是 +0.5R 以上。"
                "空頭的解法是不要進場，不是出場出得更快。"
            ),
        )
        sort_mode = st.radio(
            "排序方式",
            ["相對強弱最高（找強勢股）", "剛觸發訊號（發現機會）", "訊號後漲幅最高"],
            horizontal=True,
            key="ranking_sort_mode",
            help=(
                "「相對強弱」＝近半年報酬在整個掃描池中的百分位，這是唯一真正衡量「強勢」的指標；"
                "「剛觸發訊號」依訊號新舊排序，找的是最新機會而不是最強標的；"
                "「訊號後漲幅」是訊號觸發後到現在的漲幅，只反映這一段訊號走得好不好。"
            ),
        )

        use_rs_filter = st.checkbox(
            "只看相對強弱排前段的股票",
            value=True,
            key="ranking_use_rs_filter",
            help=(
                "原本的排行榜完全沒有衡量「強勢」，只看訊號新舊，所以掃出來常是剛好穿越均線的牛皮股。"
                "實測截圖那 9 檔裡有 6 檔的相對強弱在全市場後半段（大眾控 6.5%、晟銘電 9.5%、廣達 18%）。"
            ),
        )
        min_rs = None
        if use_rs_filter:
            min_rs = st.slider(
                "相對強弱門檻（取前 X%）", 10, 90, 30, step=10, key="ranking_min_rs",
                help="設 30 代表只保留近半年報酬排在掃描池前 30% 的股票。",
            )
            min_rs = 1 - min_rs / 100
        use_high_filter = st.checkbox(
            "只看接近 52 週高點的股票",
            value=True,
            key="ranking_use_high_filter",
            help=(
                "實測中最強的單一篩選指標。在半年報酬前 30% 的母體裡，套用實際交易規則後，"
                "距高點 3% 以內平均 +1.491R、低於高點 10~25% 只有 +0.569R、低於 25% 以上只有 +0.388R。"
                "「還有續漲空間」的實際樣貌是正在創新高，不是回檔整理。"
            ),
        )
        max_below_high = None
        if use_high_filter:
            # 用整數百分比當滑桿單位，顯示才會是「10」而不是「0.10」；傳進濾網前再換回小數。
            max_below_high = st.slider(
                "距 52 週高點最多低於（%）", 2, 30, 10, step=1, key="ranking_max_below_high"
            ) / 100
            st.caption(
                "設 10% 代表只保留「現價在 52 週高點 10% 以內」的股票。設 3% 會只剩正在創新高的，"
                "數量少但實測表現最好。"
            )

        use_extension_filter = st.checkbox(
            "排除短線追高（乖離率濾網，不建議開啟）",
            value=False,
            key="ranking_use_extension_filter",
            help=(
                f"乖離率＝現價距離 {screener.EXTENSION_MA} 日均線多遠。"
                "⚠️ 實測顯示這道濾網幫倒忙：越延伸的未來表現越好（高於 20MA 20% 以上 +1.087R、"
                "高於 0~8% 只有 +0.907R、低於 20MA 更只有 +0.427R）。動能是「買高、賣更高」，"
                "刻意挑回檔等於挑動能正在衰退的那一群。保留這個選項只是為了讓你自己比較。"
            ),
        )
        max_extension = None
        if use_extension_filter:
            max_extension = st.slider(
                f"乖離率上限（%，距 {screener.EXTENSION_MA} 日均線）",
                2, 30, 8, step=1, key="ranking_max_extension",
            ) / 100
        max_runup = None

        use_age_filter = st.checkbox(
            "只看最近觸發的新訊號（訊號年齡濾網）",
            value=True,
            key="ranking_use_age_filter",
            help=(
                "這是決定排行榜「能不能現在進場」的關鍵開關。關掉的話，候選不足時會拿幾個月前的"
                "舊訊號補滿名單——那些訊號的漲幅早就跑完了（訊號後漲幅動輒 40-60%），"
                "看到也來不及進場。\n\n"
                "上面三道濾網（大盤 200MA、相對強弱、距 52 週高點）看的是長期趨勢；"
                "這一道看的是「這個進場點是不是現在才出現」。兩者要一起用。"
            ),
        )
        max_age_days = st.slider(
            "訊號年齡上限（天）", 5, 120, 20, step=5, key="ranking_max_age"
        ) if use_age_filter else None
        if use_age_filter:
            st.caption(
                "設 20 天時，全市場 400 檔實測可用標的：均線交叉 11 檔、MACD 29 檔、ATR 通道突破 11 檔；"
                "RSI 與布林通道會是 0 檔（均值回歸型訊號幾乎不會出現在 52 週高點附近）。"
                "列表太短就放寬天數、擴大掃描檔數，或改用觸發較頻繁的 MACD。"
            )
        scan_clicked = st.button("掃描熱門強勢股排行", key="scan_ranking")

        if scan_clicked:
            params_items = tuple(sorted(params.items()))
            tw_universe, fell_back = screener.resolve_universe("台股", universe_size)
            if fell_back:
                st.warning("證交所 API 抓取失敗，台股這次改用內建的 30 檔精選清單。")
            with st.spinner(f"掃描中，台股 {len(tw_universe)} 檔 + 美股 {n_us} 檔，請稍候..."):
                st.session_state["ranking_result"] = {
                    "strategy_name": strategy.name,
                    "台股掃描檔數": len(tw_universe),
                    "台股": screener.scan_market(
                        "台股", strategy_key, params_items, start_date, end_date, universe_size=universe_size
                    ),
                    "美股": screener.scan_market("美股", strategy_key, params_items, start_date, end_date),
                }

        ranking_state = st.session_state.get("ranking_result")
        if ranking_state is None:
            st.info("點擊上方按鈕開始掃描（首次掃描需下載較多資料，可能需要數秒到數十秒）。")
        else:
            if sort_mode.startswith("相對強弱"):
                sort_key = "rs"
            elif sort_mode.startswith("剛觸發"):
                sort_key = "fresh"
            else:
                sort_key = "strength"
            st.caption(
                f"掃描時使用的策略：{ranking_state['strategy_name']}"
                f"（台股掃了 {ranking_state.get('台股掃描檔數', n_tw)} 檔）"
            )
            for rank_market in ["台股", "美股"]:
                st.markdown(f"**{rank_market}**")
                ranking_df, stats = screener.rank_signals(
                    ranking_state[rank_market],
                    sort_by=sort_key,
                    max_runup=max_runup,
                    max_age_days=max_age_days,
                    min_rs=min_rs,
                    max_extension=max_extension,
                    max_below_high=max_below_high,
                    require_bull_regime=require_bull,
                )
                filter_notes = []
                if stats["大盤空頭濾掉"]:
                    filter_notes.append(f"大盤空頭期擋掉 {stats['大盤空頭濾掉']} 檔")
                if stats["不夠強濾掉"]:
                    filter_notes.append(f"相對強弱不足擋掉 {stats['不夠強濾掉']} 檔")
                if stats["離高點太遠濾掉"]:
                    filter_notes.append(f"距高點超過 {max_below_high:.0%} 擋掉 {stats['離高點太遠濾掉']} 檔")
                if stats["追高濾掉"]:
                    filter_notes.append(f"乖離超過 {max_extension:.0%} 擋掉 {stats['追高濾掉']} 檔")
                if stats["太舊濾掉"]:
                    filter_notes.append(f"訊號超過 {max_age_days} 天擋掉 {stats['太舊濾掉']} 檔")
                if filter_notes:
                    st.caption(f"候選 {stats['候選']} 檔｜" + "、".join(filter_notes))
                if ranking_df.empty:
                    if stats["候選"]:
                        st.info(
                            f"{rank_market}有 {stats['候選']} 檔在訊號中，但全被濾網擋掉了。"
                            "這通常代表「最近真的沒有新的進場機會」——可以放寬訊號年齡上限、"
                            "擴大掃描檔數，或改用觸發頻率較高的策略（例如 MACD）。"
                        )
                    else:
                        st.info(f"目前沒有{rank_market}標的符合此策略的進場條件。")
                else:
                    display_df = format_ranking_table(ranking_df)
                    display_df.insert(0, "加入強勢股測試", False)
                    edited_df = st.data_editor(
                        display_df,
                        column_config={
                            "加入強勢股測試": st.column_config.CheckboxColumn(
                                "加入強勢股測試", help="勾選後按下方按鈕，送到「強勢股測試」頁面批次跑停損停利模擬"
                            )
                        },
                        disabled=[c for c in display_df.columns if c != "加入強勢股測試"],
                        hide_index=True,
                        width="stretch",
                        key=f"ranking_editor_{rank_market}_{sort_key}",
                    )
                    if st.button(f"將勾選的{rank_market}標的加入「強勢股測試」", key=f"add_watchlist_{rank_market}_{sort_key}"):
                        selected_idx = edited_df.index[edited_df["加入強勢股測試"]]
                        if len(selected_idx) == 0:
                            st.warning("請先在表格中勾選至少一檔標的。")
                        else:
                            watchlist = st.session_state.setdefault("strength_test_watchlist", {})
                            for idx in selected_idx:
                                raw_row = ranking_df.loc[idx]
                                entry_date = raw_row["進場日期"]
                                entry_date = entry_date.date() if hasattr(entry_date, "date") else entry_date
                                watchlist[f"{rank_market}:{raw_row['代碼']}"] = {
                                    "市場": rank_market,
                                    "代碼": raw_row["代碼"],
                                    "名稱": raw_row["名稱"],
                                    "進場日期": entry_date,
                                    "進場價格": float(raw_row["進場價格"]),
                                    "策略": ranking_state["strategy_name"],
                                    "訊號後漲幅": float(raw_row["訊號後漲幅"]),
                                }
                            st.success(f"已加入 {len(selected_idx)} 檔到「強勢股測試」清單，可切換到左側「強勢股測試」頁面查看。")
            if sort_key == "fresh":
                st.caption(
                    "依「持有天數」由小到大排序，優先顯示剛觸發、還沒漲多的訊號，方便及早發現機會；"
                    "「訊號後漲幅」欄位僅供參考，不代表未來績效。"
                )
            else:
                st.caption(
                    "依「訊號後漲幅」由高到低排序，代表目前動能最強、訊號最確立，"
                    "但也代表可能已經漲多、追高風險較高；不代表未來績效。"
                )

    with tab_fundamentals:
        render_fundamentals_tab(ticker, currency, latest_close)


def render_turtle_page() -> None:
    st.title("海龜交易法則 (Turtle Trading Rules)")
    st.caption(
        "輸入股票代碼與回測起始日期，依海龜法則自動找出多空進出場點與加碼點，並計算回測績效。"
    )

    with st.sidebar:
        st.header("海龜設定")

        market = st.radio("市場", ["台股", "美股"], horizontal=True, key="turtle_market")
        popular = data_loader.POPULAR_TICKERS[market]
        currency = data_loader.MARKET_CURRENCY[market]

        raw_code = render_ticker_picker(market, popular, "turtle")
        ticker = data_loader.normalize_ticker(raw_code, market)

        st.divider()
        today = dt.date.today()
        default_start = today - dt.timedelta(days=365 * 3)
        start_date = st.date_input("買入日期（回測起始日）", value=default_start, max_value=today, key="turtle_start")
        end_date = st.date_input("結束日期", value=today, max_value=today, key="turtle_end")

        st.divider()
        system_key = st.radio(
            "系統",
            options=list(turtle.SYSTEMS.keys()),
            format_func=lambda k: turtle.SYSTEMS[k]["label"],
            key="turtle_system",
        )
        atr_period = st.slider("ATR 天數 (N)", 5, 50, 20, step=1, key="turtle_atr_period")
        pyramid_step = st.slider("加碼門檻（N 的倍數）", 0.1, 1.0, 0.5, step=0.1, key="turtle_pyramid_step")
        max_units = st.slider("最大單位數", 1, 6, 4, step=1, key="turtle_max_units")

        st.divider()
        use_trend_filter = st.checkbox(
            "使用長期趨勢濾網（5/20/60/200 MA）",
            value=True,
            key="turtle_use_trend_filter",
            help="開啟後，只有順著長期趨勢的方向才允許開新倉（例如多頭排列時不放空），"
            "已持有的部位不受影響，仍照原本規則出場。",
        )
        trend_filter_mode = "simple"
        if use_trend_filter:
            trend_filter_mode = st.radio(
                "濾網嚴格程度",
                options=["simple", "strict"],
                format_func=lambda k: "簡易（收盤價 vs 200MA）" if k == "simple" else "嚴格（5/20/60/200MA 全排列）",
                key="turtle_trend_filter_mode",
            )

        st.divider()
        initial_capital = st.number_input(
            "起始資金", min_value=1_000, value=100_000, step=10_000, key="turtle_capital"
        )
        cost_pct = st.slider("每次進出場成本 (%)", 0.0, 1.0, 0.10, step=0.05, key="turtle_cost")

    if not ticker:
        st.info("請在左側選擇或輸入股票代碼。")
        st.stop()
    if start_date >= end_date:
        st.error("起始日期必須早於結束日期。")
        st.stop()

    with st.spinner(f"下載 {ticker} 資料中..."):
        price_df = data_loader.load_price_data(ticker, start_date, end_date)

    if price_df.empty:
        st.error(f"查無「{ticker}」的資料，請確認股票代碼是否正確，或調整日期區間。")
        st.stop()

    min_required = max(turtle.SYSTEMS[system_key]["entry_days"], atr_period) + 10
    if use_trend_filter:
        min_required = max(min_required, 200 + 10)
    if len(price_df) < min_required:
        st.warning(
            f"這段區間只有 {len(price_df)} 個交易日，少於系統計算所需的 {min_required} 天"
            + ("（含 200 日趨勢濾網）" if use_trend_filter else "")
            + "，回測結果可能不具參考性，建議拉長日期區間。"
        )

    company_name = data_loader.get_company_name(ticker)
    label = f"{ticker}　{company_name}" if company_name else ticker

    result = turtle.run_turtle_backtest(
        price_df,
        system_key=system_key,
        atr_period=atr_period,
        max_units=max_units,
        pyramid_step=pyramid_step,
        initial_capital=initial_capital,
        cost_pct=cost_pct,
        use_trend_filter=use_trend_filter,
        trend_filter_mode=trend_filter_mode,
    )

    latest_close = price_df["Close"].iloc[-1]
    filter_desc = (
        f"趨勢濾網：{'簡易 (收盤 vs 200MA)' if trend_filter_mode == 'simple' else '嚴格 (5/20/60/200MA 全排列)'}"
        if use_trend_filter
        else "趨勢濾網：未啟用"
    )
    st.subheader(label)
    st.caption(
        f"資料區間：{price_df.index[0].date()} ～ {price_df.index[-1].date()}"
        f"（{len(price_df)} 個交易日）　最新收盤價：{currency}{latest_close:,.2f}　"
        f"系統：{turtle.SYSTEMS[system_key]['label']}　{filter_desc}"
    )

    m = result.metrics
    row1 = st.columns(4)
    row1[0].metric("總報酬率", format_metric("總報酬率", m["總報酬率"]))
    row1[1].metric("買進持有報酬率", format_metric("買進持有報酬率", m["買進持有報酬率"]))
    row1[2].metric("年化報酬率 (CAGR)", format_metric("年化報酬率 (CAGR)", m["年化報酬率 (CAGR)"]))
    row1[3].metric("夏普比率", format_metric("夏普比率", m["夏普比率"]))

    row2 = st.columns(4)
    row2[0].metric("最大回檔 (MDD)", format_metric("最大回檔 (MDD)", m["最大回檔 (MDD)"]))
    row2[1].metric("勝率", format_metric("勝率", m["勝率"]))
    row2[2].metric("交易次數", format_metric("交易次數", m["交易次數"]))
    row2[3].metric("最高加碼單位數", format_metric("最高加碼單位數", m["最高加碼單位數"]))

    tab_chart, tab_equity, tab_trades = st.tabs(["進出場圖", "權益曲線", "交易明細"])

    with tab_chart:
        events = turtle.extract_unit_events(result.data)
        fig = charts.build_turtle_chart(result.data, events, label)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "紅色＝多方進場/加碼、藍色＝空方進場/加碼、× 記號＝出場（不分方向）。"
            "以收盤價判斷突破，不含當日盤中觸價。灰階線為 5/20/60/200 日均線（趨勢濾網依此判斷）。"
            "最下方 N 為 ATR，決定加碼間距。"
        )

    with tab_equity:
        fig_eq = charts.build_equity_chart(result.data)
        st.plotly_chart(fig_eq, use_container_width=True)

    with tab_trades:
        if len(result.trades) == 0:
            st.info("這段區間內策略沒有產生任何交易訊號。")
        else:
            display_trades = result.trades.copy()
            display_trades["報酬率"] = (display_trades["報酬率"] * 100).round(2).astype(str) + "%"
            display_trades["首次進場日期"] = display_trades["首次進場日期"].dt.date
            display_trades["出場日期"] = display_trades["出場日期"].dt.date
            st.dataframe(display_trades, width="stretch", hide_index=True)
            st.caption(
                "「平均進場價」為該筆交易所有加碼單位的平均成本（每單位視為等權重，即 1/最大單位數）。"
                "報酬率已依多空方向調整，未扣除交易成本；整體績效指標已計入每次進出場成本。"
                "本頁未實作原始海龜法則的 2N 停損，僅依規格中的進出場規則計算。"
            )


def render_trade_planner_page() -> None:
    st.title("停損停利規劃")
    st.caption(
        "輸入你的買入日期與方向，依 ATR 算出建議的停損／停利價位，並可儲存這筆交易紀錄。"
        "跟海龜法則不同，這裡是固定的兩條價位線，先碰到哪條就出場。"
    )

    with st.sidebar:
        st.header("進場設定")

        market = st.radio("市場", ["台股", "美股"], horizontal=True, key="planner_market")
        popular = data_loader.POPULAR_TICKERS[market]
        currency = data_loader.MARKET_CURRENCY[market]

        raw_code = render_ticker_picker(market, popular, "planner")
        ticker = data_loader.normalize_ticker(raw_code, market)

        st.divider()
        direction = st.radio("方向", ["多", "空"], horizontal=True, key="planner_direction")
        today = dt.date.today()
        entry_date = st.date_input(
            "買入日期", value=today, max_value=today, key="planner_entry_date"
        )

        st.divider()
        atr_period = st.slider("ATR 天數 (N)", 5, 50, 20, step=1, key="planner_atr_period")
        stop_mult = st.slider("停損倍數（×N）", 0.5, 5.0, 2.0, step=0.5, key="planner_stop_mult")
        exit_style = st.radio(
            "停利方式",
            ["結構停利（推薦）", "固定 N 倍數"],
            key="planner_exit_style",
            help=(
                "固定倍數停利不管股票當下的走勢結構，到價就出；結構停利等技術訊號轉弱才走，"
                "讓走得動的部位繼續跑。實測台股全市場 5,312 筆訊號，結構停利的平均 R 高於固定倍數。"
            ),
        )
        target_mult = 5.0
        structural_exit = None
        min_profit_n = 1.0
        if exit_style.startswith("結構"):
            structural_exit = st.selectbox(
                "結構停利訊號",
                list(trade_planner.STRUCTURAL_EXITS.keys()),
                key="planner_structural_exit",
            )
            st.caption(trade_planner.STRUCTURAL_EXITS[structural_exit]["說明"])
            min_profit_n = st.slider(
                "獲利達幾個 N 才啟動停利", 0.0, 6.0, 3.0, step=0.5, key="planner_min_profit_n",
                help=(
                    "進場後的築底震盪很容易立刻觸發結構訊號，門檻太低會在主升段開始前就出場。"
                    "實測 5,314 筆：門檻 1N 平均 +0.861R、3N 平均 +1.012R，但勝率從 33.4% 降到 28.2%。"
                    "再往上調 R 還會繼續升（不設停利可到 +2.6R），但勝率會掉到 21%，實務上很難執行。"
                ),
            )
        else:
            target_mult = st.slider(
                "停利倍數（×N）", 0.5, 10.0, 5.0, step=0.5, key="planner_target_mult"
            )

        st.divider()
        use_hold_limit = st.checkbox("啟用持有天數上限提醒", value=True, key="planner_use_hold_limit")
        max_hold_days = None
        if use_hold_limit:
            max_hold_days = st.slider(
                "持有天數上限（天，僅提醒不會自動出場）", 30, 420, 365, step=15, key="planner_max_hold_days",
                help=(
                    "預設 365 天，刻意設得很長：結構停利已經負責「這段走完了」，96% 的部位會自然出場，"
                    "時間上限只會綁到最強的那幾檔。實測把上限從 3 個月放寬到 12 個月，"
                    "平均 R 從 +0.525 提升到 +0.860，而平均持有只從 28 天變成 33 天。"
                ),
            )

        st.divider()
        st.header("部位大小")
        st.caption("由停損距離反推該買多少，讓每筆交易的風險金額固定。")
        capital = st.number_input(
            f"總資金（{currency}）", min_value=0.0, value=1_000_000.0,
            step=10_000.0, key="planner_capital",
        )
        risk_pct = st.slider(
            "單筆風險（佔總資金 %）", 0.25, 5.0, 1.0, step=0.25, key="planner_risk_pct",
        ) / 100
        if market == "台股":
            lot_mode = st.radio(
                "交易單位", ["整張（1000 股）", "零股（1 股）"], key="planner_lot_mode",
            )
            lot_size = 1000 if lot_mode.startswith("整張") else 1
        else:
            lot_size = 1

    if not ticker:
        st.info("請在左側選擇或輸入股票代碼。")
        st.stop()

    fetch_start = entry_date - dt.timedelta(days=atr_period * 4 + 30)
    with st.spinner(f"下載 {ticker} 資料中..."):
        price_df = data_loader.load_price_data(ticker, fetch_start, today)

    if price_df.empty:
        st.error(f"查無「{ticker}」的資料，請確認股票代碼是否正確。")
        st.stop()

    before_entry = price_df[price_df.index.date <= entry_date]
    if before_entry.empty:
        st.error("買入日期早於這檔股票最早的資料，請調整日期。")
        st.stop()

    fetched_price = float(before_entry["Close"].iloc[-1])
    n_atr = trade_planner.get_atr_at_date(price_df, entry_date, atr_period)
    if n_atr is None:
        st.warning("這個日期之前的資料不足以算出 ATR，請選擇較晚的買入日期，或拉長資料區間。")
        st.stop()

    # key 帶入 ticker/entry_date：換股票或換日期時視為全新欄位，才會重新套用剛抓到的收盤價，
    # 不然 Streamlit 會沿用使用者上次手動打過的舊數字。
    entry_price = st.number_input(
        f"進場價（預設為 {entry_date} 收盤價 {currency}{fetched_price:,.2f}，可手動修改為實際成交價）",
        min_value=0.01,
        value=fetched_price,
        step=0.01,
        key=f"planner_entry_price_{ticker}_{entry_date}",
    )

    bracket = trade_planner.compute_bracket(entry_price, n_atr, direction, stop_mult, target_mult)
    stop_price = bracket["停損價"]
    target_price = bracket["停利價"]

    row1 = st.columns(4)
    row1[0].metric("進場價", f"{currency}{entry_price:,.2f}")
    row1[1].metric("N (ATR)", f"{n_atr:,.2f}")
    row1[2].metric("停損價", f"{currency}{stop_price:,.2f}")
    if structural_exit:
        row1[3].metric("停利方式", structural_exit)
        st.caption(
            f"停損距離 {stop_mult:.1f}N＝{currency}{entry_price - stop_price:,.2f}；"
            f"停利沒有固定價位，等「{structural_exit}」出現才出場"
            f"（獲利需先達 {min_profit_n:.1f}N 才啟動）。"
        )
    else:
        row1[3].metric("停利價", f"{currency}{target_price:,.2f}")
        st.caption(f"風報比（停利距離 ÷ 停損距離）＝ {bracket['風報比']:.2f}")

    st.markdown("##### 建議部位大小")
    sizing = trade_planner.compute_position_size(
        capital, risk_pct, entry_price, stop_price, lot_size=lot_size
    )
    if sizing is None:
        st.warning("停損距離為 0 或資金設定無效，無法計算部位大小。")
        shares = 0
    else:
        shares = sizing["建議股數"]
        size_cols = st.columns(4)
        size_cols[0].metric("建議股數", f"{shares:,.0f} 股")
        size_cols[1].metric("部位市值", f"{currency}{sizing['部位市值']:,.0f}")
        size_cols[2].metric("佔用資金", f"{sizing['佔用資金比例']:.1%}")
        size_cols[3].metric("實際風險金額", f"{currency}{sizing['實際風險金額']:,.0f}")
        st.caption(
            f"風險預算 {currency}{sizing['風險預算']:,.0f}（總資金 × {risk_pct:.2%}）"
            f" ÷ 每股風險 {currency}{sizing['每股風險']:,.2f}（進場價 − 停損價）"
            f" = {shares:,.0f} 股。停損拉得越寬，部位自動越小，每筆交易的風險金額因此固定。"
        )
        if shares == 0:
            st.warning(
                "依目前的資金與風險設定，算出的部位不足一個交易單位。"
                "可以改用零股、提高風險比例，或選擇股價較低的標的。"
            )
        elif sizing["資金不足"]:
            st.warning(
                f"⚠️ 這個部位市值 {currency}{sizing['部位市值']:,.0f} 已超過總資金，"
                "代表需要槓桿才吃得下。建議縮小單筆風險比例，或把停損設得更靠近進場價。"
            )

    if structural_exit:
        outcome = trade_planner.check_structural_outcome(
            price_df, entry_date, entry_price, stop_price, structural_exit, n_atr,
            min_profit_n=min_profit_n, max_hold_days=max_hold_days,
        )
    else:
        outcome = trade_planner.check_bracket_outcome(
            price_df, entry_date, entry_price, stop_price, target_price, direction,
            max_hold_days=max_hold_days,
        )

    status = outcome["狀態"]
    if status == "已停利":
        st.success(
            f"已於 {outcome['觸價日期'].date()} 觸及停利價，報酬率 {outcome['報酬率']:+.2%}"
            f"（持有 {outcome['持有天數']} 天）"
        )
    elif status == "已停損":
        st.error(
            f"已於 {outcome['觸價日期'].date()} 觸及停損價，報酬率 {outcome['報酬率']:+.2%}"
            f"（持有 {outcome['持有天數']} 天）"
        )
    elif status == "已超時":
        st.warning(
            f"⚠️ 已持有 {outcome['持有天數']} 天仍未觸價（超過設定的 {max_hold_days} 天上限），"
            f"目前未實現報酬率 {outcome['報酬率']:+.2%}，建議重新評估這筆交易的理由是否還成立。"
        )
    elif status == "持有中":
        st.info(f"尚未觸價，持有中 {outcome['持有天數']} 天，目前未實現報酬率 {outcome['報酬率']:+.2%}")
    else:
        st.warning("查無足夠資料判斷目前狀態。")

    holding_end = outcome["觸價日期"].date() if outcome["觸價日期"] is not None else None
    summary = trade_planner.summarize_holding_period(
        price_df, entry_date, entry_price, direction, as_of_date=holding_end
    )
    if summary:
        st.markdown("##### 持有期間價量表現")
        row2 = st.columns(4)
        row2[0].metric("交易天數", f"{summary['交易天數']} 天")
        row2[1].metric("最大有利變動 (MFE)", f"{summary['最大有利變動']:+.2%}")
        row2[2].metric("最大不利變動 (MAE)", f"{summary['最大不利變動']:+.2%}")
        vol_change = summary["量能變化"]
        row2[3].metric(
            "持有期均量 vs 進場前20日均量",
            f"{vol_change:+.1%}" if vol_change == vol_change else "N/A",
        )
        st.caption(
            f"期間最高 {currency}{summary['期間最高價']:,.2f}、最低 {currency}{summary['期間最低價']:,.2f}。"
            "MFE／MAE 已依方向調整正負號：正值＝對部位有利、負值＝帳面虧損，看這筆交易帳面最深虧過、最高賺過多少。"
        )
        with st.expander("查看持有期間逐日價量"):
            st.dataframe(summary["逐日資料"], width="stretch")

    fig = charts.build_bracket_chart(
        price_df[price_df.index.date >= fetch_start],
        entry_date,
        entry_price,
        stop_price,
        target_price,
        outcome,
        f"{ticker}",
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("橘色菱形＝進場點；紅色虛線＝停損、綠色虛線＝停利；× 記號＝實際觸價點（若已觸價）。")

    st.divider()
    st.subheader("儲存交易紀錄")
    st.caption(
        "進場理由會在「部位管理」→「決策回顧」裡跟實際結果並排比對，"
        "用來看出自己哪一類判斷長期是賺的、哪一類是賠的。這是整個工具裡最值得長期累積的資料。"
    )
    # key 帶入建議股數：建議值一變（換股票／改停損／改風險比例）就視為全新欄位重新套用，
    # 否則 Streamlit 會沿用使用者上次手動打過的舊股數。
    actual_shares = st.number_input(
        f"實際股數（預設為建議的 {shares:,.0f} 股，可改成你實際成交的數量）",
        min_value=0,
        value=int(shares),
        step=int(lot_size),
        key=f"planner_shares_{ticker}_{entry_date}_{int(shares)}",
    )
    if actual_shares > 0:
        actual_risk = actual_shares * abs(entry_price - stop_price)
        actual_value = actual_shares * entry_price
        risk_cols = st.columns(3)
        risk_cols[0].metric("實際部位市值", f"{currency}{actual_value:,.0f}")
        risk_cols[1].metric("實際風險金額", f"{currency}{actual_risk:,.0f}")
        risk_cols[2].metric("佔總資金", f"{actual_value / capital:.1%}" if capital > 0 else "N/A")
        if capital > 0 and actual_risk / capital > risk_pct * 1.5:
            st.warning(
                f"⚠️ 這個股數的風險金額是總資金的 {actual_risk / capital:.2%}，"
                f"明顯超過你設定的單筆風險上限 {risk_pct:.2%}。"
            )

    reason_col, note_col = st.columns([1, 2])
    entry_reason = reason_col.selectbox(
        "進場理由", trade_planner.ENTRY_REASONS, key="planner_reason"
    )
    note = note_col.text_input(
        "備註（選填，建議寫下當時看到什麼）", key="planner_note",
        placeholder="例如：帶量突破前高，外資連三日買超",
    )
    if st.button("儲存這筆交易紀錄"):
        record = {
            "記錄時間": dt.datetime.now().isoformat(timespec="seconds"),
            "代碼": ticker,
            "方向": direction,
            "進場日期": entry_date,
            "進場價": round(entry_price, 2),
            "N (ATR)": round(n_atr, 2),
            "停損倍數": stop_mult,
            "停利倍數": target_mult,
            "停損價": round(stop_price, 2),
            # 結構停利沒有固定價位，存 None；否則部位管理會拿一個使用者沒設過的價位去判斷。
            "停利價": None if structural_exit else round(target_price, 2),
            "停利方式": structural_exit or trade_planner.FIXED_TARGET_LABEL,
            "股數": int(actual_shares),
            "進場理由": entry_reason,
            "備註": note,
        }
        trade_planner.append_trade_record(record)
        st.success("已儲存到本機交易紀錄。")

    st.divider()
    st.subheader("已儲存的交易紀錄")
    log = trade_planner.load_trade_log()
    if log.empty:
        st.info("尚無儲存的紀錄。")
    else:
        display_log = log.copy()
        display_log["進場日期"] = trade_planner.parse_entry_dates(display_log["進場日期"]).dt.date
        display_log["備註"] = display_log["備註"].fillna("")
        st.dataframe(display_log.sort_values("記錄時間", ascending=False), width="stretch", hide_index=True)
    st.caption(
        "交易紀錄儲存在本機的 data/trade_log.csv，只在你自己電腦上執行時可靠保存；"
        "若部署到 Streamlit Cloud 等雲端平台，檔案系統通常是暫時性的，重新部署或休眠喚醒後可能會消失，"
        "不建議在雲端版本依賴這個檔案做長期紀錄。"
    )


def render_strength_test_page() -> None:
    st.title("強勢股測試")
    st.caption(
        "「清單測試」把「股票回測」→「熱門強勢股排行」勾選加入的標的，套用固定 ATR 停損停利規則批次測試；"
        "「參數掃描」對整個股票池的所有歷史訊號，找出歷史上表現最好的停損停利倍數組合，取代憑感覺調參數。"
    )

    with st.sidebar:
        st.header("清單測試：停損停利規則")
        st.caption("套用到「清單測試」分頁的標的，規則跟「停損停利規劃」頁面相同。")
        atr_period = st.slider("ATR 天數 (N)", 5, 50, 20, step=1, key="strength_atr_period")
        stop_mult = st.slider("停損倍數（×N）", 0.5, 5.0, 2.0, step=0.5, key="strength_stop_mult")
        exit_style = st.radio(
            "停利方式",
            ["結構停利（推薦）", "固定 N 倍數"],
            key="strength_exit_style",
            help="跟「停損停利規劃」頁面相同。固定倍數到價就出，結構停利等技術訊號轉弱才走。",
        )
        target_mult = 5.0
        structural_exit = None
        min_profit_n = 1.0
        if exit_style.startswith("結構"):
            structural_exit = st.selectbox(
                "結構停利訊號",
                list(trade_planner.STRUCTURAL_EXITS.keys()),
                key="strength_structural_exit",
            )
            st.caption(trade_planner.STRUCTURAL_EXITS[structural_exit]["說明"])
            min_profit_n = st.slider(
                "獲利達幾個 N 才啟動停利", 0.0, 6.0, 3.0, step=0.5, key="strength_min_profit_n"
            )
        else:
            target_mult = st.slider(
                "停利倍數（×N）", 0.5, 10.0, 5.0, step=0.5, key="strength_target_mult"
            )
        st.divider()
        use_hold_limit = st.checkbox("啟用持有天數上限提醒", value=True, key="strength_use_hold_limit")
        max_hold_days = None
        if use_hold_limit:
            max_hold_days = st.slider(
                "持有天數上限（天，僅提醒不會自動出場）", 30, 420, 365, step=15, key="strength_max_hold_days"
            )

    tab_watchlist, tab_scan = st.tabs(["清單測試", "參數掃描"])

    with tab_watchlist:
        _render_watchlist_test_tab(
            atr_period, stop_mult, target_mult, max_hold_days, structural_exit, min_profit_n
        )

    with tab_scan:
        _render_param_scan_tab()


def _render_watchlist_test_tab(
    atr_period: int,
    stop_mult: float,
    target_mult: float,
    max_hold_days: int | None,
    structural_exit: str | None = None,
    min_profit_n: float = 1.0,
) -> None:
    watchlist = st.session_state.get("strength_test_watchlist", {})

    if not watchlist:
        st.info(
            "清單目前是空的。到左側「股票回測」的「熱門強勢股排行」分頁，掃描後在表格中勾選標的，"
            "按「加入強勢股測試」就會出現在這裡。"
        )
        return

    st.markdown(f"##### 目前清單（{len(watchlist)} 檔）")
    watchlist_df = pd.DataFrame(watchlist.values())
    display_watchlist = watchlist_df.copy()
    display_watchlist.insert(0, "移除", False)
    edited_watchlist = st.data_editor(
        display_watchlist,
        column_config={"移除": st.column_config.CheckboxColumn("移除")},
        disabled=[c for c in display_watchlist.columns if c != "移除"],
        hide_index=True,
        width="stretch",
        key="strength_watchlist_editor",
    )

    col_remove, col_clear = st.columns(2)
    if col_remove.button("移除勾選項目"):
        to_remove = edited_watchlist[edited_watchlist["移除"]]
        for _, row in to_remove.iterrows():
            st.session_state["strength_test_watchlist"].pop(f"{row['市場']}:{row['代碼']}", None)
        st.rerun()
    if col_clear.button("清空整個清單"):
        st.session_state["strength_test_watchlist"] = {}
        st.session_state.pop("strength_test_results", None)
        st.rerun()

    st.divider()
    if st.button("執行停損停利測試", type="primary"):
        today = dt.date.today()
        results = []
        skipped = []
        with st.spinner(f"測試中，需下載 {len(watchlist)} 檔股票資料，請稍候..."):
            for item in watchlist.values():
                ticker = item["代碼"]
                entry_date = item["進場日期"]
                entry_price = item["進場價格"]
                fetch_start = entry_date - dt.timedelta(days=atr_period * 4 + 30)
                price_df = data_loader.load_price_data(ticker, fetch_start, today)
                if price_df.empty:
                    skipped.append(f"{ticker}（查無資料）")
                    continue
                n_atr = trade_planner.get_atr_at_date(price_df, entry_date, atr_period)
                if n_atr is None:
                    skipped.append(f"{ticker}（資料不足以算 ATR）")
                    continue
                bracket = trade_planner.compute_bracket(entry_price, n_atr, "多", stop_mult, target_mult)
                if structural_exit:
                    outcome = trade_planner.check_structural_outcome(
                        price_df, entry_date, entry_price, bracket["停損價"], structural_exit,
                        n_atr, min_profit_n=min_profit_n, max_hold_days=max_hold_days,
                    )
                else:
                    outcome = trade_planner.check_bracket_outcome(
                        price_df,
                        entry_date,
                        entry_price,
                        bracket["停損價"],
                        bracket["停利價"],
                        "多",
                        max_hold_days=max_hold_days,
                    )
                latest_price = float(price_df["Close"].iloc[-1])
                results.append(
                    {
                        "市場": item["市場"],
                        "代碼": ticker,
                        "名稱": item["名稱"],
                        "進場日期": entry_date,
                        "進場價": round(entry_price, 2),
                        "現價": round(latest_price, 2),
                        "N (ATR)": round(n_atr, 2),
                        "停損價": round(bracket["停損價"], 2),
                        # 結構停利沒有固定價位，用訊號名稱取代，免得顯示一個不存在的目標價。
                        "停利": structural_exit or f"{bracket['停利價']:,.2f}",
                        "狀態": outcome["狀態"],
                        "持有天數": outcome["持有天數"],
                        "報酬率": outcome["報酬率"],
                        "_停利價": bracket["停利價"] if not structural_exit else None,
                        "_結構停利": structural_exit,
                        "_min_profit_n": min_profit_n,
                    }
                )
        st.session_state["strength_test_results"] = pd.DataFrame(results)
        if skipped:
            st.warning("以下標的略過測試：" + "、".join(skipped))

    results_df = st.session_state.get("strength_test_results")
    if results_df is not None and not results_df.empty:
        st.markdown("##### 測試結果")
        triggered = results_df[results_df["狀態"].isin(["已停損", "已停利", "已超時"])]
        if not triggered.empty:
            parts = []
            for status, label in [("已停損", "🔴 已停損"), ("已停利", "🟢 已停利"), ("已超時", "🟡 已超時")]:
                names = list(triggered[triggered["狀態"] == status]["名稱"])
                if names:
                    parts.append(f"{label}：{'、'.join(names)}")
            st.warning("　｜　".join(parts))
        st.caption(
            "🔴 已停損、🟢 已停利、🟡 已超時的列會整列上色。點選任一列可在下方展開該檔的走勢圖。"
        )
        # 底線開頭的欄位是給下方走勢圖用的內部欄位，不顯示在表格裡。
        display_results = mark_status(results_df.drop(columns=[
            c for c in results_df.columns if c.startswith("_")
        ]))
        display_results["報酬率"] = display_results["報酬率"].map(
            lambda v: f"{v:+.2%}" if pd.notna(v) else "—"
        )
        for col in ("進場價", "現價", "停損價", "N (ATR)"):
            display_results[col] = display_results[col].map(
                lambda v: f"{v:,.2f}" if pd.notna(v) else "—"
            )
        selection = st.dataframe(
            style_by_status(display_results),
            width="stretch",
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="strength_results_table",
        )

        selected_rows = selection.selection.rows if selection and selection.selection else []
        if selected_rows:
            _render_strength_detail_chart(
                results_df.iloc[selected_rows[0]], atr_period, stop_mult, target_mult, max_hold_days
            )

        resolved = results_df[results_df["狀態"].isin(["已停利", "已停損"])]
        if not resolved.empty:
            win_rate = (resolved["狀態"] == "已停利").mean()
            avg_return = resolved["報酬率"].mean()
            col1, col2, col3 = st.columns(3)
            col1.metric("已解決筆數", f"{len(resolved)} / {len(results_df)}")
            col2.metric("勝率（已解決中）", f"{win_rate * 100:.1f}%")
            col3.metric("平均報酬率（已解決中）", f"{avg_return * 100:+.2f}%")
        st.caption(
            "「狀態」為套用側欄設定的停損停利規則後、以「進場日期」與「進場價格」"
            "（皆來自熱門強勢股掃描結果）模擬至今的結果；「已超時」代表沒觸價但已超過持有天數上限，"
            "純粹是提醒，不代表已出場。不代表未來績效。"
        )
    elif results_df is not None:
        st.info("清單中的標的目前都沒有足夠資料可以測試。")


def _render_strength_detail_chart(
    row: pd.Series, atr_period: int, stop_mult: float, target_mult: float, max_hold_days: int | None
) -> None:
    """畫出測試結果中被點選那一檔的走勢圖，沿用停損停利規劃頁的同一張圖。"""
    ticker = row["代碼"]
    entry_date = row["進場日期"]
    entry_price = float(row["進場價"])
    today = dt.date.today()
    currency = data_loader.MARKET_CURRENCY.get(row["市場"], "")

    fetch_start = entry_date - dt.timedelta(days=atr_period * 4 + 30)
    price_df = data_loader.load_price_data(ticker, fetch_start, today)
    if price_df.empty:
        st.warning(f"查無 {ticker} 的價格資料。")
        return

    stop_price = float(row["停損價"])
    structural_exit = row.get("_結構停利")
    target_price = row.get("_停利價")

    if structural_exit:
        outcome = trade_planner.check_structural_outcome(
            price_df, entry_date, entry_price, stop_price, structural_exit,
            float(row["N (ATR)"]), min_profit_n=float(row.get("_min_profit_n", 1.0)),
            max_hold_days=max_hold_days,
        )
    else:
        outcome = trade_planner.check_bracket_outcome(
            price_df, entry_date, entry_price, stop_price, float(target_price), "多",
            max_hold_days=max_hold_days,
        )

    st.markdown(f"**{ticker}　{row['名稱']}**")
    cols = st.columns(5)
    cols[0].metric("進場價", f"{currency}{entry_price:,.2f}")
    cols[1].metric("現價", f"{currency}{float(row['現價']):,.2f}")
    cols[2].metric("停損價", f"{currency}{stop_price:,.2f}")
    cols[3].metric("停利", structural_exit if structural_exit else f"{currency}{float(target_price):,.2f}")
    cols[4].metric("狀態", row["狀態"])

    fig = charts.build_bracket_chart(
        price_df, entry_date, entry_price, stop_price,
        float(target_price) if target_price is not None and pd.notna(target_price) else None,
        outcome, ticker,
    )
    st.plotly_chart(fig, use_container_width=True, key=f"strength_chart_{ticker}")


def _render_param_scan_tab() -> None:
    st.caption(
        "對整個股票池「所有歷史進場訊號」（不只是目前活著的那一筆）批次測試多組停損停利倍數，"
        "統計各組合的歷史勝率與平均報酬率——樣本數通常有幾十筆以上，比手動測試幾檔更有統計意義。"
    )

    col_market, col_strategy = st.columns(2)
    market = col_market.radio("市場", ["台股", "美股"], horizontal=True, key="scan_market")
    strategy_options = strategies.list_strategy_options()
    strategy_key = col_strategy.selectbox(
        "策略",
        options=[k for k, _ in strategy_options],
        format_func=lambda k: dict(strategy_options)[k],
        key="scan_strategy",
    )
    strategy = strategies.get_strategy(strategy_key)
    st.caption(strategy.description)

    params = {}
    param_cols = st.columns(len(strategy.params))
    for col, p in zip(param_cols, strategy.params):
        if p.is_int:
            params[p.key] = col.slider(
                p.label, int(p.min_value), int(p.max_value), int(p.default), step=int(p.step), key=f"scan_param_{p.key}"
            )
        else:
            params[p.key] = col.slider(
                p.label, float(p.min_value), float(p.max_value), float(p.default), step=float(p.step), key=f"scan_param_{p.key}"
            )

    today = dt.date.today()
    col_start, col_end, col_atr = st.columns(3)
    start_date = col_start.date_input(
        "回測起始日期", value=today - dt.timedelta(days=365 * 3), max_value=today, key="scan_start"
    )
    end_date = col_end.date_input("回測結束日期", value=today, max_value=today, key="scan_end")
    atr_period = col_atr.slider("ATR 天數 (N)", 5, 50, 20, step=1, key="scan_atr_period")

    stop_options = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]
    target_options = [2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0]
    stop_mults = st.multiselect(
        "要測試的停損倍數（×N）", stop_options, default=[1.5, 2.0, 2.5, 3.0], key="scan_stop_mults"
    )
    target_mults = st.multiselect(
        "要測試的停利倍數（×N）", target_options, default=[3.0, 4.0, 5.0, 6.0], key="scan_target_mults"
    )

    n_universe = len(data_loader.POPULAR_TICKERS[market])
    if st.button("開始掃描", type="primary", key="scan_run_button"):
        if not stop_mults or not target_mults:
            st.warning("請至少各選一個停損倍數與停利倍數。")
        elif start_date >= end_date:
            st.error("起始日期必須早於結束日期。")
        else:
            params_items = tuple(sorted(params.items()))
            with st.spinner(
                f"掃描中，需下載 {n_universe} 檔{market}股票的歷史資料，並測試 "
                f"{len(stop_mults) * len(target_mults)} 組參數組合，請稍候..."
            ):
                results_df, entry_count = param_scan.scan_bracket_grid(
                    market,
                    strategy_key,
                    params_items,
                    start_date,
                    end_date,
                    atr_period,
                    sorted(stop_mults),
                    sorted(target_mults),
                )
            st.session_state["param_scan_results"] = results_df
            st.session_state["param_scan_entry_count"] = entry_count

    results_df = st.session_state.get("param_scan_results")
    entry_count = st.session_state.get("param_scan_entry_count", 0)

    if results_df is None:
        st.info("設定完參數後按「開始掃描」，會需要下載整個股票池的資料，可能需要數十秒。")
        return
    if results_df.empty:
        st.warning("這個策略／參數在這段區間內，股票池中沒有產生足夠可分析的歷史訊號，試試拉長回測區間或換個策略。")
        return

    st.divider()
    st.markdown(f"##### 掃描結果（共 {entry_count} 筆歷史進場訊號）")

    best = results_df.iloc[0]
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("歷史表現最好的組合", f"{best['停損倍數']:.1f}N / {best['停利倍數']:.1f}N")
    col2.metric("平均報酬率", f"{best['平均報酬率'] * 100:+.2f}%")
    col3.metric("勝率", f"{best['勝率'] * 100:.1f}%")
    col4.metric("已解決筆數", f"{int(best['已解決筆數'])}")

    fig = charts.build_param_heatmap(results_df, "平均報酬率", "平均報酬率")
    st.plotly_chart(fig, use_container_width=True)

    display_results = results_df.copy()
    display_results["勝率"] = (display_results["勝率"] * 100).round(1).astype(str) + "%"
    display_results["平均報酬率"] = (display_results["平均報酬率"] * 100).round(2).astype(str) + "%"
    display_results["風報比"] = display_results["風報比"].round(2)
    display_results["獲利因子"] = display_results["獲利因子"].apply(
        lambda v: "∞" if v == float("inf") else (f"{v:.2f}" if pd.notna(v) else "N/A")
    )
    st.dataframe(display_results, width="stretch", hide_index=True)

    st.caption(
        "「平均報酬率」與「勝率」只計算已觸價（已停利／已停損）的樣本，忽略仍在持有中的部分；"
        "「已解決筆數」越少的組合統計上越不可靠（例如停損停利都設很寬時，很多還沒觸價），"
        "請對照著看，不要只看平均報酬率最高那一格。這是歷史模擬結果，不代表未來績效，也有過度配適"
        "（curve-fitting）風險——建議挑一個在熱力圖上鄰近格子（例如 ±0.5N）表現都還不錯的組合，"
        "而不是選單一個數字最高的孤立格子。"
    )


@st.cache_data(ttl=900, show_spinner=False)
def _load_prices_for_log(tickers: tuple[str, ...], earliest: dt.date) -> dict:
    """批次下載交易紀錄裡所有標的的價格。快取 15 分鐘，避免每次切分頁都重抓。"""
    today = dt.date.today()
    start = earliest - dt.timedelta(days=30)
    return {t: data_loader.load_price_data(t, start, today) for t in tickers}


def render_position_page() -> None:
    st.title("部位管理")
    st.caption(
        "「未平倉總覽」一次看完所有在倉部位的現況，不用逐檔重新輸入；"
        "「決策回顧」把當初的進場理由跟實際結果並排，用來檢視自己的判斷品質。"
    )

    with st.sidebar:
        st.header("設定")
        use_hold_limit = st.checkbox(
            "啟用持有天數上限提醒", value=True, key="position_use_hold_limit"
        )
        max_hold_days = None
        if use_hold_limit:
            max_hold_days = st.slider(
                "持有天數上限（天）", 30, 420, 365, step=15, key="position_max_hold_days"
            )

    log = trade_planner.load_trade_log()
    if log.empty:
        st.info("尚無交易紀錄。請先到「停損停利規劃」頁面儲存至少一筆。")
        st.stop()

    tickers = tuple(sorted(log["代碼"].dropna().unique()))
    earliest = trade_planner.parse_entry_dates(log["進場日期"]).min().date()
    with st.spinner(f"更新 {len(tickers)} 檔標的的價格..."):
        price_data = _load_prices_for_log(tickers, earliest)

    evaluated = trade_planner.evaluate_trade_log(log, price_data, max_hold_days=max_hold_days)
    if evaluated.empty:
        st.warning("無法評估任何一筆紀錄。")
        st.stop()

    tab_open, tab_review = st.tabs(["未平倉總覽", "決策回顧"])

    with tab_open:
        _render_open_positions_tab(evaluated, max_hold_days)

    with tab_review:
        _render_review_tab(evaluated)


def _render_open_positions_tab(evaluated: pd.DataFrame, max_hold_days: int | None) -> None:
    open_pos = evaluated[evaluated["狀態"].isin(trade_planner.OPEN_STATUSES)].copy()
    if open_pos.empty:
        st.info("目前沒有未平倉部位。")
        return

    total_value = open_pos["部位市值"].dropna().sum()
    unrealized = (open_pos["報酬率"] * open_pos["部位市值"]).dropna().sum()
    overtime = int((open_pos["狀態"] == "已超時").sum())

    cols = st.columns(4)
    cols[0].metric("未平倉部位數", f"{len(open_pos)} 筆")
    cols[1].metric("部位總市值", f"{total_value:,.0f}" if total_value else "N/A")
    cols[2].metric("未實現損益", f"{unrealized:+,.0f}" if total_value else "N/A")
    cols[3].metric("超時待檢視", f"{overtime} 筆")
    if total_value:
        st.caption("部位市值僅計入有記錄股數的交易；舊版紀錄沒有股數欄位，不會納入合計。")

    # 依「距停損」升冪排序——最接近被掃出場的部位排最前面，優先處理。
    open_pos = open_pos.sort_values("距停損", na_position="last")

    display = open_pos[[
        "代碼", "方向", "進場日期", "進場價", "現價", "停損價", "停利方式",
        "報酬率", "距停損", "持有天數", "狀態", "進場理由", "備註",
    ]].copy()
    display["報酬率"] = display["報酬率"].map(lambda v: f"{v:+.2%}" if pd.notna(v) else "—")
    display["距停損"] = display["距停損"].map(lambda v: f"{v:.2%}" if pd.notna(v) else "—")
    for col in ("進場價", "現價", "停損價"):
        display[col] = display[col].map(lambda v: f"{v:,.2f}" if pd.notna(v) else "—")
    display["進場理由"] = display["進場理由"].fillna("(未填)")
    display["備註"] = display["備註"].fillna("")
    st.dataframe(style_by_status(mark_status(display)), width="stretch", hide_index=True)
    st.caption(
        "🟡 已超時的列會整列上色。「距停損」＝現價還要往不利方向走多少 % 才會觸及停損價，已由小到大排序，"
        "數字最小的部位最接近出場。"
    )

    if overtime:
        names = "、".join(open_pos[open_pos["狀態"] == "已超時"]["代碼"])
        st.warning(
            f"⚠️ {names} 已超過 {max_hold_days} 天仍未觸價。"
            "資金可能卡在橫盤標的上，建議回頭確認當初的進場理由是否還成立。"
        )


def _render_review_tab(evaluated: pd.DataFrame) -> None:
    closed = evaluated[evaluated["狀態"].isin(trade_planner.CLOSED_STATUSES)].copy()
    if closed.empty:
        st.info("尚無已平倉的交易。等有幾筆出場紀錄之後，這裡就會開始有東西可以檢討。")
        return

    win_rate = (closed["狀態"] == "已停利").mean()
    avg_return = closed["報酬率"].mean()
    avg_hold = closed["持有天數"].mean()

    cols = st.columns(4)
    cols[0].metric("已平倉筆數", f"{len(closed)} 筆")
    cols[1].metric("勝率", f"{win_rate:.1%}")
    cols[2].metric("平均報酬率", f"{avg_return:+.2%}")
    cols[3].metric("平均持有天數", f"{avg_hold:.0f} 天")

    st.markdown("##### 依進場理由分類")
    by_reason = trade_planner.summarize_by_reason(evaluated)
    if by_reason.empty:
        st.info("已平倉交易都沒有填進場理由。")
    else:
        shown = by_reason.copy()
        shown["勝率"] = shown["勝率"].map(lambda v: f"{v:.1%}")
        shown["平均報酬率"] = shown["平均報酬率"].map(lambda v: f"{v:+.2%}")
        shown["平均持有天數"] = shown["平均持有天數"].map(lambda v: f"{v:.0f}")
        st.dataframe(shown, width="stretch", hide_index=True)
        st.caption(
            "⚠️ 每一類的筆數通常很少，不要把幾筆交易的勝率當成結論。"
            "這張表的用途是長期累積後看出系統性偏誤，例如某一類理由的勝率持續偏低。"
        )

    st.markdown("##### 逐筆檢討")
    detail = closed[[
        "代碼", "方向", "進場日期", "進場價", "停損價", "停利方式",
        "狀態", "報酬率", "持有天數", "進場理由", "備註",
    ]].copy()
    detail["報酬率"] = detail["報酬率"].map(lambda v: f"{v:+.2%}" if pd.notna(v) else "—")
    for col in ("進場價", "停損價"):
        detail[col] = detail[col].map(lambda v: f"{v:,.2f}" if pd.notna(v) else "—")
    detail["進場理由"] = detail["進場理由"].fillna("(未填)")
    detail["備註"] = detail["備註"].fillna("")
    st.dataframe(
        style_by_status(mark_status(detail).sort_values("進場日期", ascending=False)),
        width="stretch",
        hide_index=True,
    )


def render_kol_page() -> None:
    st.title("X KOL 動態")
    st.caption("追蹤財經／技術分析類 KOL 在 X 上的最新貼文，名單來源為使用者提供的 Google Sheet。")

    try:
        roster = kol_feed.load_kol_roster()
    except Exception as exc:
        st.error(f"讀取 KOL 名單失敗：{exc}")
        return

    bearer_token = kol_feed.get_bearer_token()
    if not bearer_token:
        st.warning(
            "尚未設定 X API 金鑰，目前僅顯示名單，不會抓取即時貼文。\n\n"
            "設定方式：至 https://developer.x.com 申請開發者帳號並取得 Bearer Token，"
            "然後在專案的 `.streamlit/secrets.toml` 加入：\n\n"
            "```toml\n[x_api]\nbearer_token = \"你的 token\"\n```\n\n"
            "部署到 Streamlit Cloud 時，改到該服務的 App settings → Secrets 貼上同樣內容，"
            "不要把金鑰寫進程式碼或提交進 Git。"
        )

    display_roster = roster.rename(
        columns={"name": "名稱", "url": "連結", "memo": "簡介", "follower": "粉絲數(千)"}
    )
    show_cols = [c for c in ["名稱", "連結", "簡介", "粉絲數(千)"] if c in display_roster.columns]
    st.dataframe(
        display_roster[show_cols].sort_values("粉絲數(千)", ascending=False),
        width="stretch",
        hide_index=True,
        column_config={"連結": st.column_config.LinkColumn("連結")},
    )

    if not bearer_token:
        return

    st.divider()
    selected_names = st.multiselect(
        "選擇要抓取貼文的 KOL（建議一次選少數幾位，避免超過 API 額度）",
        roster["name"].tolist(),
    )
    if st.button("抓取最新貼文", key="kol_fetch"):
        results = {}
        for name in selected_names:
            row = roster[roster["name"] == name].iloc[0]
            username = row["username"]
            if not username:
                results[name] = {"error": "無法從網址解析帳號。"}
                continue
            with st.spinner(f"抓取 @{username} 的貼文..."):
                results[name] = kol_feed.fetch_recent_tweets(username, bearer_token)
        st.session_state["kol_results"] = results

    kol_results = st.session_state.get("kol_results")
    if kol_results:
        for name, result in kol_results.items():
            st.markdown(f"**{name}**")
            if "error" in result:
                st.error(result["error"])
            elif not result.get("tweets"):
                st.info("暫無最新貼文。")
            else:
                for tw in result["tweets"]:
                    created = (tw.get("created_at") or "")[:10]
                    st.write(f"{created}　{tw.get('text', '')}")
                    tw_metrics = tw.get("public_metrics", {})
                    st.caption(f"讚 {tw_metrics.get('like_count', 0)}　轉推 {tw_metrics.get('retweet_count', 0)}")
            st.divider()


def main() -> None:
    mode = st.sidebar.radio(
        "功能",
        ["股票回測", "海龜交易法則", "停損停利規劃", "部位管理", "強勢股測試", "X KOL 動態"],
    )
    st.sidebar.divider()
    if mode == "股票回測":
        render_backtest_page()
    elif mode == "海龜交易法則":
        render_turtle_page()
    elif mode == "停損停利規劃":
        render_trade_planner_page()
    elif mode == "部位管理":
        render_position_page()
    elif mode == "強勢股測試":
        render_strength_test_page()
    else:
        render_kol_page()


if __name__ == "__main__":
    main()
