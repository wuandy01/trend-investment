# 趨勢投資回測系統

用 Streamlit 打造的股票交易策略回測網站，支援台股與美股，可選擇股票、回測起訖日期與技術指標策略，畫出技術分析圖與權益曲線，並掃描熱門強勢股、查看基本面財務數據、追蹤財經 KOL 動態。

> 本工具僅供策略研究與教育用途，所有數據與回測結果不構成投資建議。

左側「功能」切換兩個主頁面：**股票回測**（含 5 個分頁）與 **X KOL 動態**（獨立頁面，不受股票/策略設定影響）。

## 功能：股票回測

- **選擇股票**：台股（自動補上 `.TW`）與美股，內建常見標的清單，也可自行輸入代碼
- **選擇回測區間**：起始日期／結束日期
- **五種策略**（皆可調整參數）：均線交叉、RSI 超買超賣、MACD、布林通道、ATR 通道突破
- **回測績效**：總報酬率、CAGR、夏普比率、最大回檔、勝率、獲利因子，並與買進持有比較
- **技術分析圖**：Plotly 互動式 K 線圖（紅漲綠跌），疊加指標線／通道與進出場標記，下方含成交量與指標子圖
- **權益曲線**：策略 vs 買進持有的資產成長曲線與回檔幅度
- **交易明細**：每筆交易的進出場日期、價格、報酬率
- **熱門強勢股排行**：依左側選定的策略與參數，掃描台股與美股熱門標的（各 30 檔），列出目前有進場訊號、且訊號後漲幅最高的前 10 名（台美股分開列示）
- **財務數據**：Yahoo Finance 基本面——公司資訊、關鍵比率（P/E、P/B、ROE、毛利率等）、三大財報（損益表／資產負債表／現金流量表，近 5 年）、分析師評等與目標價

> 富途牛牛 (Futu) 沒有公開 API，需要本機安裝並登入 OpenD 閘道才能串接，部署到雲端後會失效，因此本專案的財務數據**只採用 Yahoo Finance**。

## 功能：X KOL 動態

- 讀取使用者提供、公開分享的 [Google Sheet KOL 名單](https://docs.google.com/spreadsheets/d/1GByalwK-RUT0p4_ydFsTydHeun6Wwo825C5XyILm3IM)（姓名／連結／簡介／粉絲數），以公開 CSV 匯出網址讀取，不需金鑰
- 設定 X API Bearer Token 後，可選擇特定 KOL 抓取最新貼文（見下方「設定 X API 金鑰」）
- **不會做未經授權的爬蟲**：X 對非官方存取的防爬機制很嚴格，本專案只走官方 X API v2，沒有金鑰就只顯示名單、不抓貼文

### 設定 X API 金鑰

1. 到 [developer.x.com](https://developer.x.com) 申請開發者帳號，取得 Bearer Token（注意：有意義的讀取量通常需要付費方案，免費額度非常有限，請以官網當下公告的方案為準）
2. 複製 `.streamlit/secrets.toml.example` 為 `.streamlit/secrets.toml`，填入：
   ```toml
   [x_api]
   bearer_token = "你的 token"
   ```
3. `secrets.toml` 已加入 `.gitignore`，不會被提交進版本控制
4. 部署到 Streamlit Community Cloud 時，改到該 App 的 **Settings → Secrets** 貼上同樣內容

## 專案結構

```
app.py                  Streamlit 主程式（UI 組裝、頁面切換）
src/
  data_loader.py        股票資料下載（yfinance）與代碼正規化
  indicators.py          技術指標（MA、RSI、MACD、布林通道、ATR）
  strategies.py          五種策略的訊號邏輯與參數定義
  backtest.py             向量化回測引擎與績效指標
  charts.py                Plotly 圖表產生
  screener.py              跨股票掃描熱門強勢股排行
  fundamentals.py          Yahoo Finance 基本面財務數據
  kol_feed.py              X KOL 名單讀取與貼文抓取（X API v2）
.streamlit/
  config.toml            主題與伺服器設定
  secrets.toml.example   X API 金鑰設定範本（複製為 secrets.toml 後填入）
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
- 財務數據的三大財報只挑常用項目顯示（避免資訊過載），數字自動換算成萬／億／兆
- X KOL 名單透過 Google Sheet 的公開 CSV 匯出網址讀取（`.../export?format=csv&gid=0`），不需要 Google API；抓取貼文需要使用者自行選擇 KOL 並按按鈕才會呼叫 X API，避免每次頁面重整就消耗 API 額度
