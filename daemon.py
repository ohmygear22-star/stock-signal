"""統一調度守護進程（droplet 上由 systemd 託管，Restart=always）。

任務與節奏：
- tripwire 每 2 分鐘（經濟日曆事件窗口內提頻到 1 分鐘）：指數/VIX/期貨震盪
- rss 關鍵詞絆網每 2 分鐘
- watch_scan 每 15 分鐘（僅美股開盤時段；只推 WARN/STOP，見 main.run_report）
- serenity 歸檔每 60 分鐘：新貼比對監控清單
- daily_report 收盤後窗口內一次（去重）
- scoring 收盤日報後半小時：賬本結算
- scorecard 每週日 09:00（美東）：週記分卡推送
心跳寫 .daemon_heartbeat.json（bot /status 讀）。所有任務異常都只記日誌、不退出。
--once 參數 = 每個任務各跑一輪（測試用）。"""
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import config
import ledger
import macro
import market_hours
import notify
import serenity
import tripwire
import yfinance as yf

HEARTBEAT = Path(__file__).parent / ".daemon_heartbeat.json"
TICK = 20  # 主循環秒數

DURATIONS = {"tripwire": 120, "rss": 120, "watch_scan": 900,
             "serenity": 3600, "scoring": 1800}


def do_scoring_push() -> None:
    """V2：開盤時段每 30 分鐘 + 收盤後，逐標的五層評估 → 按推送策略決定是否通知。"""
    if market_hours.status() not in ("open", "after_close"):
        return
    import data as data_mod
    import market_data
    import push_policy
    import scoring
    snap = market_data.get_market_snapshot()
    for item in config.load_watchlist():
        sym = item["symbol"]
        try:
            ev = scoring.evaluate_symbol(sym, data=data_mod.fetch(sym), market_snap=snap)
        except Exception as exc:
            log(f"scoring {sym} 失敗：{exc}")
            continue
        d = push_policy.decide(sym, ev)
        if d["push"]:
            body = (push_policy.format_critical(sym, ev, d["changes"]) if d["critical"]
                    else push_policy.format_compact(ev, d["changes"]))
            notify.send("🚨 訊號更新", body)
            log(f"推送 {sym}：{'; '.join(d['changes'][:2])}")
        try:
            import ledger
            if ledger.record_prediction(ev) > 0:
                _hb("ledger_scoring")  # 預測快照寫入（EOD 權威版去重後）
        except Exception:
            pass
    _hb("scoring_push")


def log(text: str) -> None:
    print(f"[{datetime.now():%m-%d %H:%M:%S}] {text}", flush=True)


def _hb(task: str) -> None:
    hb = json.loads(HEARTBEAT.read_text()) if HEARTBEAT.exists() else {}
    hb[task] = datetime.now().strftime("%Y-%m-%d %H:%M")
    HEARTBEAT.write_text(json.dumps(hb))


def do_tripwire() -> None:
    for t in tripwire.check():
        ledger.record_event("SPY" if "spy" in t["key"] else "MARKET",
                            f"tripwire_{t['key']}", "WARN",
                            "空" if "-" in t["message"] else "多",
                            price=0.0, extra={"note": t["message"]},
                            dedup_key=t["key"] + datetime.now().strftime("%Y%m%d%H"))
        if t.get("critical"):
            notify.send("🚨 市場劇烈異動", f"⚡ {t['message']}")
            log(f"絆網 CRITICAL：{t['message']}")
        else:
            log(f"絆網觸發（僅記錄，不打擾）：{t['message']}")
    _hb("tripwire")


def do_rss() -> None:
    """只抓取入庫（供日報宏觀面分類用）——機主已關閉關鍵詞新聞的單獨推送（2026-09-14）。"""
    items, hits = macro.fetch_rss()
    if hits:
        log(f"關鍵詞命中 {len(hits)} 條（僅入庫，不推送）")
    _hb("rss")


