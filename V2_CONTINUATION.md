# V2 线性治理规则（Continuation Rules）

> 生产基线：`dcad50f`（V2 Core LIVE / P0 ✅ / P1 ✅ / 期权层保留默认 UNAVAILABLE /
> 预测快照采集 active / Phase 8 冻结 / Phase 9 待做 / Phase 10 待做）
>
> 本文档是唯一权威流程。**ZCode 不会自动等待或自动唤醒**——每个 Gate 到期时由
> owner 手动携带本文档继续。时间流逝 ≠ 后续阶段获批。

## 观察期（前 5 个完整美股交易日）

起始：dcad50f 部署后的第一个完整交易日（2026-09-15）；第 5 天 = **2026-09-21**。

- 只采集数据：每 ticker × horizon × 交易日一条 EOD 权威预测
- 快照必须含：price、五层分数、各层可用性、四个 horizon 的方向/置信/加权分
- Telegram 与生产监控照常
- **禁止**：调权重、解冻 weight_tuner.py

## Gate A — 2026-09-21（仅一次轻量观察质量检查）

检查：快照覆盖预期 ticker、EOD 去重生效、无缺失 horizon 字段、layer_status 记录正确、
daemon 健康、Telegram 健康。
不做宽 QA、不调权重。健康 → 生产不动。

## Gate B — 第 2 周（Phase 9：多 horizon 历史回测）

扩展回测至 1 / 5 / 21 / 252 个交易日（数据不足则跳过长档）。
保留：train/validation 切分、基准漂移对比、最小样本要求、既有规则验证逻辑。
不海量调参；Phase 9 单独不改变生产权重。
输出：各规则/各层按 horizon 的历史价值。只跑一次缩减验证。

## Gate C — Phase 8 资格（两条件同时满足）

1. **时间**：≥ 4–6 周在线观察数据
2. **样本**：≥ 100 条已结算 Tomorrow 预测 且 ≥ 40 条已结算 Week 预测

时间够但样本不够 → 等。不因日历时间到了就调。

## Phase 8 — 受控权重调节（仅 Gate C 后）

- 解冻 Phase 8；用在线预测结果 + Phase 9 历史验证 + 层归因 + 分 horizon 结果
- **只生成候选权重**（bounded、可解释、分 horizon、有 out-of-sample 证据）；
  不直接改写生产权重
- 候选报告必须含：样本量、命中率变化、平均方向化收益、假信号变化、
  （可测时）校准变化、分 horizon 与分层的变更明细
- **生产权重在 owner 明确批准前不变**

## Month / Year 约束

- Month：~3 个月有效在线观察前不做认真调权（Phase 9 历史证据可先行参考）
- Year：不得用短在线历史宣称已验证——先用 Phase 9 历史；在线 1 年验证自然积累

## Phase 10 — 文档收尾（Phase 8 产出稳定接受模型之后）

更新 README / DEPLOYMENT / 配置示例 / 模型方法论 / horizon 定义 / 降级行为 /
观察-学习流程。不引入新功能。

## 观察期生产规则

保持现有生产行为。禁止：重构架构、加框架、加不必要 provider、无必要改 Telegram、
改评分权重、提前恢复 Phase 8、自动晋升模型、连券商、自动交易、重复宽 QA、重复 review。

**真实生产缺陷处理**：只修该缺陷 → 一次针对性冒烟 → 部署最小安全修复 → 回到观察模式。
