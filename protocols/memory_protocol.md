# 记忆架构测试协议

## 目标
评估三个 agent 系统在信息记忆、持久化、召回方面的实际能力。

## 测试环境
- 每个 agent 使用默认配置，不做特殊调优
- 测试前清除所有 agent 的历史记忆/上下文缓存
- 每个测试执行 3 次，取平均值

## 控制变量
| 变量 | 要求 |
|------|------|
| 输入内容 | 三个 agent 使用完全相同的提示词 |
| 时间间隔 | 跨会话测试统一间隔 24h |
| 干扰轮数 | 穿插无关对话轮数相同 |
| 评判人 | 同一人评分，使用统一 rubric |

## 评分标准

### recall_accuracy (0-1)
- 1.0: 完全正确回忆所有事实
- 0.8: 遗漏 1 个事实，其余正确
- 0.6: 遗漏 2 个事实或有 1 个不准确
- 0.4: 只记住部分，且有不准确之处
- 0.0: 完全无法回忆

### hallucination_count (int)
- 统计 agent 回答中不存在于原始输入的"事实"数量

### resolution_quality (1-5)
- 5: 主动指出矛盾，给出清晰分析和建议
- 4: 主动指出矛盾，但分析一般
- 3: 被追问后能识别矛盾
- 2: 模糊地提到不一致
- 1: 完全没有识别矛盾

## 数据记录格式
每次测试记录为 JSON，保存至 `data/raw/`:

```json
{
  "run_id": "mem-01_openclaw_20260416_1",
  "agent": "openclaw",
  "task_id": "mem-01",
  "dimension": "memory",
  "timestamp": "2026-04-16T10:00:00+08:00",
  "metrics": {
    "recall_accuracy": 0.8,
    "hallucination_count": 0
  },
  "notes": "遗漏了截止日期信息",
  "raw_transcript": "（可选）对话原文路径"
}
```

## 执行流程
1. 确认 agent 环境干净（无历史上下文）
2. 按 tasks.yaml 中的 description 执行任务
3. 记录 agent 完整回答
4. 使用评分标准打分
5. 保存 JSON 到 data/raw/
6. 重复 3 次，记录每次的 run_id
