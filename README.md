# OpenClaw Research

AI Agent 系统横向对比研究：OpenClaw vs Nous Hermes vs Claude Code

## 研究维度

| 维度 | Benchmark | 来源 |
|------|-----------|------|
| **任务成功率** | [SWE-bench Verified](https://github.com/SWE-bench/SWE-bench) (500 题) | Princeton NLP, 业界标准 |
| **记忆架构** | 多轮 SWE-bench run（`--runtime-profile memory-enabled --rounds 5`）| Direction B 协议 |
| **Token 效率** | TEFS 指标 (嵌入 SWE-bench 流程统计) | 借鉴 MCP-Bench |

所有维度共享同一批 SWE-bench 多轮 run；不再单独跑 MemoryAgentBench quick-test。

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

# 3. Phase 1 pilot（20 题，memory-enabled，5 轮）
python run.py swebench \
  --agent openclaw \
  --mode workspace \
  --limit 20 \
  --runtime-profile memory-enabled \
  --rounds 5 \
  --run-id pilot_mem5

# 4. 跑真实 SWE-bench workspace case
python run.py swebench --agent openclaw --mode workspace --instance-ids astropy__astropy-12907 --run-id pilot_astropy

# 5. 查看结果
python run.py swebench --evaluate swebench_output/openclaw.pilot_astropy.jsonl --run-id pilot_astropy
python run.py compare --run-id pilot_astropy
python run.py visualize --output results/
```

## 命令速查

| 命令 | 做什么 |
|------|--------|
| `python run.py smoke` | 冒烟测试（API + 数据集 + Docker） |
| `python run.py swebench --agent NAME --limit 20 --runtime-profile memory-enabled --rounds 5 --run-id pilot_mem5` | Phase 1 pilot：20 题 × 5 轮记忆测试 |
| `python run.py swebench --agent openclaw --mode workspace ...` | OpenClaw 在真实 repo workspace 中改文件 |
| `python run.py swebench --agent NAME --mode repo-mentioned ...` | 机器上有本地项目目录，但 agent 需要自己发现并进入 |
| `python run.py swebench ... --auto-evaluate` | 跑完自动调用 SWE-bench harness 评测 |
| `python run.py swebench ... --runtime-profile memory-enabled --rounds 5` | 隔离 state、清空开局 memory、连续观察第 1-5 轮表现 |
| `python run.py swebench --evaluate FILE` | 评测已有预测文件 |
| `python run.py curve --run-id RUN_ID` | 绘制多轮记忆曲线（Direction B 核心分析） |
| `python run.py compare` | 统计摘要 |
| `python run.py visualize --output results/` | 生成图表 |
| ⚠ `python run.py memory --agent NAME --quick-test` | **已废弃**：MemoryAgentBench quick-test，仅保留作冒烟 |

`--agent` 可选: `openclaw`, `hermes`, `claude-code`, `all`

## 研究级路径

正式研究结果请优先使用 OpenClaw 的 `workspace` 模式，而不是 text-only patch 生成。

- `workspace` 模式会为每个 SWE-bench instance materialize 一个本地 git 工作区，并从真实 `git diff` 导出 patch
- 默认走 `native` 提问风格：只给一个接近真实用户提问的 issue 文本，不额外加行为约束或 hints
- `repo-mentioned` 模式更接近裸用户提问：机器上已有一个本地项目目录，但 agent 不是从 repo 根目录起跑，只会在 prompt 里被告知“这台机器上有个项目文件夹叫 xxx”
- `memory-enabled` runtime 协议会给每个 agent 和 task 分配隔离 state 目录：第 1 轮前清空 memory，后续轮次复用同一 state，但不会碰默认 `~/.openclaw` / `~/.hermes` / `~/.claude`
- `claude-code` 在 Kimi 后端下会以 `--thinking disabled` 运行，避免 OpenAI shim 返回缺失 `reasoning_content` 时污染评测结果
- token / tool trace 以 OpenClaw session telemetry 为准，结果会落到 `data/raw/`
- 具体流程见 [protocols/research_protocol.md](protocols/research_protocol.md)

## Token 效率计量

Token 统计自动嵌入 SWE-bench 流程：每次 agent 调用都记录 tokens_in/out/total，
保存到 `data/raw/`。分析时用 TEFS (Token Efficiency Function Score) 指标：

    TEFS = task_score / (tokens_total / 1000)

即"每消耗 1k token 获得的任务得分"，越高越好。
