"""Telegram 交互 bot：隨問隨答（DEPLOYMENT.md §13）。

- 發 ticker（如 CRWV / $CRWV）→ 秒回該股完整分析
- /add 代碼[: 屬性[: 底層]]  /del 代碼  → 手機上管理監控清單
- /list 監控清單  /status 系統健康
- 安全：只回應 .env 登記的 chat_id；只能讀行情 + 改 watchlist.txt，無任何交易權限
- 每次回覆尾行固定「僅供參考，非投資建議」
無 TELEGRAM_BOT_TOKEN 時直接退出並說明（systemd 會顯示 failed，這是預期）。
"""
import json
import re
import sys
import time
from pathlib import Path

import requests

import config
import data
import llm
import market_hours
import rules
import serenity

API = "https://api.telegram.org"
LEVEL_ICON = {"INFO": "•", "WARN": "▲", "STOP": "🛑"}
DISCLAIMER = "僅供參考，非投資建議"


def _tg(method: str, payload: dict) -> dict:
    resp = requests.post(f"{API}/bot{config.TELEGRAM_BOT_TOKEN}/{method}",
                         json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _reply(chat_id: int, text: str) -> None:
    _tg("sendMessage", {"chat_id": chat_id, "text": text[:4000]})


def analyze_ticker(sym: str, profile: str = "stock", underlying: str | None = None) -> str:
    """單標的完整分析（Telegram 查詢與未來複用）。"""
    try:
        d = data.fetch(sym)
    except Exception as exc:
        return f"✗ {sym}：{exc}"
    signals, summary = rules.evaluate(sym, d, profile)
    lines = [f"{sym}（${summary['price']}，日漲跌 {summary['daily_change_pct']:+.1f}%）",
             f"RSI {summary['rsi14']}｜MACD柱 {summary['macd_hist']:+.4f}"
             f"｜ATR {summary['atr_pct']:.1f}%｜量z {summary['volume_z']}"]
    for s in signals:
        lines.append(f"{LEVEL_ICON[s['level']]} [{s['dir']}] {s['text']}")
    if d.get("next_earnings"):
        lines.append(f"◇ 下次財報：{d['next_earnings']}")
    mentions = serenity.recent_mentions(underlying or sym, hours=24 * 60)
    if mentions:
        corpus = "\n".join(f"[{m['ts'][:10]}] {m['text'][:220]}" for m in mentions[:12])
        if llm.OPENAI_API_KEY:
            summ = llm.complete(
                "你是嚴謹的分析助理。只根據給定推文內容，總結分析師 Serenity 對該股票的觀點："
                "整體立場（偏多/偏空/中性/無明確立場）+ 最多 3 個核心論據 + 值得注意的具體數字或日期。"
                "輸出繁體中文、120 字內、條列式；內容不足以判斷就直說；不得加入原文沒有的觀點。",
                f"股票：{sym}\nSerenity 近 60 天提及 {underlying or sym} 的推文：\n{corpus}",
            )
            if summ:
                lines.append(f"◇ Serenity 觀點（近 60 天提及 {len(mentions)} 次，AI 摘要）：")
                lines.append("  " + summ.strip().replace("\n", "\n  "))
            else:
                lines.append(f"◇ Serenity 近 60 天提及 {len(mentions)} 次（摘要生成失敗，顯示最新一條）：")
                lines.append(f"  [{mentions[0]['ts'][:10]}] {mentions[0]['text'][:150]}")
        else:
            lines.append(f"◇ Serenity 近 60 天提及 {len(mentions)} 次（未設 OpenAI key，顯示原文片段）：")
            for m in mentions[:2]:
                lines.append(f"  [{m['ts'][:10]}] {m['text'][:150]}")
    if llm.OPENAI_API_KEY:
        opinion = llm.analyze(sym, underlying or sym, summary, d["news"],
                              inverse=(profile == "leveraged_inverse"))
        if opinion:
            lines.append(f"◇ 消息面（{config.OPENAI_MODEL}）：{opinion['stance']}（score {opinion['score']:+d}）"
                         f"——{opinion['reasons'][0] if opinion['reasons'] else ''}")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def _detail(sym: str) -> str:
    """V2 完整評估視圖（四 horizon + 五層 + 主因/主險）。"""
    import push_policy
    import scoring
    try:
        ev = scoring.evaluate_symbol(sym)
    except Exception as exc:
        return f"✗ {sym}：{exc}"
    lines = [f"🔬 {sym} 完整評估（{ev['generated_at'][:16]}）", ""]
    for hz in ("tomorrow", "week", "month", "year"):
        h = ev["horizons"][hz]
        lines.append(f"[{push_policy.HZ_LABEL[hz]}] {h['direction']} {h['confidence']}%"
                     f"（加權 {h['score']:+.2f}）")
        lines.append(f"  主因：{h['main_reason']}")
        lines.append(f"  主險：{h['main_risk']}")
    lines.append("")
    for key, lab in push_policy.LAYER_LABEL.items():
        l = ev["layers"][key]
        if l["status"] == "unavailable":
            lines.append(f"{lab}: UNAVAILABLE")
        else:
            reasons = "；".join(r[:40] for r in l["reasons"][:2])
            lines.append(f"{lab}: {l['score']:+.2f}（conf {l['confidence']}, {l['status']}）{reasons}")
    lines.append("")
    lines.append("失效條件：方向反轉或置信度跌破 50 時另行推送。僅供參考，非投資建議")
    return "\n".join(lines)


def handle_message(msg: dict) -> None:
    chat_id = msg["chat"]["id"]
    if chat_id != config.TELEGRAM_CHAT_ID_INT:
        return  # 非機主，一律無視
    import unicodedata
    text = unicodedata.normalize("NFKC", msg.get("text") or "").strip()  # 全角→半角（中文輸入法友善）
    if not text:
        return
    low = text.lower()

    if low.startswith("/start") or low.startswith("/help"):
        _reply(chat_id, "用法：\n直接發 ticker（如 CRWV）→ 完整分析\n"
                        "/detail 代碼 → V2 五層×四時間窗評估\n"
                        "/events 代碼 → 近期結構化事件\n"
                        "/serenity 代碼 → Serenity 提及情況\n"
                        "/refresh 代碼 → 立即重估並回報變化\n"
                        "/add /del /list /status → 清單與健康")
    elif low.startswith("/add"):
        entry = config.parse_watchlist_line(text[4:].strip())
        if not entry:
            _reply(chat_id, "格式：/add 代碼 或 /add 代碼 : leveraged_inverse : 底層")
            return
        wl = config.load_watchlist()
        if any(i["symbol"] == entry["symbol"] for i in wl):
            _reply(chat_id, f"{entry['symbol']} 已在監控清單")
            return
        with open(config.WATCHLIST_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n{entry['symbol']}" +
                    (f" : {entry['profile']}" if entry["profile"] != "stock" else "") +
                    (f" : {entry['underlying']}" if entry["underlying"] != entry["symbol"] else ""))
        _reply(chat_id, f"✓ 已加入監控：{entry['symbol']}"
                        f"（屬性 {entry['profile']}），下次掃描生效")
    elif low.startswith("/del"):
        sym = text[4:].strip().split()[0].upper() if text[4:].strip() else ""
        if not sym or not config.WATCHLIST_FILE.exists():
            _reply(chat_id, "格式：/del 代碼")
            return
        lines = config.WATCHLIST_FILE.read_text(encoding="utf-8").splitlines()
        kept = [l for l in lines if config.parse_watchlist_line(l)
                and config.parse_watchlist_line(l)["symbol"] != sym]
        removed = len(lines) - len(kept)
        config.WATCHLIST_FILE.write_text("\n".join(kept) + "\n", encoding="utf-8")
        _reply(chat_id, f"{'✓ 已移除 ' + sym if removed else '✗ 清單中沒有 ' + sym}")
    elif low.startswith("/detail"):
        sym = text[7:].strip().split()[0].upper() if text[7:].strip() else ""
        _reply(chat_id, _detail(sym) if sym else "格式：/detail 代碼")
    elif low.startswith("/events"):
        sym = text[7:].strip().split()[0].upper() if text[7:].strip() else ""
        if not sym:
            _reply(chat_id, "格式：/events 代碼")
            return
        try:
            import events
            evs = events.recent_events_for(sym, days=7)
            if not evs:
                _reply(chat_id, f"{sym}：近 7 天無結構化事件")
            else:
                body = "\n".join(f"• [{e['category']}] {e['headline'][:80]}"
                                 for e in evs[:8])
                _reply(chat_id, f"{sym} 近 7 天事件：\n{body}\n僅供參考，非投資建議")
        except Exception:
            _reply(chat_id, "事件引擎未啟用")
    elif low.startswith("/serenity"):
        sym = text[9:].strip().split()[0].upper() if text[9:].strip() else ""
        if not sym:
            _reply(chat_id, "格式：/serenity 代碼")
            return
        entry = next((i for i in config.load_watchlist() if i["symbol"] == sym), None)
        und = entry["underlying"] if entry else sym
        mentions = serenity.recent_mentions(und, hours=24 * 60)
        if not mentions:
            _reply(chat_id, f"Serenity 近 60 天未提及 {und}（available，無相關論點）")
        else:
            _reply(chat_id, f"{und} 近 60 天提及 {len(mentions)} 次（最近 {mentions[0]['ts'][:10]}），"
                            "完整觀點請直接發 ticker 查詢")
    elif low.startswith("/refresh"):
        sym = text[8:].strip().split()[0].upper() if text[8:].strip() else ""
        if not sym:
            _reply(chat_id, "格式：/refresh 代碼")
            return
        import push_policy
        import scoring
        try:
            ev = scoring.evaluate_symbol(sym)
        except Exception as exc:
            _reply(chat_id, f"✗ {sym}：{exc}")
            return
        d = push_policy.decide(sym, ev, critical_event=None)
        _reply(chat_id, push_policy.format_compact(ev, d["changes"]))
    elif low.startswith("/list"):
        wl = config.load_watchlist()
        _reply(chat_id, "監控清單：\n" + "\n".join(
            f"• {i['symbol']}" + (f"（{i['profile']}，消息面看 {i['underlying']}）"
                                  if i["profile"] != "stock" else "") for i in wl))
    elif low.startswith("/status"):
        hb = {}
        p = Path(__file__).parent / ".daemon_heartbeat.json"
        if p.exists():
            try:
                hb = json.loads(p.read_text())
            except Exception:
                pass
        _reply(chat_id, f"美股時段：{market_hours.status()}\n"
                        f"Serenity：{serenity.health()}\n"
                        f"賬本：{__import__('ledger').system_status()}\n"
                        f"最近掃描：{hb.get('watch_scan', '—')}\n"
                        f"最近絆網：{hb.get('tripwire', '—')}")
    else:
        m = re.match(r"^\$?([A-Za-z][A-Za-z0-9.=^:-]{0,11})$", text)
        if m:
            sym = m.group(1).upper()
            entry = next((i for i in config.load_watchlist() if i["symbol"] == sym), None)
            _reply(chat_id, analyze_ticker(sym,
                                           profile=entry["profile"] if entry else "stock",
                                           underlying=entry["underlying"] if entry else None))
        else:
            _reply(chat_id, "看不懂指令。發 ticker 查詢，或 /help 看用法")


def main() -> int:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID（.env），bot 不啟動——其餘功能不受影響")
        return 1
    if not hasattr(config, "TELEGRAM_CHAT_ID_INT"):
        try:
            config.TELEGRAM_CHAT_ID_INT = int(config.TELEGRAM_CHAT_ID)
        except ValueError:
            print("TELEGRAM_CHAT_ID 必須是數字")
            return 1
    print("Telegram bot 啟動（long-polling）")
    offset = 0
    while True:
        try:
            updates = _tg("getUpdates", {"timeout": 50, "offset": offset})
            for u in updates.get("result", []):
                offset = u["update_id"] + 1
                if "message" in u:
                    handle_message(u["message"])
        except requests.RequestException as exc:
            print(f"polling 異常，10 秒後重試：{exc}")
            time.sleep(10)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())
