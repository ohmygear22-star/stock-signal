"""主程式。
  python main.py --mode auto             ← 給定時任務用：自動判斷盤中掃描/收盤日報/休市退出
  python main.py --mode daily            手動：完整分析（指標 + 規則 + LLM 消息面）
  python main.py --mode intraday         手動：盤中快速掃描
  python main.py --check                 自檢：資料源 + 清單 + 時段狀態
本系統唯讀、不連任何券商帳戶、不依賴任何 AI 也能完整運行；下單一律由你在 Webull 手動確認。"""
import argparse
import sys
from datetime import datetime, timezone

import config
import data
import llm
import market_hours
import notify
import rules
import yfinance as yf

LEVEL_ICON = {"INFO": "•", "WARN": "▲", "STOP": "🛑"}


def main() -> int:
    parser = argparse.ArgumentParser(description="股票買賣時機信號系統（唯讀，不自動交易）")
    parser.add_argument("--mode", choices=["auto", "daily", "intraday"], default="auto")
    parser.add_argument("--watchlist", help="臨時覆蓋監控清單，逗號分隔")
    parser.add_argument("--force-status", choices=["open", "after_close", "closed"],
                        help="僅供測試：覆蓋美股時段判斷")
    args = parser.parse_args()

    if args.mode == "auto":
        return run_auto(args.force_status)

    watchlist = _watchlist(args)
    mode_label = "收盤完整分析" if args.mode == "daily" else "盤中掃描"
    return run_report(watchlist, args.mode, mode_label)


def run_auto(forced: str | None) -> int:
    status = forced or market_hours.status()
    if status == "closed":
        print(f"[{datetime.now():%H:%M}] 美股休市，快速退出（零網路請求）")
        return 0
    if status == "open":
        watchlist = _watchlist(None)
        stale, ok = [], []
        for item in watchlist:
            try:
                d = data.fetch(item["symbol"])
            except Exception:
                continue
            if _intraday_stale(d):
                stale.append(item["symbol"])
            else:
                ok.append(item)
        # 假日偵測：全部標的的盤中資料都停在 30 分鐘以前 = 市場沒開（美國假日）
        if watchlist and not ok:
            print("美股今日休市（資料未更新），本次跳過")
            return 0
        return run_report(ok or watchlist, "intraday", "盤中掃描（自動）")
    # after_close：每個交易日只出一次日報
    if config.DAILY_STATE_FILE.exists() and \
            config.DAILY_STATE_FILE.read_text().strip() == market_hours.today_et():
        print("今日收盤日報已產出，不重複")
        return 0
    code = run_report(_watchlist(None), "daily", "收盤完整分析（自動）")
    if code == 0:
        config.DAILY_STATE_FILE.write_text(market_hours.today_et())
    return code


def run_report(watchlist: list[dict], mode: str, mode_label: str) -> int:
    report = [f"模式：{mode_label}｜監控：{', '.join(i['symbol'] for i in watchlist)}"]
    exit_code = 0
    stop_worthy = False

    for item in watchlist:
        sym = item["symbol"]
        try:
            d = data.fetch(sym)
        except Exception as exc:
            report.append(f"\n=== {sym} ===\n▲ 資料取得失敗：{exc}")
            exit_code = 1
            continue

        signals, summary = rules.evaluate(sym, d, item["profile"])
        stop_worthy |= any(s["level"] == "STOP" for s in signals)
        try:
            import ledger
            import macro
            ledger.update_active(sym, [s for s in signals if s["rule_id"] != "none"],
                                 price=summary["price"], event_day=macro.is_event_day())
        except Exception:
            pass

        lines = [f"\n=== {sym}（${summary['price']}，日漲跌 {summary['daily_change_pct']:+.1f}%）==="]
        lines.append(
            f"RSI {summary['rsi14']}｜MACD柱 {summary['macd_hist']:+.4f}｜ATR {summary['atr_pct']:.1f}%｜量z {summary['volume_z']}"
        )
        for s in signals:
            lines.append(f"{LEVEL_ICON[s['level']]} [{s['dir']}] {s['text']}")

        if mode == "daily":
            try:
                import serenity as serenity_mod
                mentions = serenity_mod.recent_mentions(item["underlying"], hours=72)
                if mentions:
                    lines.append(f"◇ Serenity 近 72h 提及 {item['underlying']} {len(mentions)} 次（最近 {mentions[0]['ts'][:10]}）")
            except Exception:
                pass
            inverse = item["profile"] == "leveraged_inverse"
            opinion = llm.analyze(sym, item["underlying"], summary, d["news"], inverse=inverse)
            if opinion is None:
                if not config.OPENAI_API_KEY:
                    lines.append("◇ 消息面：未設定 OPENAI_API_KEY，已跳過（key 請放 .env，不要貼在對話裡）")
            else:
                lines.append(f"◇ 消息面（{config.OPENAI_MODEL} 參考）：{opinion['stance']}（score {opinion['score']:+d}）")
                for r in opinion["reasons"][:3]:
                    lines.append(f"   - {r}")

        report.extend(lines)

    if mode == "daily":
        try:
            import macro
            report.extend([""] + macro.daily_section())
        except Exception:
            pass

    # V2-first 路由（2026-09-14 通知清理）：盤中僅 STOP 級（槓桿止損）直推；
    # WARN 級技術信號與日報只寫日誌與賬本——它們照常進入 Performance 層與 push_policy
    if mode == "intraday" and stop_worthy:
        notify.send("🛑 止損警報", "\n".join(report))
    else:
        print("\n".join(report))
    return exit_code


def run_check() -> int:
    print(f"美股時段狀態：{market_hours.status()}")
    ok = True
    for item in _watchlist(None):
        sym = item["symbol"]
        try:
            hist = yf.Ticker(sym).history(period="5d", interval="1d")
            if hist.empty:
                raise RuntimeError("日 K 為空")
            last = hist.index[-1].date()
            print(f"✓ {sym:<8} 資料正常（最近交易日 {last}，屬性 {item['profile']}）")
        except Exception as exc:
            ok = False
            print(f"✗ {sym:<8} 資料異常：{exc}")
    print(f"LLM 消息面層：{'已啟用（' + config.OPENAI_MODEL + '）' if config.OPENAI_API_KEY else '未啟用（無 key，機械信號不受影響）'}")
    print(f"Telegram 推送：{'已啟用' if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID else '未啟用（信號寫入 signals.log）'}")
    return 0 if ok else 1


def _watchlist(args) -> list[dict]:
    if args and args.watchlist:
        return [{"symbol": s.strip().upper(), "profile": "stock", "underlying": s.strip().upper()}
                for s in args.watchlist.split(",") if s.strip()]
    return config.load_watchlist()


def _intraday_stale(d: dict, minutes: int = 30) -> bool:
    intraday = d["intraday"]
    if intraday.empty:
        return True
    last = intraday.index[-1]
    last_utc = last.tz_convert(timezone.utc) if last.tzinfo else last.tz_localize(timezone.utc)
    age = (datetime.now(timezone.utc) - last_utc.to_pydatetime()).total_seconds() / 60
    return age > minutes


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(run_check())
    sys.exit(main())
