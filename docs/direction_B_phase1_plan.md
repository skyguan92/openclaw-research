# Direction B · Phase 1 实验设计

**状态**: 提案 · 待确认
**上游文档**: `results/mem5_full_v2/paper.html` (Phase 0 validation)
**编写**: 2026-04-18
**预计启动**: 确认后立即

---

## 0 · 本文档的目的

Phase 0 (`mem5_full_20260417_v2`) 只证明了 "三个 runtime 的 memory 机制跑得通"。
Phase 1 的目标是**正面回答**：在真实用户场景下，persistent memory 给三个
runtime 各带来多大的 (a) 成功率提升 (b) 成本降低，以及这些收益对
"两次做同一任务之间穿插多少其他任务" 有多稳健。

本文档是实验方案的详细规格，供 review 后直接进入实现。

---

## 1 · 真实用户场景的形式化

用户的实际使用模式 (按你的描述)：

```
用户: 帮我修 astropy__astropy-12907        [session_A 开始]
agent: ...work work work...
       patch 生成、harness 评分、session_A 关闭
       [agent 在 memory 里写下: "我刚才在 astropy/units 改了 equivalency 函数..."]

[用户又做了一些别的事 —— 问了 5 个不相关的 django 问题]

用户: 帮我修 astropy__astropy-12907        [session_B 开始，距离 d=5]
agent: ...读 memory, 发现之前做过类似的 → 快速复用...
       patch 生成、harness 评分
```

**关键事实**：
1. 任务描述是**具体的** (带 instance_id)，不模糊。
2. 每个 session 是<b>独立冷启 subprocess</b>，不共享任何 in-RAM 状态。
3. agent 对"上次"的感知 = memory 池里有多少**不是这个任务**的条目。没有时间感。
4. 穿插任务也是**真实任务**，会往 memory 写条目（否则就不叫"噪声"了）。

---

## 2 · 核心自变量与预期信号

### 2.1 · 自变量

| 变量 | 水平 | 备注 |
|---|---|---|
| `runtime` | openclaw / hermes / claude-code | 与 Phase 0 同 |
| `target_task` T | 5 个难题 (见 §3) | 从 phase1 pool 里挑 |
| `session_distance` d | 0 / 1 / 3 / 5 / 10 | 穿插的不相关任务数 |
| `memory_mode` | on / off | off 即对照组 |

### 2.2 · 因变量

| 指标 | 含义 | 期望方向 (memory-on vs off) |
|---|---|---|
| `Δresolved@d` | P(repeat resolved \| on) − P(repeat resolved \| off) | `> 0` |
| `Δcost_tokens@d` | cost(repeat \| on) / cost(repeat \| off) | `< 1.0` |
| `Δtool_calls@d` | tools(repeat \| on) − tools(repeat \| off) | `< 0` |
| `retrieval_hit_rate@d` | memory 检索中命中 T 首次 session 条目的比例 | 衰减应平缓 |

---

## 3 · 任务池

### 3.1 · 选择标准

**难度条件**（关键）：每个 target 必须满足 **memory-off 条件下 pass rate ≤ 60%**。
  - 若首轮即过，memory 的价值测不出。
  - 筛选方法：Phase 1 启动前先做 **task-selection pilot**，跑 1 次 memory-off
    每 instance × 3 runtime = 60 run，剔除所有 runtime 全过的 instance。

**多样性条件**：targets 跨至少 3 个 repo；fillers 跨至少另外 3 个 repo (尽量与
targets 不重叠，避免 repo 级 repo-locality 干扰 memory 检索)。

### 3.2 · 候选分配（基于现有 `phase1` 池的 20 instance）

| 角色 | 数量 | 当前候选 (待 pilot 筛选) |
|---|---|---|
| `target` | 5 | astropy-13033, django-13212, matplotlib-23299, pytest-5262, sympy-21379 |
| `filler` | 10 | 其余 15 个，pilot 后挑难度适中的 10 个 |
| `reserve` | 5 | 留作备用 / seed 扩展 |

最终分配依赖 §3.1 的 pilot 结果。

### 3.3 · Filler 属性要求

filler **不需要通过**，只要 agent 做出**合理的工作** —— 目的是让 memory 里产生
"真实的、任务特定的条目"。即使 filler 失败了，它写入 memory 的探索痕迹本身
就是"噪声源"，这正是我们要的。

