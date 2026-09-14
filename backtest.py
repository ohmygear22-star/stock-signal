"""事件式回測：回答「這條規則歷史上觸發後，股價按信號方向走的統計優勢有多少」。

方法（與 DEPLOYMENT.md 設計一致）：
- 事件 = 規則首次成立的瞬間（entry 語義），逐日回放 5 年
- 每次事件的事後成績 = +1/+3/+5 日收盤方向化收益 D = 方向 × (P[i+h]/P[i] − 1)
- 優勢 = 事件平均 D − 同期全體基準漂移（多規則比 +漂移，空規則比 −漂移）
- 防 過擬合：前 60% 樣本 = 訓練段、後 40% = 驗證段，分開報告
- 敏感性：RSI 30/70 之外另測 25/75、20/80（只有魔法數字才靈 = 噪音）

用法：.venv/bin/python backtest.py [--years 5] [--tickers AAPL,NVDA]
默認 = watchlist.txt 全部 + SPY + QQQ（基準對照組，防倖存者偏差）。
輸出：backtest_report.md + 螢幕摘要。
注意：不含交易成本；命中率≠可交易利潤；樣本 < 20 只判定「樣本不足」。"""
import argparse
from datetime import datetime

import numpy as np
import yfinance as yf

import config
import rules

HORIZONS = [1, 3, 5]
TRAIN_FRAC = 0.6
MIN_N = 20
SENSITIVITY = [(25.0, 75.0), (20.0, 80.0)]  # 主跑 30/70，另測這兩組
BASELINES = ["SPY", "QQQ"]


def fetch_daily(sym: str, years: int):
    df = yf.Ticker(sym).history(period=f"{years}y", interval="1d")
    if df.empty or len(df) < 80:
        raise RuntimeError(f"{sym}: 日 K 不足（{len(df)} 根）")
    return df


def scan_symbol(sym: str, df, rsi_lo=30.0, rsi_hi=70.0, extra_sens=True):
    """回放全歷史，回傳 (事件列表, 基準漂移 dict: 各 horizon 全體平均收益)。"""
    ctx = rules.build_ctx(df)
    close = df["Close"]
    n = len(close)
    events = []
    drift = {h: float(np.mean([close.iloc[i + h] / close.iloc[i] - 1
                               for i in range(60, n - max(HORIZONS))])) for h in HORIZONS}

    def _collect(lo, hi, keep_non_rsi):
        for i in range(60, n - max(HORIZONS)):
            for s in rules.daily_rules_at(ctx, i, entry_only=True, rsi_lo=lo, rsi_hi=hi):
                rid = s["rule_id"]
                is_rsi = rid.startswith("rsi_oversold") or rid.startswith("rsi_overbought")
                if not keep_non_rsi and not is_rsi:
                    continue  # 敏感性跑只看 RSI 規則
                sign = 1 if s["dir"] == "多" else -1
                rec = {"rule_id": rid, "symbol": sym, "i": i,
                       "date": df.index[i].date(), "sign": sign, "D": {}}
                for h in HORIZONS:
                    rec["D"][h] = sign * (float(close.iloc[i + h]) / float(close.iloc[i]) - 1)
                events.append(rec)

    _collect(rsi_lo, rsi_hi, keep_non_rsi=True)
    if extra_sens:
        for lo, hi in SENSITIVITY:
            _collect(lo, hi, keep_non_rsi=False)
    return events, drift


