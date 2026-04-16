# Token 效率测试协议

## 目标
对比三个 agent 系统完成相同任务时的 token 消耗，识别效率差异和浪费模式。

## 测试环境
- 每个 agent 使用默认配置
- 同一任务使用完全相同的初始 prompt
- 记录方式：API 返回的 usage 字段 / agent 自带的统计 / 手动统计

## Token 统计方法

| Agent | 统计来源 |
|-------|---------|
| OpenClaw | TBD - 确认 API 或日志中是否有 token 统计 |
| Nous Hermes | API usage 字段 (OpenAI-compatible) |
| Claude Code | `/cost` 命令或 API usage 字段 |

> **注意**: 不同 tokenizer 会导致绝对数值不可直接比较。
> 辅助指标：统计"完成同一任务的 API 调用次数"作为归一化参考。

## 控制变量
| 变量 | 要求 |
|------|------|
| 任务定义 | 相同的 prompt 和预期输出 |
| 模型温度 | temperature=0（如可配置）|
| 工具集 | 尽量对齐可用工具（文件读写、搜索、终端）|
| 重试 | 首次失败后不自动重试，单独记录 |

## 评估维度

### 绝对效率
- `tokens_total`: 完成任务的总 token 数
- `tokens_in` / `tokens_out`: 输入输出分别统计

### 相对效率
- `tokens_per_loc`: 每产出一行有效代码的 token 消耗
- `tool_calls_count`: 工具调用总次数
- `redundant_calls`: 无效或重复的工具调用

### 效率趋势
- `efficiency_trend`: 连续相似任务时，token 消耗是递减、稳定还是递增

## 数据记录格式

```json
{
  "run_id": "tok-01_hermes_20260416_1",
  "agent": "hermes",
  "task_id": "tok-01",
  "dimension": "token_efficiency",
  "timestamp": "2026-04-16T11:00:00+08:00",
  "metrics": {
    "tokens_in": 1200,
    "tokens_out": 800,
    "tokens_total": 2000,
    "task_completed": true,
    "tool_calls_count": 3,
    "redundant_calls": 0
  },
  "notes": "",
  "model": "hermes-3-llama-3.1-70b"
}
```

## 执行流程
1. 准备标准化的任务输入文件（代码文件、prompt）
2. 启动 agent，输入任务 prompt
3. 记录所有 API 调用的 token usage
4. 判定任务是否完成
5. 保存 JSON 到 data/raw/
6. 每个任务重复 3 次