---

## 4 · 实验序列构造 (Snapshot Tree)

### 4.1 · 朴素线性方案的代价

最朴素做法：对每个 `(runtime, target, d, mode)` 独立跑完整序列。
共需 3 × 5 × 5 × 2 = 150 个 target-slot，每 slot 需要 `d + 2` 次 run，
总计约 `3 × 5 × (2 + 3 + 5 + 7 + 12) × 2 = 870` run。不可接受。

### 4.2 · Snapshot Tree 方案

利用 `runtime_state/` 是磁盘目录、可 `cp -r` 快照的事实：

```
per (runtime, target T):
  step 0: clean state
  step 1: run T (first)                → state_T0
  step 2: branch state_T0 → run T (repeat_d0) → 记录为 d=0 数据点
  step 3: resume from state_T0, run X_1 → state_X1
  step 4: branch state_X1 → run T (repeat_d1) → d=1 数据点
  step 5: resume from state_X1, run X_2, X_3 → state_X3
  step 6: branch state_X3 → run T (repeat_d3) → d=3 数据点
  step 7: run X_4, X_5 → state_X5; branch → repeat_d5
  step 8: run X_6..X_10 → state_X10; branch → repeat_d10
```

每个 target × runtime 需要：
- 1 T_first
- 10 filler (X_1..X_10)
- 5 T_repeat (d=0,1,3,5,10)
= **16 run / target / runtime**

全实验（memory-on）：`3 runtime × 5 target × 16 run = 240 run`
memory-off baseline（不需要 repeat，也不需要 filler）：
  `3 runtime × (5 target + 5 重复验证) = 30 run`

**总 ~270 run**（比朴素少 ~70%）。

### 4.3 · 快照实现

`runtime_state/phase1/<runtime>/<target_id>/snapshots/state_X<k>/` 存每个节点的
目录拷贝。每个 T_repeat 跑前 `cp -r` 对应 snapshot 到工作 state dir。

---

## 5 · 对照组：Memory-Off 语义

Memory-off 的正确做法**不是**"T_first 后清空再 T_repeat"(那等价于两次独立首跑)，
而是**确认 memory-off 下首跑表现就是 baseline**。具体：

```
memory-off baseline for target T, runtime R:
  clean runtime_state
  run T once  → record as "memory_off_first_attempt"
```

为降低单次噪声，每个 target 跑 **3 次 memory-off**，取均值与方差。这让
memory-off 的 30 run 上升到 **45 run**（3 runtime × 5 target × 3 seed）。

---

## 6 · Runner-Level Instrumentation

### 6.1 · Memory 事件埋点（新增）

为每个 runtime 实现一个最小 memory-event logger，输出到
`usage_details.memory_events`:

```json
"memory_events": [
  {"kind": "read",  "key": "astropy/units",    "hit": true,  "entry_id": "T0-event-42"},
  {"kind": "read",  "key": "django/queryset",  "hit": false},
  {"kind": "write", "key": "astropy/units",    "entry_id": "Td1-event-7"}
]
```

- **openclaw**: 在 memory 模块的读写调用处加 log hook
- **hermes**: 已有 state.db；拦截读写 query
- **claude-code**: 通过 CLAUDE.md / session 文件的 grep/read 操作推断（较间接）

### 6.2 · Snapshot 哈希

每条 run 记录在 `usage_details` 里加：

```json
"runtime_state_hash_before": "sha256:...",
"runtime_state_hash_after":  "sha256:..."
```

让后期分析能验证"序列确实如设计般演化了"。

### 6.3 · 扩展 Output Schema

`swe_*.json` 的 top-level 加字段：

```json
{
  "experiment_id": "phase1",
  "phase1_meta": {
    "target_task_id": "astropy__astropy-13033",
    "role": "target" | "filler",
    "attempt": "first" | "repeat" | "filler" | "memory_off",
    "session_distance": 3,             // null when N/A
    "filler_index": 2,                 // X_2, for filler rows
    "filler_sequence_so_far": ["X_1","X_2"],  // 便于复盘
    "snapshot_source": "state_X1",     // 本 run 是从哪个 snapshot 起跑的
    "seed": 1                           // 仅 memory-off baseline
  }
}
```

