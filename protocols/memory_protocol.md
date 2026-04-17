# 记忆测试协议（Direction B）

## 核心原则

记忆能力不再通过独立的 MemoryAgentBench quick-test 衡量，而是**嵌入 SWE-bench 多轮 run** 中自然观测：

- 同一 task × 同一 agent，连续跑 5 轮，每轮复用上一轮的 runtime state
- 第 1 轮前清空 memory（隔离 state 目录，不碰默认 `~/.openclaw` 等）
- 观测指标：task_success 随轮次的变化趋势（记忆曲线）

## 实验命令

```bash
python run.py swebench \
  --agent openclaw \
  --mode workspace \
  --limit 20 \
  --runtime-profile memory-enabled \
  --rounds 5 \
  --run-id pilot_mem5
```

## 分析命令

```bash
# 绘制记忆曲线（round 1-5 的 task_success 趋势）
python run.py curve --run-id pilot_mem5

# 对比多个 agent 的记忆曲线
python run.py curve --run-id pilot_mem5 --agents openclaw,hermes,claude-code
```

## State 隔离机制

- state 目录：`data/runtime_state/<agent>/<run_id>/<instance_id>/`
- 第 1 轮前：清空该目录（等效于"无记忆"起点）
- 第 2-5 轮：直接复用，不清理
- 不影响默认 `~/.openclaw`、`~/.hermes`、`~/.claude`

## 与旧协议的关系

`memory_adapter.py` 中的 quick-test（AR / TTL / LRU / CR 四项）**已废弃为主路径**，保留为可选冒烟工具。MemoryAgentBench config 生成器保留（`--generate-config`），供需要对接完整 MemoryAgentBench 环境时使用。