def _serenity_alert_text(h: dict) -> str:
    import llm
    head = f"{', '.join(h['symbols'])}：\n"
    tc = llm.translate_tc(h["text"][:1200]) if llm.OPENAI_API_KEY else None
    if tc:
        return (head + f"【繁中翻譯】\n{tc.strip()}\n\n— 原文節錄：{h['text'][:160]}…\n（{h['ts'][:16]}）")
    return (head + "【原文】（API 餘額不足暫無翻譯，充值後自動恢復繁中摘要）\n"
            + h["text"][:350] + f"\n（{h['ts'][:16]}）")


def do_watch_scan() -> None:
    import main
    if market_hours.status() != "open":
        return
    main.run_report(config.load_watchlist(), "intraday", "盤中掃描（自動）")
    _hb("watch_scan")


def do_serenity() -> None:
    fresh, msg = serenity.fetch_new()
    x_result = serenity.fetch_new_x()
    if x_result:
        fresh = (fresh or []) + x_result[0]
        msg += "；" + x_result[1]
    if fresh:
        hits = serenity.match_watchlist(fresh, [i["symbol"] for i in config.load_watchlist()])
        for h in hits:
            records, meaningful = serenity.track_mention(h)  # V2：論點跟蹤（Telegram 統一走 V2 push_policy）
            for rec in meaningful:
                ledger.record_event(rec["ticker"], f"serenity_{rec['classification']}",
                                    "WARN", "中性" if not rec.get("stance") else
                                    ("多" if rec["stance"] > 0 else "空"),
                                    price=0.0,
                                    extra={"note": rec["text"][:120],
                                           "post_id": rec["post_id"]},
                                    dedup_key=f"ser-{rec['post_id']}-{rec['ticker']}")
        log(f"Serenity {msg}，命中監控 {len(hits)} 條")
    _hb("serenity")


def do_daily_report() -> None:
    """EOD 摘要（V2 格式）：四時間窗一覽 + 主險 + 宏觀面，取代 legacy 日報的 Telegram 直推。
    同時把 EOD 權威預測快照落庫（每 ticker 每交易日一條）。"""
    status = market_hours.status()
    today = market_hours.today_et()
    from datetime import time as _time
    if status != "after_close" or market_hours.datetime_et().time() < _time(16, 30):
        return  # 盤後 +30 分鐘（owner 2026-09-15 指令）
    if config.DAILY_STATE_FILE.exists() and \
            config.DAILY_STATE_FILE.read_text().strip() == today:
        return
    import data as data_mod
    import market_data
    import push_policy
    import scoring
    snap = market_data.get_market_snapshot()
    lines = ["📊 EOD 摘要（V2 四時間窗）", ""]
    for item in config.load_watchlist():
        sym = item["symbol"]
        try:
            ev = scoring.evaluate_symbol(sym, data=data_mod.fetch(sym), market_snap=snap)
            ledger.record_prediction(ev)
        except Exception as exc:
            lines.append(f"{sym}：評估失敗 {exc}")
            continue
        hzs = []
        for hz in ("tomorrow", "week", "month", "year"):
            h = ev["horizons"][hz]
            hzs.append(f"{push_policy.HZ_LABEL[hz]} {push_policy.DIR_ICON[h['direction']]} {h['confidence']}%")
        price = f"${ev.get('price'):.2f}" if ev.get("price") else "—"
        lines.append(f"{sym}（{price}）：" + "｜".join(hzs))
        lines.append(f"  主險：{ev['horizons']['tomorrow']['main_risk'][:70]}")
    try:
        import macro
        lines += [""] + macro.daily_section()
    except Exception:
        pass
    lines += ["", "僅供參考，非投資建議"]
    notify.send("📊 EOD 摘要（V2）", "\n".join(lines))
    config.DAILY_STATE_FILE.write_text(today)
    _hb("daily_report")


