# mem5_full_20260417_v2 多轮对比分析报告

**实验标识**: `mem5_full_20260417_v2` · 2026-04-17
**任务**: SWE-bench Verified · `astropy__astropy-12907` · 模式 `repo-mentioned`
**Agent × Round**: 3 × 5 = 15 次 run
**共同后端**: Kimi K2 for coding (所有三个 runtime 统一路由到同一模型，排除 LLM 差异)
**记分器**: SWE-bench 官方 harness (Docker)

---

## 1. 数据总览

三个 agent 每个跑 5 轮；第 1 轮前 `runtime_state` 清空，第 2–5 轮复用上一轮的状态目录以测 Direction B 的"memory 复用"信号。

| 字段 | 说明 |
|---|---|
| `pure_input` | 非 cache 的真实输入 tokens |
| `cache_read` | 命中 cache 的 tokens (Kimi 定价 0.1× input) |
| `cache_write` | 写入 cache 的 tokens (1.25× input) |
| `output` | 模型输出 tokens (3× input) |
| `provider_tokens` | 实际计费 tokens (pure_input + cache_read×0 + cache_write + output，provider 自报) |
| `runtime_tokens` | 运行时总 tokens (provider + cache_read) |
| `cost_tokens` | 等价 input-tokens (provider 无关口径，用于横向比较) |
| `cost_usd` | Kimi-for-coding 定价折算真美元 (排除 openclaude 错误上报的 Anthropic 定价) |

### Harness 结果 (`resolved_heatmap.png`)

**15/15 全部 resolved** — 每个 agent 每一轮都生成了通过 harness 的 patch。这意味着**任务难度维度在这一实例上被 saturate**，无法从结果层面区分三个 agent 的成功率，差异只能从 token / tool / latency 三个过程指标看。

---

## 2. Token 结构三分天下 (`token_breakdown.png`)

五分层堆叠柱 (平均 / run)：

| Agent | pure_input | cache_read | cache_write | output | 总 runtime |
|---|---:|---:|---:|---:|---:|
| openclaw | **515.7 k** | 0 | 0 | 3.4 k | **519.1 k** |
| hermes | 54.8 k | **1,520 k** | 0 | 18.6 k | **1,593 k** |
| claude-code | 0 | **1,929 k** | 0 | 17.5 k | **1,946 k** |

**三种迥然不同的 token profile：**

- **openclaw**: 没有 cache 机制，每次都把 prompt 原样送给 provider；runtime_tokens ≈ provider_tokens ≈ pure_input。对 Kimi 来说每个 token 都按完整 input 价计费。
- **hermes**: cache_read 占 ~95%，pure_input 只占 ~3%。真正向 provider 付钱的部分 (`provider_tokens` ≈ 73 k) 极小，因为 Kimi 对 cache_read 只收 0.1× input 单价。
- **claude-code (openclaude)**: cache_read 占 ~99%，`pure_input = 0`。这是因为 openclaude 的 `modelUsage.inputTokens` 完全等于 `cacheReadInputTokens` — runner 做减法后 pure_input 落到 0。极端 cache-heavy 的运行模式。

> ⚠️ **openclaude 的 `costUSD` 字段不可用** — 它按 Anthropic Sonnet/Opus 定价估，对 Kimi 后端高估 ~100×。分析中所有 `cost_usd` 都走 `cost_usd()` 的后备路径，用 Kimi 定价乘 5-bucket 分解重新计算。

---

## 3. 成本对比 (`cost_comparison.png`)

### cost_tokens (provider 无关的等价 input-token 数)

| Agent | 平均 cost_tokens / run |
|---|---:|
| openclaw | **526 k** |
| hermes | 263 k |
| claude-code | **245 k** ← 最低 |

### cost_usd (按 Kimi 定价折算)

| Agent | 平均 $ / run |
|---|---:|
| openclaw | **$0.086** |
| hermes | $0.077 |
| claude-code | **$0.073** ← 最低 |

**两个口径排序一致但差距不同。**

- `cost_tokens` 把 cache_read 加权 0.1×，claude-code 的 1.9 M cache 折成 ~190 k 等价 input，比 openclaw 的 515 k pure_input 便宜得多。
- `cost_usd` 进一步压缩差距：cache_read 在 Kimi 只收 $0.015/M，openclaw 的 pure_input $0.15/M 是 cache 的 10 倍；但 openclaw 总量只有 515 k vs claude-code 的 1.9 M，所以美元数字相对拉近。
- **结论**：只要 cache 命中率够高，runtime 可以"跑很多 tokens 却花很少钱"。cost_tokens 更诚实地反映了 openclaw 把每一个 token 都按 full price 付的事实。

---

## 4. TEFS 效率排名

TEFS (Token Efficiency Function Score) = resolved_score / (cost_tokens / 1000)，单位 score per 1k 等价 input tokens。

| Agent | TEFS(cost_tokens) |
|---|---:|
| claude-code | **0.0047** ← 最高 |
| hermes | 0.0040 |
| openclaw | 0.0021 |

在所有任务都 resolved 的前提下，**claude-code 在"每 1k 等价 input token 产生多少成功任务"上最高效**，hermes 其次。openclaw 因为完全不 cache，每轮都要烧全量 pure_input，效率最低。

> 注意：TEFS 依赖 `resolved` 作为分子。在 100% 通过的情况下，TEFS 完全由分母 (cost) 决定；真正区分 agent 效率差异需要**跨难度分布**的任务集。

---

## 5. Memory 行为曲线 (`memory_curve.png`)

这是 Direction B 的核心信号：**复用 state 后，第 N 轮是否比第 1 轮更便宜/更少工具调用？**

