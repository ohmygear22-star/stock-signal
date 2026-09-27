"""Alpha 因子研究驗證器：用我們自己的數據裁決移植因子（研究版，不入生產）。

復用 backtest.py 的 analyze/verdict/drift 邏輯（同一套 train/validation、
扣基準漂移、最小樣本紀律），事件語義 = 條件首次成立（entry），與規則回測一致。
用法：.venv/bin/python alpha_research.py [--years 5] [--tickers ...]
輸出：alpha_factors_report.md。
"""
import argparse
from datetime import datetime

import numpy as np

import alpha_factors
from backtest import BASELINES, HORIZONS, analyze, fetch_daily, verdict
from config import load_learning_universe, load_watchlist


def scan_factors(sym: str, df) -> tuple[list[dict], dict]:
    events = []
    close = df["Close"]
    n = len(close)
    drift = {h: float(np.mean([close.iloc[i + h] / close.iloc[i] - 1
                               for i in range(alpha_factors.WARMUP, n - 1) if i + h < n]))
             for h in HORIZONS}
    for fid, fn in alpha_factors.FACTORS.items():
        try:
            bull, bear = fn(df)
        except Exception:
            continue
        for tag, state, sign in ((fid, bull, 1), (fid.replace("alpha_", "antialpha_"), bear, -1)):
            prev = False
            for i in range(alpha_factors.WARMUP, n - 1):
                cur = bool(state.iloc[i])
                if cur and not prev:  # entry 語義
                    rec = {"rule_id": tag, "symbol": sym, "i": i,
                           "date": df.index[i].date(), "sign": sign, "D": {}}
                    for h in HORIZONS:
                        if i + h < n:
                            rec["D"][h] = sign * (float(close.iloc[i + h]) / float(close.iloc[i]) - 1)
                    events.append(rec)
                prev = cur
    return events, drift


def run(years: int, tickers: list[str]) -> str:
    if not tickers:
        seen = []
        for i in load_watchlist() + load_learning_universe():
            if i["symbol"] not in seen:
                seen.append(i["symbol"])
        tickers = seen + [b for b in BASELINES if b not in seen]
    all_events, drifts, bar_counts = [], {}, {}
    for sym in tickers:
        try:
            df = fetch_daily(sym, years)
            evs, drift = scan_factors(sym, df)
            all_events.extend(evs)
            drifts[sym] = drift
            bar_counts[sym] = len(df)
        except Exception as exc:
            print(f"✗ {sym}: {exc}")

    drift_by_h = {}
    for h in HORIZONS:
        drift_by_h[h] = (float(np.average([drifts[s][h] for s in drifts],
                                          weights=[bar_counts[s] for s in drifts]))
                         if drifts else 0.0)

    lines = [f"# Alpha 因子驗證報告（{datetime.now():%Y-%m-%d %H:%M}）", "",
             f"標的：{len(tickers)} 只｜週期：{years} 年｜來源：Qlib Alpha158 精選移植（研究版）",
             "假設全部事先聲明（見 alpha_factors.py 註釋）；判定標準與規則回測完全一致。",
             "（不含成本；驗證段=後 40% 樣本；antialpha_* = 反向觸發的對照事件）"]
    for h in (1, 5, 21, 252):
        stats = analyze(all_events, drift=drift_by_h[h], horizon=h)
        lines += ["", f"## +{h} 交易日（基準漂移 {drift_by_h[h]:+.2%}）", "",
                  "| 因子 | 次數 | 命中率 | 平均收益 | 優勢(扣基準) | 訓練段 | 驗證段(次數) | 判定 |",
                  "|---|---|---|---|---|---|---|---|"]
        for rid, st in sorted(stats.items(), key=lambda kv: -kv[1]["n"]):
            train = f"{st['train_edge']:+.2%}" if st["train_edge"] is not None else "—"
            test = f"{st['test_edge']:+.2%} ({st['test_n']})" if st["test_edge"] is not None else "—"
            lines.append(f"| {rid} | {st['n']} | {st['hit']:.0%} | {st['mean_d']:+.2%} | "
                         f"{st['edge']:+.2%} | {train} | {test} | {verdict(st)} |")
    lines += ["", "## 讀法",
              "- 「通過驗證」= 該因子在我們自己的數據上、驗證段仍為正——僅獲得 Phase 8 旁聽資格",
              "- 任何入選 Layer 2 都必須等 Phase 8 權重治理流程 + owner 批准",
              "- 樣本不足因子不補測（第一批假設池固定，避免 data snooping）"]
    return "\n".join(lines)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--years", type=int, default=5)
    p.add_argument("--tickers", default="")
    a = p.parse_args()
    report = run(a.years, [t.strip().upper() for t in a.tickers.split(",") if t.strip()])
    with open("alpha_factors_report.md", "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
