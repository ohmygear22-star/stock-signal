# Stock-Signal V2 — Full Continuation Rules（权威版 2026-09-15）

> 生产基线：`55d6567`。观察宇宙 = 警报 watchlist 6 只（全链路）+ 学习宇宙 15 只（仅 EOD 快照）
> = 21 只 × 4 horizon = 84 条/交易日权威快照。
> 状态：P0 ✅ P1 ✅ 全部核心层 LIVE；Phase 8 冻结 / Phase 9 待做 / Phase 10 待做。
> **GLM/ZCode 不自动等待、不自动唤醒——每个 Gate 由 owner 手动触发（"Proceed with Gate A/B/C1"）。时间流逝 ≠ 批准。**

## 观察期（Sep 15 / 16 / 17 / 18 / 21 五个完整交易日）

9/14 部署日不算完整观察日。期间：只采集、零调权、不激活 weight_tuner。
Telegram 照常运作（学习宇宙永远静默）；预测采集与 Telegram 是否发消息无关。

## Gate A — 2026-09-22 09:00 HKT（= 9/21 21:00 ET，Sep 21 收盘后）

一次轻量观察质量检查（13 项）：daemon/bot 健康、predictions_ledger 进数据、
watchlist + 学习宇宙都有 EOD 快照、约 21 只覆盖、每只含四 horizon、
每条含 direction/confidence/weighted/price/generated_at/ET 交易日、
五层值与状态齐全（options=unavailable 如实存）、ticker/horizon/day 去重生效、
学习宇宙零 Telegram、四个心跳键（scoring_push / ledger_scoring / ledger_settlement / learning_eod）健康。
健康 → 什么都不做继续采集。数据采集缺陷 → 只修该缺陷。Gate A 不是改评分的许可。

## Gate B — 2026-09-25 09:00 HKT（Phase 9 历史 multi-horizon 回测）

扩展现有回测（不重写）至 1/5/21/252 交易日（数据不足跳过长档）。
保留 train/validation 切分、基准漂移、最小样本、规则验证、反过拟合。
按 horizon 报告：样本量/命中率/平均方向化收益/扣基准优势/训练段/验证段/通过与否。
目的是学：哪些规则对 Tomorrow/Week/Month 有用、哪些有 Year 证据、哪些应保持降权。
**Phase 9 不得直接改生产权重；完成 Phase 9 ≠ 恢复 Phase 8。** 只跑一次缩减验证，交报告即停。

## Gate C1 — 2026-10-13 09:00 HKT（Phase 8 首次资格检查：先查后动）

条件（全部满足才可开始 Phase 8）：
① ≈4 周有效在线观察；② ≥100 条已结算 Tomorrow；③ ≥40 条已结算 Week；
④ 数据质量（坏快照不占主导）；⑤ 多样性（21 只跨行业，而非少数高相关股）。
不满足 → 只报告（已结算 Tomorrow/Week 数、可用交易日、缺失率、原因）然后停止等待。

## Gate C2 — 2026-10-27 09:00 HKT（6 周复核）

C1 未就绪或数据太窄时用。条件同上 + ≥6 周 + 可接受缺失率 + 市场状态多样性。
仍不满足 → 继续采集，不因时间到了而强推。

## Phase 8 — 受控权重调节（资格满足后）

流程：生产模型 → 在线结果 + Phase 9 历史 → 层归因 → 候选权重 → out-of-sample 对比
→ 候选报告 → **OWNER 批准** → 才可能晋升。
只生成候选、不自动晋升、owner 批准前生产权重不变。
每个 horizon 独立调，绝不用一套通用权重。Tomorrow 重短窗/事件/期权；Week 用动量/事件持续性/
Serenity 相关性/市场状态；Month 保守（21D 结果成熟慢）；Year 历史验证优先。
候选必须：小、有界、可解释、分 horizon、有证据。
报告必须含：样本量/交易日/horizon/命中率/平均方向化收益/扣基准优势/假多率/假空率/
校准（可测时）/层贡献/行业差异/市场状态差异/out-of-sample，并明确写
「PROMOTION RECOMMENDED」或「KEEP CURRENT MODEL」。

## Month 检查点 — 2026-12-15 09:00 HKT（~3 个月）

评估成熟 21D 结果是否足够认真评估 Month 权重（在线 + Phase 9 历史 + 归因 + 多样性）。
不因满 3 个月就调；样本不足继续等。候选仍需 out-of-sample + owner 批准。

## Year 模型

不设短期在线验证日历 gate。先用 Phase 9 历史 252D 证据；在线 1 年结果自然积累。
在真正成熟前不得宣称 Year 已被在线验证。

## Phase 10 — 文档收尾（无固定日期）

仅在 Phase 9 完成 且 Phase 8 产出稳定接受模型（或 owner 明确决定保持现权重）之后。
更新 README/DEPLOYMENT/.env.example/方法论/五层定义/horizon 定义/学习宇宙行为/
Telegram 行为/AI 降级行为/预测-结算方法论/权重治理。不引入新功能。

## 学习宇宙规则

维持 ~15 只，观察期不扩到 40-50 只（yfinance 保底、多样性已足、API 负载无增益）。
学习 ticker：仅 EOD、有评分、有快照、无 Telegram、无 push_policy、无盘中扫描、
无 Serenity 直警、无 legacy 技术警。

## 样本解释规则

预测多 ≠ 独立证据多。21 股 × 5 天 >100 条 Tomorrow 若全部发生在单一 risk-off 状态，
不等价于 100 个独立市场环境。Phase 8 同时要求最小样本量 + 最小观察时间/多样性。

## 置信度口径

当前显示的「BEARISH 87%」= **模型信念（conviction）**，不是统计校准概率。
校准评估留给 Phase 8/9。

## AI/LLM 规则

Z.AI 不可用 / 余额空 / 429 / LLM_PROVIDER=none 时系统必须完全运作。
AI 只做增强（Serenity 分类、翻译、叙述解读），绝不成为数据采集/五层评分/四 horizon/
Telegram 核心警报/预测记录/结果结算的必需品。

## 生产规则（观察期全程）

不重构、不加框架、不调权、不提前激活 weight_tuner、不自动晋升、不连券商、不自动交易、
不擅自扩学习宇宙、不改 systemd、不重复宽 QA、不重复 review。
真实生产缺陷：只修该缺陷 → 一次针对性冒烟 → 最小安全部署 → 立即回观察模式。

## 日历总表（owner 提醒时间，HKT/SGT）

| 时间 | Gate | 动作 |
|---|---|---|
| 2026-09-22 09:00 | A | 5 天观察质量检查（零改动） |
| 2026-09-25 09:00 | B | Phase 9 历史 multi-horizon 回测（不动生产权重） |
| 2026-10-13 09:00 | C1 | Phase 8 首次资格检查（先查后动） |
| 2026-10-27 09:00 | C2 | 6 周资格复核 |
| 2026-12-15 09:00 | Month | 3 个月 Month 证据审查 |
| Year | — | 无在线日历 gate；历史 252D 先行 |
| Phase 10 | — | 无固定日期；Phase 8 决定稳定后 |

## 当前预期动作

**NOW: DO NOTHING.** 基线 `55d6567`。继续采集。等待 owner 触发 Gate A。