### openclaw — 最清晰的递减信号

| Round | tool_calls | latency_s | provider_tokens | cost_tokens |
|---:|---:|---:|---:|---:|
| R1 | **32** | 253 | 857 k | 876 k |
| R2 | 13 | 140 | 578 k | 582 k |
| R3 | 6 | 91 | 343 k | 346 k |
| R4 | 7 | 104 | 446 k | 450 k |
| R5 | **5** | **59** | 374 k | 378 k |

**Δ(R5 − R1): tools −27 (−84%), latency −194 s (−77%), cost_tokens −498 k (−57%)。**

这是三个 agent 里最强的 memory-reuse 信号。复用 state 后 openclaw 每轮只需要少量工具调用就能再次生成 patch，说明：
- 它的 state 里保留了前一轮的工作痕迹，避免重复探索；
- 但因为没有 cache，每轮依然要把那个更短的 prompt 原样送给 provider，所以 cost 降幅 (57%) 小于 tools 降幅 (84%)。

### hermes — 弱递减 + R4 异常

| Round | tool_calls | latency_s | runtime_tokens | cost_tokens |
|---:|---:|---:|---:|---:|
| R1 | 49 | 615 | 1.44 M | 225 k |
| R2 | 41 | 494 | 1.16 M | 206 k |
| R3 | 39 | 1056 | 1.32 M | 239 k |
| R4 | **72** | **1274** | **2.67 M** | 406 k |
| R5 | 42 | 703 | 1.38 M | 238 k |

R4 是明显的外点 (72 工具，1274 秒，runtime 翻倍)。可能原因：单任务 run-to-run 噪声、前一轮 state 干扰、或 hermes 在某轮陷入 tool-loop。R5 立即回落到 R1/R2 水平，说明不是累积性退化。

**Δ(R5 − R1): tools −7, cost_tokens +13 k** — 几乎持平，memory 效应被噪声淹没。

### claude-code — 中等递减 + R4 谷底

| Round | tool_calls | latency_s | runtime_tokens | cost_tokens |
|---:|---:|---:|---:|---:|
| R1 | **70** | 881 | 3.22 M | 383 k |
| R2 | 55 | 1007 | 2.16 M | 283 k |
| R3 | 54 | 1351 | 2.13 M | 257 k |
| R4 | **31** | 557 | 1.03 M | **140 k** |
| R5 | 47 | 526 | 1.19 M | 163 k |

**Δ(R5 − R1): tools −23 (−33%), latency −355 s (−40%), cost_tokens −220 k (−57%)。**

曲线单调性不如 openclaw 漂亮，但整体趋势下降。R4 是个"爽脆"的谷底 (31 tools)，可能是复用到位后直接命中最短路径。R5 小反弹可能是状态漂移或 claude-code 内置行为 (e.g., 重新 TODO-list 化)。

### 三方对比

| 指标 | openclaw | hermes | claude-code |
|---|---:|---:|---:|
| Δtools (R5−R1) | **−27** | −7 | −23 |
| Δprov_tokens | −483 k | +4 k | −6 k |
| Δcost_tokens | −498 k | +13 k | −220 k |
| R1 tools | 32 | 49 | 70 |
| R5 tools | 5 | 42 | 47 |

**openclaw 在 memory 效应上最强** (tools/cost 都大幅下降)；**claude-code 效应中等但方向一致**；**hermes 最弱，基本没从 state 复用里获益**，且出现明显外点。

---

## 6. 核心观察

1. **100% resolved**：单任务下难度不是区分维度。未来实验要扩到 SWE-bench 多实例 + 难度分层才能在成功率上见到差异。
2. **Token profile 三分天下**：openclaw 无 cache、hermes 约 95% cache_read、claude-code 约 99% cache_read (pure_input = 0)。同样都跑 Kimi，三种 runtime 暴露给 provider 的 billable footprint 相差 ~7×。
3. **openclaude 自报成本不可信**：`modelUsage.costUSD` 按 Anthropic 定价估，对 Kimi 后端高估 100×。本次分析走 `cost_usd()` 的后备路径，用 Kimi 定价重算。
4. **memory 复用效应量化**：openclaw tools −84%、claude-code tools −33%、hermes 无显著效应。这对应 Direction B 的"memory-enabled runtime 是否真的带来工作量节省"的初步答案 — **带来了，且不同 runtime 效应量差异显著**。
5. **TEFS 排名 (效率每 k-token 成功率) claude-code > hermes > openclaw**，但在 100% resolved 下完全由 cost 分母决定，不能跨 agent 归因为"智能水平差异"，只能归因为 cache 使用模式。

## 7. 局限与下一步

- **单任务 (astropy__astropy-12907)** — 所有结论都在"这一道题"上成立。必须扩到 10+ 实例、跨难度、跨 repo 才能泛化。
- **随机性**：hermes R4 的异常提示同一 agent 在同一 round 的方差可能很大；应跑 ≥3 个 seed 取 mean±std。
- **state 依赖链**：R1→R5 是串行累积的，R4 的表现也受 R3 状态影响；要严格归因需要做 "clean start at each round" 对照组。
- **下一步**：
  1. 扩实验到 5 道 SWE-bench 实例 (覆盖简单/中等/困难)，保持 3 × 5 = 15 run per 实例；
  2. 加对照组 `memory-off` (每轮清空 state)，量化 memory 的净贡献；
  3. 对 hermes 的 R4 spike 做 trace 分析，判断是 tool-loop、状态漂移、还是 LLM 采样噪声。

---

*Generated from `data/raw/swe_astropy__astropy-12907_*_mem5_full_20260417_v2_r*.json` (15 records).*
*Figures in `results/mem5_full_v2/`.*