---

## 7 · 规模与时间估算

| 组件 | run 数 | 单 run 均值 | 估计时长 (串行) |
|---|---:|---:|---:|
| Task-selection pilot | 60 | ~10 min | ~10 h |
| Memory-on snapshot tree | 240 | ~10 min | ~40 h |
| Memory-off baseline | 45 | ~10 min | ~7.5 h |
| **合计** | **345 run** | | **~58 h 串行** |
| 3-runtime 并行 | | | **~20 h wall-clock** |

每日 8 小时工作计算，约 3 个工作日可跑完。若单任务均值 &gt; 10 min（难题预期更长），
等比缩放。

### Cost 估算 (Kimi 定价)

按 Phase 0 观测：均值 $0.08/run × 345 run ≈ **$28 USD**。可接受。

---

## 8 · 分阶段执行 Gate

每阶段有明确的"可以继续吗" 判断。

### Phase 1a · Task Selection Pilot (1 天)
- 跑 20 instance × 3 runtime × 1 (memory-off) = 60 run
- **Gate**：至少 5 个 instance 满足 "≥1 runtime 失败" → 可进 Phase 1b
- 若所有 20 都 100% 过 → 换更难的任务源（SWE-bench Lite 不行，需 SWE-bench Full 或手工挑）

### Phase 1b · Pilot on 1 target × 3 runtime (半天)
- 完整跑 1 target 的 snapshot tree (16 run × 3 runtime = 48 run)
- **Gate**：
  - 所有 runtime 的 snapshot save/restore 成功率 100%
  - memory_events 埋点在每个 runtime 都有输出
  - 至少一个 (runtime × d) 组合展现出 Δresolved &gt; 0 或 Δcost &lt; 1 的信号
- 若 gate 过 → 全量 Phase 1c；若不过 → 修 bug 重来

### Phase 1c · 全量 (2-3 天)
- 剩余 4 targets × 3 runtime × 16 run = 192 run + 45 baseline = 237 run
- 加 Phase 1b 的 48 run = 总 285 run

### Phase 1d · 分析 + 报告 (1-2 天)
- 生成距离衰减曲线（每 runtime 5 线：d=0,1,3,5,10）
- 计算检索命中率矩阵
- 更新 `paper.html` Phase 1 章节 + 新图

---

## 9 · 工程 Prerequisites (必须在 Phase 1b 前完成)

| # | 工作项 | Owner | 预计工时 |
|---|---|---|---:|
| 9.1 | Runner 加 snapshot save/restore 逻辑（按 `phase1_meta.snapshot_source` 决定起始 state） | eng | 0.5 d |
| 9.2 | 三个 runtime 的 memory-event logger 接入 | eng | 1 d (分 runtime) |
| 9.3 | `runtime_state_hash_before/after` 计算 + schema 写入 | eng | 0.25 d |
| 9.4 | `phase1_meta` schema 在 `swe_*.json` 里实装 | eng | 0.25 d |
| 9.5 | 把 Phase 1 序列生成逻辑写成可执行脚本 `scripts/run_phase1.py` | eng | 0.5 d |
| 9.6 | `analysis/phase1_curves.py`：距离衰减曲线 + 检索命中率图 | analysis | 0.5 d |
| 9.7 | CI 检查：`upstream_reported_cost_usd` 偏差 &gt; 5× 则报警 | eng | 0.25 d |
| **合计** | | | **~3.25 d** |

---

## 10 · 可证伪假设 (pre-registered)

在数据生成之前、分析之前，锁定以下三个假设作为"我们到底在预测什么"：

### H1 · Memory-on 在难题上带来正的 Δresolved

> 对所有三个 runtime，存在至少一个距离 d ∈ {0,1,3,5,10}，使得
> `Δresolved@d > 0.1`（即 memory 至少多救回 10% 的题）。
> 若不成立 ⟹ 当前 memory 实现只是"省 token 不救题"，需重新审视 runtime_state
> 里到底写了什么。

### H2 · 距离衰减存在且有 runtime 差异

