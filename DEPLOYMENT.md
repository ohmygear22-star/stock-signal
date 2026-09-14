# Droplet 部署設計（已執行）

> **執行日誌 2026-09-12**：全部八項實施完成並部署到 152.42.220.26（/opt/stock-signal）。
> systemd：stocksignal.service（daemon，active）+ stocksignal-bot.service（等 .env 填 key 後 restart）。
> 驗收：端口零新增（24=24 基線）、生產站正常響應、心跳正常、Serenity 首拉基線化。
> Mac 端 launchd 已卸載（plist 保留為降級備案）。
> 既有瑕疵（非本次造成，僅報告）：caddy 的 systemd 狀態残留 2026-06-07 的 failed(timeout) 記錄，
> 進程本身正常服務中；建議日後 `systemctl reset-failed caddy` 清理（待機主批准）。
> 機主待辦：.env 填 TELEGRAM_BOT_TOKEN/CHAT_ID（+可選 OPENAI_API_KEY）→
> `systemctl restart stocksignal-bot`；Webull 查 ETF 代碼後加 watchlist.txt。

## 1. 架構總覽

```
droplet（Ubuntu）
├── caddy + quizzes.it 生產站        ← 完全不動
└── /opt/stock-signal/               ← 新增，純出站連線
      ├── run.sh → main.py --mode auto
      ├── watchlist.txt（監控清單，改檔即生效）
      ├── .env（OpenAI/Telegram key，你自己填）
      └── signals.log（信號紀錄）

systemd timer 每 15 分鐘喚醒一次：
開盤 → 盤中掃描｜收盤後 → 每日報告（自動去重）｜休市 → 1 秒退出
```

- 不開任何端口、不加域名、不碰 caddy —— 對外完全不可見
- 每 15 分鐘只活幾秒，記憶體峰值 ~50MB，CPU 忽略不計
- 重啟/斷電後 systemd 自動恢復（Persistent=true，錯過的週期補跑一次，
  auto 模式的去重機制保證不會重複出報告）

## 2. 檔案布局

| 位置 | 內容 |
|---|---|
| `/opt/stock-signal/` | 代碼 + venv + watchlist.txt + .env + signals.log |
| `/etc/systemd/system/stocksignal.service` | 單次執行單元（oneshot） |
| `/etc/systemd/system/stocksignal.timer` | 每 15 分鐘觸發 |

## 3. systemd 單元（草案）

```ini
# stocksignal.service
[Unit]
Description=Stock signal scanner
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/stock-signal
ExecStart=/opt/stock-signal/run.sh --mode auto
TimeoutStartSec=180
```

```ini
# stocksignal.timer
[Unit]
Description=Run stock signal scanner every 15 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
Persistent=true

[Install]
WantedBy=timers.target
```

## 4. 部署步驟（確認後執行）

1. SSH 連線 + 環境檢查：python3 版本（需 ≥3.10；若是 20.04 舊系統我先做兼容處理）、磁碟空間
2. rsync 代碼（排除本機 .venv、日誌、.last_daily）
3. droplet 上建 venv + pip install
4. 安裝 systemd 單元，`systemctl enable --now stocksignal.timer`
5. 驗收測試（見第 6 節）
6. **停用 Mac 本機 launchd**（避免兩邊重複推送；plist 保留，一條命令可恢復）

## 5. 日常維護流程

- **改監控清單**：在 droplet 上 `nano /opt/stock-signal/watchlist.txt` 存檔即生效，不用重啟
- **改代碼**：本機改好 → rsync → 下一次執行自動用新版（每次都是新進程，無需重啟）
- **填 key**：`.env` 只在 droplet 上編輯，永不經過對話
- **看日誌**：`tail -50 /opt/stock-signal/signals.log`（信號）或 `journalctl -u stocksignal`（系統層）

## 6. 驗收標準（全過才算部署完成）

- [ ] `systemctl status stocksignal.timer` 顯示 active
- [ ] droplet 上 `./run.sh --check`：清單全部 ✓
- [ ] 手動跑一次 auto 模式，signals.log 出現報告
- [ ] `ss -tlnp` 對比部署前：**沒有新增任何監聽端口**
- [ ] `curl -I https://quizzes.it`（或站點實際域名）仍是 200，生產站正常
- [ ] Mac 端 launchd 已停用，不會重複推送

