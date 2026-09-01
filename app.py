"""趨勢投資回測系統 —— Streamlit 主程式。"""

from __future__ import annotations

import datetime as dt
import math

import pandas as pd
import streamlit as st

from src import backtest, charts, data_loader, fundamentals, kol_feed, screener, strategies, trade_planner, turtle

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
    display["進場日期"] = display["進場日期"].dt.date
    display["現價"] = display["現價"].round(2)
    display["進場價格"] = display["進場價格"].round(2)
    return display


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
            f"根據左側目前選擇的策略「{strategy.name}」與參數，分別掃描台股（{n_tw} 檔）與美股（{n_us} 檔）"
            f"熱門標的，找出目前有進場訊號、且訊號後累積漲幅最高的前 10 名。"
        )
        scan_clicked = st.button("掃描熱門強勢股排行", key="scan_ranking")

        if scan_clicked:
            params_items = tuple(sorted(params.items()))
            with st.spinner("掃描中，需下載多檔股票資料，請稍候..."):
                st.session_state["ranking_result"] = {
                    "strategy_name": strategy.name,
                    "台股": screener.scan_market("台股", strategy_key, params_items, start_date, end_date),
                    "美股": screener.scan_market("美股", strategy_key, params_items, start_date, end_date),
                }

        ranking_state = st.session_state.get("ranking_result")
        if ranking_state is None:
            st.info("點擊上方按鈕開始掃描（首次掃描需下載較多資料，可能需要數秒到數十秒）。")
        else:
            st.caption(f"掃描時使用的策略：{ranking_state['strategy_name']}")
            for rank_market in ["台股", "美股"]:
                st.markdown(f"**{rank_market}**")
                ranking_df = ranking_state[rank_market]
                if ranking_df.empty:
                    st.info(f"目前沒有{rank_market}標的符合此策略的進場條件。")
                else:
                    st.dataframe(
                        format_ranking_table(ranking_df),
                        width="stretch",
                        hide_index=True,
                    )
            st.caption("「訊號後漲幅」為自策略進場訊號觸發日起算至今的價格漲幅，僅代表目前訊號的强弱，不代表未來績效。")

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
        target_mult = st.slider("停利倍數（×N）", 0.5, 10.0, 5.0, step=0.5, key="planner_target_mult")

        st.divider()
        use_hold_limit = st.checkbox("啟用持有天數上限提醒", value=True, key="planner_use_hold_limit")
        max_hold_days = None
        if use_hold_limit:
            max_hold_days = st.slider(
                "持有天數上限（天，僅提醒不會自動出場）", 5, 180, 30, step=5, key="planner_max_hold_days"
            )

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
    row1[3].metric("停利價", f"{currency}{target_price:,.2f}")
    st.caption(f"風報比（停利距離 ÷ 停損距離）＝ {bracket['風報比']:.2f}")

    outcome = trade_planner.check_bracket_outcome(
        price_df, entry_date, entry_price, stop_price, target_price, direction, max_hold_days=max_hold_days
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
    note = st.text_input("備註（選填）", key="planner_note")
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
            "停利價": round(target_price, 2),
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
        display_log["進場日期"] = pd.to_datetime(display_log["進場日期"]).dt.date
        display_log["備註"] = display_log["備註"].fillna("")
        st.dataframe(display_log.sort_values("記錄時間", ascending=False), width="stretch", hide_index=True)
    st.caption(
        "交易紀錄儲存在本機的 data/trade_log.csv，只在你自己電腦上執行時可靠保存；"
        "若部署到 Streamlit Cloud 等雲端平台，檔案系統通常是暫時性的，重新部署或休眠喚醒後可能會消失，"
        "不建議在雲端版本依賴這個檔案做長期紀錄。"
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
    mode = st.sidebar.radio("功能", ["股票回測", "海龜交易法則", "停損停利規劃", "X KOL 動態"])
    st.sidebar.divider()
    if mode == "股票回測":
        render_backtest_page()
    elif mode == "海龜交易法則":
        render_turtle_page()
    elif mode == "停損停利規劃":
        render_trade_planner_page()
    else:
        render_kol_page()


if __name__ == "__main__":
    main()