> 每个 runtime 的 Δresolved / Δcost 曲线都是单调递减 (或至少非增) 函数。
> 且 claude-code 的衰减率（d=10 相对 d=0 的保留率）**不小于** openclaw、hermes。
>
> 依据：openclaude 的 memory 是持久化 markdown 文件 + grep，对远距离检索
> 不敏感；openclaw 的工作笔记偏 ephemeral，远距离可能被覆盖；hermes 有
> 结构化 state.db 但检索策略未知。

### H3 · 三个 runtime 的 R4 外点在 Phase 1 不再出现（多 seed 下）

> 即 Phase 0 观察到的 hermes R4 (72 tools) 是采样噪声，不是结构性 bug。
> 若在 Phase 1 多 seed 仍 reproduce，则 hermes 有可复现的 tool-loop bug，
> 需 pre-Phase-1b 修复。

---

## 11 · 需要决策 / 确认的开放问题

1. **Target 选多少个？** 本文档默认 5。若预算允许可加到 8，显著降方差。
2. **Filler 是否可以跨实验共享？** 即 target T_1 的 filler 序列能否和
   target T_2 的完全一样？当前倾向 "是，共享一套 10-filler 序列"（简化实现），
   但这会让"不同 target 的 memory 池长得一模一样" — 可能是 feature 也可能是 bug。
3. **Filler 要不要跟 target 同 repo？** 若允许，memory 检索会更容易因"repo
   匹配"而命中，高估 memory 价值。默认**不允许**，fillers 取自不同 repo。
4. **openclaude 的 memory-event 埋点怎么做？** 官方 CLI 无 hook API。
   候选：(a) 拦截 CLAUDE.md 的 filesystem 读写；(b) 在 prompt 里加指令
   让 agent 显式声明检索；(c) 放弃该 runtime 的检索命中率。倾向 (a)。
5. **快照粒度**：`runtime_state/` 可能每次 run 增长几 MB~几十 MB，10 个
   snapshot × 5 target × 3 runtime ≈ 150 个 snapshot。预估磁盘几 GB，可接受。
   但需要 verify 不会膨胀到 &gt;10 GB。
6. **Harness 评测并行度**：5 target × 16 run × 3 runtime = 240 harness 实例。
   当前单机串行跑，考虑用 Docker Swarm / K8s 并行。

---

## 12 · Definition of Done

Phase 1 完成的判据：

- [ ] 345 run (或调整后数量) 全部产出 `swe_*.json` 含 `phase1_meta`
- [ ] 5 target × 3 runtime × 5 distance 的距离衰减图出图
- [ ] `retrieval_hit_rate` 矩阵出图（若 埋点成功）
- [ ] H1/H2/H3 各给出 accept / reject / inconclusive 的数值判断
- [ ] `paper.html` 增 Phase 1 章节，结论章节据新数据改写
- [ ] 工程 prerequisites (§9) 全部入库 + 有测试

---

## 附录 A · 风险清单

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 20 个 instance 全 100% 通过（Phase 1a gate fail） | 中 | 高 — 需换任务源 | 预留后备 SWE-bench Full instance 清单 |
| snapshot save/restore 在某 runtime 不工作 | 中 | 中 | Phase 1b pilot 先验证，3 个 runtime 单独做 |
| openclaude 无法埋点 retrieval 事件 | 高 | 低 — 其他指标仍可算 | 报告中注明"该 runtime 的 hit rate 不可观测" |
| 磁盘空间不够 (snapshots 膨胀) | 低 | 中 | 跑前 `du -sh` 基线，超 10 GB 压缩或删 |
| 单 run &gt;30 min（难题超长） | 中 | 中 — 时长翻倍 | Phase 1b 量到实际 per-runtime 均值，决定是否降 target 数 |

---

## 附录 B · 开始命令（待实装后填）

```bash
# Phase 1a: task selection pilot
python scripts/run_phase1.py --stage pilot --out data/raw/phase1_pilot

# Phase 1b: single-target snapshot tree
python scripts/run_phase1.py --stage tree --targets astropy__astropy-13033 \
  --distances 0,1,3,5,10 --out data/raw/phase1_tree

# Phase 1c: full rollout
python scripts/run_phase1.py --stage full --targets-file docs/phase1_targets.yaml

# Phase 1d: analysis
python -m analysis.phase1_curves --data data/raw/phase1_tree \
  --out results/phase1/
```
