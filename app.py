"""趨勢投資回測系統 —— Streamlit 主程式。"""

from __future__ import annotations

import datetime as dt
import math

import streamlit as st

from src import backtest, charts, data_loader, strategies

st.set_page_config(page_title="趨勢投資回測系統", layout="wide")


def format_metric(key: str, value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A"
    if isinstance(value, float) and math.isinf(value):
        return "∞"
    if key in {"總報酬率", "年化報酬率 (CAGR)", "年化波動度", "最大回檔", "勝率", "平均每筆報酬率", "買進持有報酬率"}:
        return f"{value * 100:,.2f}%"
    if key == "最終資產":
        return f"{value:,.0f}"
    if key in {"夏普比率", "獲利因子"}:
        return f"{value:,.2f}"
    if key == "交易次數":
        return f"{int(value)}"
    return str(value)


def main() -> None:
    st.title("趨勢投資回測系統")
    st.caption("選擇股票、設定回測區間與策略參數，檢視技術分析圖與回測績效。")

    with st.sidebar:
        st.header("回測設定")

        market = st.radio("市場", ["台股", "美股"], horizontal=True)
        popular = data_loader.POPULAR_TICKERS[market]
        currency = data_loader.MARKET_CURRENCY[market]

        options = [f"{code}  {name}" for code, name in popular.items()] + ["－ 自行輸入代碼 －"]
        choice = st.selectbox("選擇股票", options)
        if choice == "－ 自行輸入代碼 －":
            placeholder = "例如 2330.TW" if market == "台股" else "例如 AAPL"
            raw_code = st.text_input("輸入股票代碼", placeholder=placeholder)
        else:
            raw_code = choice.split()[0]
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

    tab_chart, tab_equity, tab_trades = st.tabs(["技術分析圖", "權益曲線", "交易明細"])

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
            st.dataframe(display_trades, use_container_width=True, hide_index=True)
            st.caption("交易明細為進出場價格的原始報酬率（未扣除成本），整體績效指標已計入每次進出場成本。")


if __name__ == "__main__":
    main()