## 7. 風險與對策

| 風險 | 對策 |
|---|---|
| yfinance 被限流 | 每 15 分鐘 × 3 檔標的，量極輕；失敗只影響當次，下次自動重試 |
| 系統 Python 太舊（<3.10） | 部署前先檢查；太舊就先做代碼兼容處理再部署 |
| signals.log 無限長大 | 純文字每天幾 KB；任何時候可安全清空（`> signals.log`），或日後加 logrotate |
| droplet 故障 | Mac 端 launchd 一條命令即可恢復為本機運行（降級備案） |

## 8. 回滾（3 條命令徹底移除）

```bash
systemctl disable --now stocksignal.timer
rm /etc/systemd/system/stocksignal.service /etc/systemd/system/stocksignal.timer
rm -rf /opt/stock-signal
```

## 9. 待你確認的點

1. 用哪個 IP：`146.190.81.26` 還是 `152.42.220.26`？（是否與 quizzes 同機）
2. SSH 用 root + 現有 `digitalocean_quizzes` key？（droplet 預設就是這樣裝的）
3. 確認設計無誤後回覆 proceed，才開始部署。

## 10. X 貼文監控層（2026-09-12 設計，待確認）——多帳號架構

用戶指定的 X 帳號一律進這一層，逐帳號評估「價值 × 可接入性」，分級處理：

### 10.1 Serenity @aleabitoreddit —— 建議納入（免費路徑已驗證）

- AI/半導體供應鏈研究，~1M 粉絲，貼文有真實市場影響力
- 接入：`github.com/yan-labs/serenity-aleabitoreddit` 推文歸檔（6,500+ 條 JSON，活躍維護）
  ——每小時拉 raw JSON，狀態檔按推文 ID 去重，只處理增量
- 作用：①新貼提及監控清單 ticker → 直接警報；②近 24h 相關貼併入 LLM 消息面上下文
- 限制：主打光模組（AAOI/SIVE/FOCI），CRWV 非主戰場 → ticker 比對只在真正提及時觸發；
  延遲小時級；4,502%/225x 為自稱不可驗證 → 只當輿情溫度計，絕不跟單

### 10.2 JACKAL @Jackal_quant —— 建議暫緩（理由如下，可覆議）

- 中文量化/宏觀博主，~5-8K 粉絲，無第三方歸檔（太小沒人維護）
- **無免費可靠接入路徑**：X API 要約 $200/月（未核實）；RSSHub/Nitter 屬實驗性質，
  隨時悄悄失效——為一個小帳號引入脆弱抓取模組，維護成本 > 信號價值
- 可信度疑點：自述背景無法驗證；且在推銷付費「Jackal 工具箱」（內容行銷漏斗動機）
- 粉絲量級 → 貼文無市場影響力；宏觀內容與 ticker 比對幾乎不會命中
- **若用戶堅持**：可加 RSSHub 實驗性路由（明確標註「可能靜默失效」），
  或等未來有歸檔/官方可負擔方案再納入

### 紀律（適用所有帳號）

X 貼文一律是「消息面輸入」，不是信號本體；機械規則為準，絕不自動跟單；
只從指定真號的歸檔/來源取數，防假冒帳號。

## 11. 宏觀事件層（2026-09-12 設計，待確認）

用戶需求：宏觀/地緣/政策事件（領袖人物發言、中東戰局→能源→通膨→利率鏈、財政部國債回購等）
也要納入系統。設計為三個組件：

1. **經濟日曆（事前預警）**：`macro_calendar.txt` 純文字維護已知日程——FOMC、CPI/PCE、
   非農、重要聽證會/大選辯論等；日報自動倒數（同財報預警邏輯），臨近事件提醒
   「降低槓桿倉位/不隔夜」。日程免費可查（聯準會/BLS 官網），人工季度更新一次即可。
2. **宏觀新聞流（事後解讀）**：定時拉免費市場新聞源（Yahoo/Google News RSS、
   Finviz 快訊），餵給 LLM 做主題分類——利率/Fed、地緣/能源、關稓/政治、
   流動性/債市、財報季等——日報新增「宏觀面」一節：今日活躍主題 + 對監控清單
   的方向影響（如：Fed 偏鷹 → 高 beta 成長股承壓）。盤中不重複跑 LLM（成本與延遲不划算）。