def analyze(events: list[dict], drift: float, horizon: int = 3) -> dict:
    """按 rule_id 聚合：命中率、平均 D、優勢（扣基準）、訓練/驗證分段。"""
    by_rule = {}
    for e in events:
        by_rule.setdefault(e["rule_id"], []).append(e)
    out = {}
    for rid, evs in by_rule.items():
        evs_sorted = sorted(evs, key=lambda x: x["date"])
        n = len(evs_sorted)
        ds = np.array([e["D"][horizon] for e in evs_sorted])
        split = int(n * TRAIN_FRAC)
        tr, te = ds[:split], ds[split:]
        sign = evs_sorted[0]["sign"]
        out[rid] = {
            "n": n,
            "hit": float((ds > 0).mean()),
            "mean_d": float(ds.mean()),
            "edge": float(ds.mean()) - (drift if sign > 0 else -drift),
            "train_edge": float(tr.mean()) if len(tr) else None,
            "test_edge": float(te.mean()) if len(te) else None,
            "test_n": len(te),
            "sign": sign,
        }
    return out


def verdict(stats: dict) -> str:
    if stats["n"] < MIN_N:
        return "樣本不足"
    if stats["test_n"] >= 8 and stats["test_edge"] is not None and stats["test_edge"] > 0:
        return "通過驗證"
    if stats["test_n"] < 8:
        return "驗證段樣本少"
    return "驗證未過"


def run(years: int, tickers: list[str]) -> str:
    symbols = tickers or ([i["symbol"] for i in config.load_watchlist()] + BASELINES)
    all_events, per_symbol, drifts, bar_counts = [], {}, {}, {}
    for sym in symbols:
        try:
            df = fetch_daily(sym, years)
            evs, drift = scan_symbol(sym, df)
            all_events.extend(evs)
            drifts[sym] = drift
            bar_counts[sym] = len(df)
            per_symbol[sym] = len(evs)
        except Exception as exc:
            per_symbol[sym] = f"✗ {exc}"

    # 基準漂移 = 各標的 +3 日漂移按 K 棒數加權平均（多頭基準；空規則比 −漂移）
    if drifts:
        drift3 = float(np.average([drifts[s][3] for s in drifts],
                                  weights=[bar_counts[s] for s in drifts]))
    else:
        drift3 = 0.0
    stats = analyze(all_events, drift=float(drift3), horizon=3)
    lines = [f"# 回測報告（生成 {datetime.now():%Y-%m-%d %H:%M}）", ""]
    lines.append(f"標的：{'、'.join(symbols)}｜週期：{years} 年｜口徑：事件觸發後 +3 日方向化收益")
    lines.append(f"全體基準漂移（+3日）：{drift3:+.2%}——多規則的「優勢」已扣它，空規則扣它的反向")
    lines.append("（不含成本；命中率≠利潤；驗證段=後 40% 樣本）")
    lines.append("")
    lines.append("| 規則 | 次數 | 命中率 | 平均+3日 | 優勢(扣基準) | 訓練段 | 驗證段(次數) | 判定 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for rid, st in sorted(stats.items(), key=lambda kv: -kv[1]["n"]):
        train = f"{st['train_edge']:+.2%}" if st["train_edge"] is not None else "—"
        test = f"{st['test_edge']:+.2%} ({st['test_n']})" if st["test_edge"] is not None else "—"
        lines.append(f"| {rid} | {st['n']} | {st['hit']:.0%} | {st['mean_d']:+.2%} | "
                     f"{st['edge']:+.2%} | {train} | {test} | {verdict(st)} |")
    lines.append("")
    lines.append("## 各標的事件數")
    lines.append(", ".join(f"{s}={n}" for s, n in per_symbol.items()))
    lines.append("")
    lines.append("## 讀法")
    lines.append("- 「通過驗證」= 驗證段均值仍為正——門檻可繼續用，但仍需實盤記分卡複核")
    lines.append("- 「驗證未過」= 訓練段好看、驗證段失效 = 過擬合，規則降級為參考")
    lines.append("- 「樣本不足」= 5 年內觸發太少，統計上無結論，維持現狀靠實盤累積")
    lines.append("- AAPL/NVDA 等今日贏家存在倖存者偏差，SPY/QQQ 行是對照組")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--tickers", default="")
    args = parser.parse_args()
    report = run(args.years, [t.strip().upper() for t in args.tickers.split(",") if t.strip()])
    with open("backtest_report.md", "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
