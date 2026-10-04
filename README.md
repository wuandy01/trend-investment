# Trend Investment — Strategy Backtesting & Trade Planning Platform

A Streamlit application for backtesting technical trading strategies on Taiwan and US equities, with parameter sweeping, cross-market screening, and ATR-based trade planning.

> Built for strategy research and education. Backtested results are historical simulations and do not constitute investment advice or a prediction of future performance.

繁體中文版說明請見 [README.zh-TW.md](README.zh-TW.md)。

---

## Why this repo is worth a look

Most backtesting side-projects report a headline return and stop there. The design decisions below were the actual work, and each one is documented with the experiment that motivated it.

### Bias and robustness controls

| Control | Implementation |
|---|---|
| **No look-ahead bias** | Every strategy signal is executed with a one-trading-day delay. A signal generated on today's close cannot be traded at today's close. |
| **Transaction costs** | Configurable per-round-trip cost (%), included in all headline performance metrics (total return, CAGR, Sharpe). Trade-level detail shows raw price return, and the distinction is stated explicitly rather than blurred. |
| **Benchmark comparison** | Every backtest is reported against buy-and-hold over the identical period, so strategy performance is never presented in isolation. |
| **Overfitting defense** | The parameter sweep outputs a heatmap across stop-loss × take-profit multiples rather than a single "best" number. The documented guidance is to select a *neighborhood* of adjacent cells that all perform well, not the single highest isolated cell. |
| **Sample size awareness** | The sweep tests *every historical entry signal* across the stock pool (typically 50–200 signals), not just currently-open positions, because a handful of live positions is not a statistically meaningful sample. |

### Documented experiments

These are findings from running the tool, not features:

**Trend filter strictness (TSMC, Aug 2023 – Aug 2026).** Comparing three settings on the Turtle system:

| Setting | Total return | Short trades |
|---|---|---|
| No filter | 83% | 7 trades, 6 losses (−39% drag) |
| Simple (price vs MA200) | 60% | 1 trade |
| Strict (MA5 > MA20 > MA60 > MA200) | 31% | — |

The strict filter also removes valid long entries after normal pullbacks. Conclusion recorded in the docs: no setting is universally better; the simple filter is the better default, the strict filter suits a more conservative, lower-frequency posture.

**Stop-loss / take-profit sweep (Taiwan equities, MA cross 20/60, 2023–2026).** Across 194 historical entry signals, the best combination was 3.0N / 6.0N (avg +4.29%, 53.8% win rate), with a consistent trend that wider stops produced higher average returns. This independently corroborated an earlier manual finding that a 2N stop is too tight for some tickers and gets shaken out by normal pullbacks.

**Holding-period distribution.** The fixed stop/target planner has no time dimension, so a position can sit unresolved indefinitely. Measuring TSMC over three years (fixed 2N/5N, entering every 15 trading days): median resolution was 18 days, but 35% of trades took over 30 days and 8% took over 60. The 30-day timeout warning default was chosen from this distribution rather than picked arbitrarily.

**Screener ranking design.** The original ranking sorted by "largest gain since signal," which structurally surfaces stocks that have *already* run. Tested on TSMC with MA cross, the top-ranked result was a signal triggered 399 days earlier — the move was long over. Re-sorting by holding days ascending returned a signal triggered 5 days prior with only 0.6% gain: an actual emerging opportunity. Both sort modes are retained, because "confirm momentum" and "find new entries" are different questions.

---

## Features

**Strategy backtesting** — Five configurable strategies (MA cross, RSI mean-reversion, MACD, Bollinger Bands, ATR channel breakout) across Taiwan (`.TW`) and US tickers. Outputs total return, CAGR, Sharpe ratio, max drawdown, win rate, and profit factor against buy-and-hold, with interactive Plotly candlestick charts, equity curves, drawdown plots, and per-trade detail.

