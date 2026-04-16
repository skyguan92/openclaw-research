# OpenClaw Research

AI Agent 系统横向对比研究：OpenClaw vs Nous Hermes vs Claude Code

## 研究维度

| 维度 | Benchmark | 来源 |
|------|-----------|------|
| **任务成功率** | [SWE-bench Verified](https://github.com/SWE-bench/SWE-bench) (500 题) | Princeton NLP, 业界标准 |
| **记忆架构** | [MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench) + 快速测试 | ICLR 2026 |
| **Token 效率** | TEFS 指标 (嵌入 SWE-bench 流程统计) | 借鉴 MCP-Bench |

## 项目结构

```
run.py               统一入口（所有命令从这里走）
adapters/            三个 agent 的适配层
  agent_runner.py      统一调用接口（API/CLI 双模式）
  swebench_adapter.py  SWE-bench 集成
  memory_adapter.py    记忆评测集成
vendors/             第三方 benchmark（git clone，不入库）
  swe-bench/           SWE-bench Verified
  memory-agent-bench/  MemoryAgentBench (ICLR 2026)
benchmarks/          自定义快速测试任务 (tasks.yaml)
protocols/           测试协议文档
scripts/             手动记录工具
data/raw/            所有测试数据（JSON per run）
analysis/            分析脚本 + Jupyter notebook
results/             图表和报告
```

## 快速开始

```bash
# 1. 安装依赖
source .venv/bin/activate
pip install datasets requests pyyaml pandas matplotlib seaborn

# 2. 冒烟测试（验证环境）
python run.py smoke

# 3. 跑记忆快速测试（直接调 API，最快验证）
python run.py memory --agent openclaw --quick-test

# 4. 跑 SWE-bench（取 5 题试跑）
python run.py swebench --agent openclaw --limit 5

# 5. 查看结果
python run.py compare
python run.py visualize --output results/
```

## 命令速查

| 命令 | 做什么 |
|------|--------|
| `python run.py smoke` | 冒烟测试（API + 数据集 + Docker） |
| `python run.py swebench --agent NAME --limit N` | SWE-bench 评测 |
| `python run.py swebench --evaluate FILE` | 评测已有预测文件 |
| `python run.py memory --agent NAME --quick-test` | 快速记忆测试 |
| `python run.py memory --agent NAME --generate-config` | 生成 MemoryAgentBench 配置 |
| `python run.py compare` | 统计摘要 |
| `python run.py compare --demo` | 用 demo 数据演示 |
| `python run.py visualize --output results/` | 生成图表 |
| `python run.py record --agent NAME --task ID` | 手动记录结果 |

`--agent` 可选: `openclaw`, `hermes`, `claude-code`, `all`

## Token 效率计量

Token 统计自动嵌入 SWE-bench 流程：每次 agent 调用都记录 tokens_in/out/total，
保存到 `data/raw/`。分析时用 TEFS (Token Efficiency Function Score) 指标：

    TEFS = task_score / (tokens_total / 1000)

即"每消耗 1k token 获得的任务得分"，越高越好。
