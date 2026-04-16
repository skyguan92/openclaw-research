# 任务执行成功率测试协议

## 目标
评估三个 agent 系统在真实软件工程任务中的端到端完成能力。

## 测试环境
- 每个任务提供相同的初始代码库（如有）
- Agent 可使用所有默认工具（终端、文件操作、搜索）
- 不给 agent 额外提示或帮助，观察自主完成能力
- 每个任务设定最大时间 / 最大轮数限制

## 限制条件
| 变量 | 要求 |
|------|------|
| 最大轮数 | 30 轮对话 |
| 最大时间 | 30 分钟 |
| 人工干预 | 不允许（超限则判定失败）|
| 初始代码 | 三个 agent 使用相同的 git snapshot |

## 评判标准

### 二元判定 (pass/fail)
按 tasks.yaml 中每个任务的 `pass_criteria` 逐条检查：
- **pass**: 所有 criteria 满足
- **partial**: 满足部分 criteria（记录满足了哪些）
- **fail**: 核心 criteria 未满足

### 质量评分 (1-5)
即使通过，也评估产出质量：
- 5: 优秀 — 代码质量高，方案最优，风格一致
- 4: 良好 — 功能正确，有小瑕疵
- 3: 及格 — 功能可用但方案不够优雅
- 2: 勉强 — 能跑但有明显问题
- 1: 失败 — 核心功能未实现

### 自主恢复能力
- `error_encountered`: bool — 是否遇到错误
- `self_recovered`: bool — 是否自主恢复
- `recovery_rounds`: int — 恢复用了几轮

## 数据记录格式

```json
{
  "run_id": "suc-01_claude-code_20260416_1",
  "agent": "claude-code",
  "task_id": "suc-01",
  "dimension": "task_success",
  "timestamp": "2026-04-16T14:00:00+08:00",
  "metrics": {
    "result": "pass",
    "quality_score": 4,
    "criteria_met": ["pip_install_ok", "api_callable", "tests_pass"],
    "criteria_failed": [],
    "rounds_used": 12,
    "time_minutes": 8.5,
    "error_encountered": true,
    "self_recovered": true,
    "recovery_rounds": 2
  },
  "notes": "第一次因缺少 alembic 依赖失败，自主安装后成功"
}
```

## 执行流程
1. 为每个任务准备干净的代码库 snapshot（放在 benchmarks/task_success/）
2. 启动 agent，输入任务 prompt
3. 观察但不干预，计时计轮
4. 任务完成或超限后，检查 pass_criteria
5. 评质量分
6. 保存 JSON 到 data/raw/
7. 每个任务重复 3 次
