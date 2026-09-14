# 股票買賣時機信號系統（獨立運行版）

用公開行情 + 技術指標產生買賣時機**提醒**；OpenAI（Astra）消息面評分為可選增強層。
**本系統不依賴任何 AI 服務也能完整運行**，不連任何券商帳戶、不自動下單——
收到信號後由你自己（例如在 Webull）手動確認操作。

## 獨立性說明

- 機械信號層（指標 + 規則）：純本地 Python + 免費公開資料，**零 AI 依賴、零費用**
- Astra 消息面層：可選，沒填 key 自動跳過
- 已安裝 macOS 定時任務（launchd，每 15 分鐘）：開盤時段自動掃描、收盤後自動出日報、
  休市 1 秒退出（零網路請求）；時區與夏令/冬令自動處理，無需維護

## 日常使用：只改一個檔案

監控清單在 `watchlist.txt`，純文字，每行一個標的：

```
AAPL                            ← 普通股票，直接寫代碼
NVDA
CRWV
SQQQ : leveraged_inverse : QQQ  ← 反向/槓桿產品要這樣標（消息面看 QQQ）
```

存檔後下一次掃描自動生效。加股、刪股、改屬性都只動這個檔案。

## 手動執行

```bash
cd stock-signal
./run.sh --check        # 自檢：資料源、清單、時段狀態
./run.sh --mode auto    # 和定時任務一樣的智能模式
./run.sh --mode daily   # 立即出完整日報（含 LLM 消息面，若已設 key）
./run.sh --mode intraday
```

## 啟用 Astra 消息面 / Telegram 推送（可選）

```bash
cp .env.example .env   # 填 OPENAI_API_KEY、TELEGRAM_*（key 只放這裡，永遠不要貼進對話）
```

## 定時任務管理

```bash
launchctl list | grep stocksignal                      # 查看狀態
tail -20 ~/.../stock-signal/launchd.log                # 看定時執行日誌
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/local.stocksignal.plist   # 停止
rm ~/Library/LaunchAgents/local.stocksignal.plist       # 徹底移除
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.stocksignal.plist # 重新啟用
```

**已知限制**：Mac 睡覺時定時任務不會跑（喚醒後補跑一次，auto 模式會自動避開重複報告）。
若要 24/7 不間斷（例如假期出遊），可考慮部署到你的 DigitalOcean 主機——那台同時跑著
quizzes.it 生產站，動它之前先和我確認方案。

## 反向/槓桿產品專用規則（leveraged_inverse）

- 盤中異動門檻從 3% 提高到 6%（槓桿產品的正常波動幅度）
- 盤中虧損觸及 5% → 🛑 **強制止損提醒**
- 每次報告附 decay 提醒：此類產品不建議多日持有
- 底層股票財報前 5 天 → ▲ 高波動預警
- Astra 評分方向自動反轉（底層利多 = 反向產品利空）

## 安全底線

- 系統唯讀；永遠不要給它券商帳號、密碼、API key
- Astra 消息面是參考，機械信號以規則引擎為準；兩者衝突時**寧可不做**
- 本工具提供的是提醒與數據，不構成投資建議；槓桿反向 ETF 可在單日虧損過半
