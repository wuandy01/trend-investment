# 趨勢投資回測系統

用 Streamlit 打造的股票交易策略回測網站，支援台股與美股，可選擇股票、回測起訖日期與技術指標策略，並畫出技術分析圖與權益曲線。

> 本工具僅供策略研究與教育用途，所有數據與回測結果不構成投資建議。

## 功能

- **選擇股票**：台股（自動補上 `.TW`）與美股，內建常見標的清單，也可自行輸入代碼
- **選擇回測區間**：起始日期／結束日期
- **五種策略**（皆可調整參數）：均線交叉、RSI 超買超賣、MACD、布林通道、ATR 通道突破
- **回測績效**：總報酬率、CAGR、夏普比率、最大回檔、勝率、獲利因子，並與買進持有比較
- **技術分析圖**：Plotly 互動式 K 線圖（紅漲綠跌），疊加指標線／通道與進出場標記，下方含成交量與指標子圖
- **權益曲線**：策略 vs 買進持有的資產成長曲線與回檔幅度
- **交易明細**：每筆交易的進出場日期、價格、報酬率
- **熱門強勢股排行**：依左側選定的策略與參數，掃描台股與美股熱門標的（各 30 檔），列出目前有進場訊號、且訊號後漲幅最高的前 10 名（台美股分開列示）

## 專案結構

```
app.py                  Streamlit 主程式（UI 組裝）
src/
  data_loader.py        股票資料下載（yfinance）與代碼正規化
  indicators.py          技術指標（MA、RSI、MACD、布林通道、ATR）
  strategies.py          五種策略的訊號邏輯與參數定義
  backtest.py             向量化回測引擎與績效指標
  charts.py                Plotly 圖表產生
  screener.py              跨股票掃描熱門強勢股排行
.streamlit/config.toml  主題與伺服器設定
requirements.txt
```

## 本機執行

需要 Python 3.9+。

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

啟動後瀏覽器會自動開啟 `http://localhost:8501`。

## 部署到 Streamlit Community Cloud（免費）

1. 把整個專案推上 GitHub（可以是 public repo）
2. 到 [share.streamlit.io](https://share.streamlit.io) 用 GitHub 帳號登入
3. 點 **New app**，選擇這個 repo、分支，Main file path 填 `app.py`
4. 按 **Deploy**，等待安裝相依套件（約 1-3 分鐘）即可取得公開網址

之後每次 `git push` 到該分支，Streamlit Cloud 會自動重新部署。

## 策略說明

| 策略 | 邏輯 |
| --- | --- |
| 均線交叉 (MA Cross) | 短期均線由下往上穿越長期均線時做多，跌破時出場 |
| RSI 超買超賣 | RSI 跌破超賣線進場，站上超買線出場（均值回歸） |
| MACD | MACD 線穿越訊號線判斷動能轉折 |
| 布林通道 | 價格向上突破下軌進場（觸底反彈），觸及上軌出場 |
| ATR 通道突破 | 以 EMA 為中軌、ATR 為通道寬度，價格突破上軌進場、跌破下軌出場 |

所有策略訊號皆延遲一個交易日才執行（避免使用未來資料），並可在側欄設定每次進出場的交易成本（%）。

## 技術重點

- 股價資料來自 [yfinance](https://github.com/ranaroussi/yfinance)（Yahoo Finance），台股用 `.TW` 後綴、美股直接用代碼
- 回測為長多／空手（0/1）的向量化回測，不含放空與槓桿
- 交易明細顯示的報酬率為進出場價格的原始報酬（未扣成本），整體績效指標（總報酬率、CAGR 等）已計入交易成本
- 熱門強勢股排行採批次下載（`yfinance` 多代碼一次請求）以加速掃描，「訊號後漲幅」＝該股票目前策略訊號觸發進場那天至今的價格漲幅，只有目前正處於進場訊號中的股票才會列入排行