def do_scheduled_digest() -> None:
    """每日三段定時「今日展望」摘要（owner 2026-09-15 指令）：
    盤前+30 = 04:30 ET（16:30 HKT）、盤中+30 = 10:00 ET（21:30 HKT）。
    盤後+30 由 do_daily_report（EOD 摘要）承擔。錨定美東時段，冬令自動順延。"""
    from datetime import time as _time
    now = market_hours.datetime_et()
    if now.weekday() >= 5:
        return
    t = now.time()
    slot = None
    if t >= _time(4, 30) and not _done_today("digest_premarket"):
        slot = ("digest_premarket", "盤前")
    elif t >= _time(10, 0) and t < _time(16, 0) and not _done_today("digest_intraday"):
        slot = ("digest_intraday", "盤中")
    if not slot:
        return
    key, label = slot
    import data as data_mod
    import market_data
    import push_policy
    import scoring
    snap = market_data.get_market_snapshot()
    evals = []
    for item in config.load_watchlist():
        try:
            evals.append(scoring.evaluate_symbol(item["symbol"],
                                                 data=data_mod.fetch(item["symbol"]),
                                                 market_snap=snap))
        except Exception as exc:
            log(f"摘要 {item['symbol']} 評估失敗：{exc}")
    if evals:
        notify.send("📋 今日展望", push_policy.format_today_digest(evals, label))
    _hb(key)


def do_learning_eod() -> None:
    """學習宇宙（learning-only）：每日收盤窗口評估一次並落 EOD 預測快照。
    不進 push_policy、不發 Telegram、與 watchlist 行為完全隔離。"""
    if market_hours.status() != "after_close" or _done_today("learning_eod"):
        return
    import data as data_mod
    import market_data
    import scoring
    universe = config.load_learning_universe()
    if not universe:
        return
    snap = market_data.get_market_snapshot()
    watch_syms = {i["symbol"] for i in config.load_watchlist()}
    todo = [i for i in universe if i["symbol"] not in watch_syms]
    skipped = len(universe) - len(todo)
    ok = 0
    for item in todo:
        try:
            ev = scoring.evaluate_symbol(item["symbol"],
                                         data=data_mod.fetch(item["symbol"]),
                                         market_snap=snap)
            ledger.record_prediction(ev)
            ok += 1
        except Exception as exc:
            log(f"學習宇宙 {item['symbol']} 評估失敗：{exc}")
    log(f"學習宇宙 EOD 快照：{ok}/{len(todo)} 完成"
        + (f"（{skipped} 隻已在 watchlist，跳過）" if skipped else ""))
    _hb("learning_eod")


def do_scoring() -> None:
    now = market_hours.datetime_et()
    # 收盤日報後半小時以後才跑（且每天一次）
    if now.time().hour != 17 or _done_today("ledger_settlement"):
        return
    n = ledger.score_pending(lambda s: yf.Ticker(s).history(period="1mo", interval="1d"))
    log(f"賬本結算 {n} 條")
    _hb("ledger_settlement")


def do_scorecard() -> None:
    now = market_hours.datetime_et()
    if now.weekday() != 6 or now.time().hour != 9 or _done_today("scorecard"):
        return
    notify.send("📊 每週記分卡", ledger.scorecard(7))
    _hb("scorecard")


def _done_today(task: str) -> bool:
    hb = json.loads(HEARTBEAT.read_text()) if HEARTBEAT.exists() else {}
    return hb.get(task, "")[:10] == market_hours.today_et()


def main() -> int:
    once = "--once" in sys.argv
    due = {k: 0.0 for k in DURATIONS}
    log(f"daemon 啟動（once={once}）")
    while True:
        try:
            now = time.time()
            # 經濟日曆事件窗口內，絆網提頻到每分鐘
            tw_interval = 60 if macro.in_event_window() else DURATIONS["tripwire"]
            if now >= due["tripwire"]:
                do_tripwire()
                due["tripwire"] = time.time() + tw_interval
            if now >= due["rss"]:
                do_rss()
                due["rss"] = time.time() + DURATIONS["rss"]
            if now >= due["watch_scan"]:
                do_watch_scan()
                due["watch_scan"] = time.time() + DURATIONS["watch_scan"]
            if now >= due["serenity"]:
                do_serenity()
                due["serenity"] = time.time() + DURATIONS["serenity"]
            if now >= due["scoring"]:
                do_scoring_push()
                due["scoring"] = time.time() + DURATIONS["scoring"]
            do_daily_report()
            do_scheduled_digest()
            do_learning_eod()
            do_scoring()
            do_scorecard()
        except Exception as exc:
            log(f"循環異常（不退出）：{exc}")
        if once:
            return 0
        time.sleep(TICK)


if __name__ == "__main__":
    sys.exit(main())