3. **事件日標記進信號賬本**：宏觀事件發生日在 ledger 打 flag——日後可統計
   「技術信號在事件日 vs 平常日的命中率差異」，如果事件日命中率明顯變差，
   就給規則引擎加「事件日靜音/降權」開關（用數據決定，不拍腦袋）。

**誠實的預期管理**：事件的「方向」本質上不可預測（同一份 CPI，
比預期高可以因「降息憧憬」上漲也可以因「通膨頑固」下跌——取決於市場當時的定位）。
所以宏觀事件層做的是預警、解讀、兜底，不預測事件結果，也不產生方向性買賣信號。

## 12. 即時捕捉層（2026-09-12 設計，待確認）——「事件發生當下手機就響」

用戶明確需求：ASAP 捕捉突發事件。三個組件，全部免費：

1. **指數震盪絆網（最快、最可靠的「有大事」探測器）**：
   - 監控 SPY / QQQ / VIX / ES 期貨（含隔夜），droplet 上每 2-3 分鐘掃一次
   - 觸發條件：SPY 或 QQQ 盤中瞬時 ±1%、VIX 瞬時 +10%、期貨隔夜 ±1%
   - 原理：不管是演講、開戰還是數據，價格永遠比新聞快——指數一動鈴就響，
     不需要知道原因。隔夜事件（中東式）由期貨絆網覆蓋。
2. **關鍵詞新聞絆網（告訴你「發生了什麼」）**：
   - droplet 每 1-2 分鐘輪詢免費新聞 RSS；本地關鍵詞過濾（Fed/FOMC/CPI/利率/
     伊朗/中東/關稅/Trump/襲擊/停電…可自定義）→ 命中瞬間推送標題
   - 只在命中後才叫 LLM 出一句話解讀（成本：每次幾美分）
3. **事件窗口模式（已知日程的精準覆蓋）**：
   - 經濟日曆裡的事件（CPI 8:30、FOMC 14:00 等）自動進入「1 分鐘掃描窗口」：
     公布前 15 分鐘起每分鐘掃描，公布後立即拉新聞 + LLM 快評 + 推送

**延遲預算（誠實版）**：價格絆網約 2-3 分鐘內響；新聞絆網約 1-3 分鐘（RSS 本身的
延遲）。彭博終端級的秒級速度要價每年數萬美元，不建議；而且對「人讀完警報→
打開 Webull→手動下單」的流程來說，1-3 分鐘不是瓶頸，人的反應才是。
Mac 本機 15 分鐘節奏不動；此層需要 droplet 部署後才能開（高頻輪詢不適合筆記本）。

**推送規格**：絆網警報 = 最高優先級推送（區別於一般 WARN），格式：
「⚡ 指數異動：SPY -1.2%（5 分鐘內）｜VIX +14%｜配對新聞：[標題]」

## 13. Telegram 交互模式（2026-09-12 設計，待確認）——隨問隨答

在 droplet 上加一個常駐小服務（`telegram_bot.py`，systemd `stocksignal-bot.service`），
用 Telegram long-polling 監聽消息，實現秒級互動：

**指令集（v1）**：
- 直接發 ticker（`AAPL` / `$CRWV`）→ 秒回該股完整分析：
  價格/漲跌、RSI/MACD/ATR 摘要、當前所有信號、財報倒數、
  Astra 消息面評分（若已設定 key）、Serenity 對該股的立場與最近提及（若層已上線）
- `/add 代碼`、`/del 代碼` → 手機上直接增刪監控清單（含 leveraged_inverse 標註格式）
- `/status` → 系統健康摘要（數據源、各層狀態、最近一次掃描時間）

**安全設計**：
- 只回應 `.env` 裡登記的 chat_id（= 只有機主本人），其他人發消息一律忽略
- ticker 必須能被數據源解析，帶頻率上限防轟炸
- 機器人只能讀行情 + 改監控清單，無任何交易/金鑰權限

**口徑**：回覆的是「信號報告」（指標 + 規則信號 + 消息面），不是買賣指令——
每次回覆尾行固定附「僅供參考，非投資建議」。

純出站連線（long-polling），無新增監聽端口；資源佔用可忽略（空閒時就是一個掛起的 HTTP 等待）。
工作量約半天，與部署一起上。