**Turtle Trading system** — A separate long/short ATR breakout engine implementing the classic rules: 20/55-day breakout entries, pyramid scaling at 0.5N intervals up to 4 units, 10/20-day exits. Runs on its own backtest engine (`src/turtle.py`) rather than sharing `backtest.py`, because the main engine is long/flat only and cannot represent shorts. Includes an optional long-term trend filter with two strictness levels.

**Trade planner** — Fixed ATR bracket calculator (stop = entry − k×N, target = entry + m×N) with risk/reward ratio, current status, MFE/MAE over the holding period, volume behavior versus the 20-day pre-entry average, and optional local trade logging.

**Screener** — Scans 30 Taiwan and 30 US tickers for active entry signals under the selected strategy, ranked by either "newly triggered" or "strongest momentum." Selected tickers can be pushed to batch stop/target simulation.

**Parameter sweep** — Batch-tests multiple stop/target multiple combinations across all historical signals in the pool, output as a heatmap plus a ranked detail table.

**KOL feed** — Reads a publicly shared Google Sheet of finance commentators via its CSV export URL (no API key required). Post fetching uses the official X API v2 only — the project does not scrape X, and without a token it simply displays the list.

---

## Architecture

```
app.py                  Streamlit UI — page routing and layout composition
src/
  data_loader.py        Price data download (yfinance) and ticker normalization
  indicators.py         Technical indicators (MA, RSI, MACD, Bollinger, ATR)
  strategies.py         Signal logic and parameter definitions for five strategies
  backtest.py           Vectorized backtest engine and performance metrics
  charts.py             Plotly chart generation
  screener.py           Cross-ticker scanning and ranking
  param_scan.py         Batch stop/target parameter sweep across historical signals
  fundamentals.py       Yahoo Finance fundamentals (ratios, statements, ratings)
  turtle.py             Turtle rules: long/short signals, pyramiding, separate engine
  trade_planner.py      ATR bracket calculation, trigger detection, local trade log
  kol_feed.py           KOL list loading and X API v2 post fetching
```

Two implementation notes worth flagging:

- **Indicators are implemented in pure pandas**, deliberately avoiding TA-Lib and other C-extension dependencies, so the app deploys to Streamlit Community Cloud without build issues.
- **Fundamentals use Yahoo Finance only.** Futu (富途牛牛) has no public API — it requires a locally installed and authenticated OpenD gateway, which breaks once the app is deployed to the cloud. Yahoo was chosen for deployability, accepting reduced data coverage.

---

## Setup

Requires Python 3.9+.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Opens at `http://localhost:8501`.

**Optional — X API access:** copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and add a Bearer Token from [developer.x.com](https://developer.x.com). `secrets.toml` is gitignored. On Streamlit Cloud, use Settings → Secrets instead. Posts are only fetched on an explicit button press, to avoid burning API quota on every page rerun.

---

## Limitations

- **The main backtest engine is long/flat only** (0/1 position), with no shorting or leverage. Shorts are only available on the Turtle page, via the separate engine.
- **The Turtle implementation omits the original 2N stop-loss rule.** Exits follow only the N-day breakout rule and any user-specified bracket. This is a deliberate, documented deviation, not an oversight — but it means results are not directly comparable to the canonical Turtle system.
- **Parameter sweep results are in-sample historical simulation** and carry overfitting risk. No walk-forward or out-of-sample holdout is implemented; the heatmap-neighborhood heuristic is a mitigation, not a substitute.
- **Local trade log is not durable in cloud deployment.** `data/trade_log.csv` relies on a persistent filesystem; Streamlit Cloud's is ephemeral and will lose records on redeploy or sleep/wake.
- **Fundamentals coverage is limited** to commonly used line items from Yahoo Finance, filtered to avoid information overload.
- **X integration is not implemented beyond the official API**, and meaningful read volume on X requires a paid tier.

---

## Data sources

Price data via [yfinance](https://github.com/ranaroussi/yfinance) (Yahoo Finance). Fundamentals via Yahoo Finance. KOL list via a publicly shared Google Sheet CSV export. Social posts via the official X API v2.

No unauthorized scraping is performed anywhere in this project.
