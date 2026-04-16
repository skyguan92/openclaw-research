# OpenClaw Research

AI Agent 系统横向对比研究：OpenClaw vs Nous Hermes vs Claude Code

## 研究维度

| 维度 | 关注点 | 任务数 |
|------|--------|--------|
| 记忆架构 | 持久化、召回准确性、矛盾检测、跨会话连贯性 | 5 |
| Token 效率 | 相同任务的 token 消耗、工具调用效率、学习效应 | 5 |
| 任务成功率 | 端到端完成能力、质量评分、自主错误恢复 | 6 |

## 项目结构

```
benchmarks/          测试任务定义 (tasks.yaml)
protocols/           测试协议文档（评分标准、控制变量）
scripts/             benchmark runner (交互式记录测试结果)
data/raw/            原始测试数据 (JSON per run)
analysis/            分析脚本 + Jupyter notebook
results/             生成的图表和报告
```

## 快速开始

```bash
# 安装依赖
pip install -e .

# 查看所有测试任务
python scripts/run_benchmark.py --list

# 记录一次测试结果
python scripts/run_benchmark.py --agent openclaw --task mem-01

# 运行对比分析（demo 数据）
python analysis/compare.py --demo

# 生成图表（demo 数据）
cd analysis && python visualize.py --demo --output ../results/
```

## 工作流

1. 按 `protocols/` 中的协议执行测试
2. 用 `scripts/run_benchmark.py` 记录每次 run
3. 数据自动保存到 `data/raw/*.json`
4. 用 `analysis/compare.py` 查看统计摘要
5. 用 `analysis/visualize.py` 生成图表
6. 用 Jupyter notebook 做深度探索分析
