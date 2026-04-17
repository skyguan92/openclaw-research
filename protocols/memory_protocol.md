# 记忆测试协议（Direction B）

## 当前主路径

本研究的记忆能力衡量，已从"独立的 MemoryAgentBench 风格 QA"改为"在同一 SWE-bench instance 上复用隔离 runtime state 多轮执行，观察行为变化"。

理由：单纯的事实召回测试主要考的是底层 LLM 的长上下文能力，而不是三个 agent 框架各自的 memory/session 架构。多轮 SWE-bench 任务里，能明显看出:

- 第 1 轮 vs 第 N 轮的 token / tool-call 消耗曲线
- patch 是否稳定（`patch_stability`）
- `resolved_rate` 是否随轮次提升 / 下降
- 是否出现"memory poisoning"（上下文膨胀导致决策退化）

这些才是真正区分 OpenClaw / Hermes / Claude Code 记忆架构的信号。

## 操作方式

```bash
python run.py swebench \
  --agent all --mode repo-mentioned \
  --instance-set phase1 \
  --run-id phase1_mem5 \
  --runtime-profile memory-enabled --rounds 5 \
  --auto-evaluate --timeout 0

python run.py curve --experiment-id phase1_mem5
```

运行机制：

- 每个 `(agent, task)` 分配独立 runtime state 目录（`data/runtime_state/<experiment>/<agent>/<task>/`）
- 第 1 轮前清空 state；后续 4 轮复用同一 state
- 不触碰默认 `~/.openclaw` / `~/.hermes` / `~/.claude`
- `--auto-evaluate` 让每一轮跑完立即送 harness，`resolved` 直接写回 `metrics` 块

## 已废弃路径

- `python run.py memory --quick-test` 仍可跑，但只留作 smoke；**不进论文结论**
- MemoryAgentBench config 生成器保留，等将来需要对接 ICLR 2026 外部 benchmark 时再重新评估
