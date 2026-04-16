# 研究级实验协议

## 目标
把这个仓库从“能跑 demo”提升到“能产出可复现、可辩护的 agent benchmark 结果”。

## 核心原则
- 比较的是 agent scaffold，不是底层模型本身。除非明确更换模型，否则所有 agent 都应固定到同一后端模型。
- SWE-bench 必须使用真实本地代码工作区，不接受 text-only patch generation 作为正式结果。
- 正式实验默认使用 `native prompt`：只给接近真实用户的任务描述，不加额外操作规则、策略引导或 benchmark hints。
- 每一轮实验都必须有稳定 `run_id`，并完整记录环境、代码版本、模型版本、超时和预算。

## 场景划分
- `workspace`: 预先提供目标 repo 的本地工作区；适合和 SWE-bench harness 对齐
- `repo-mentioned`: 机器上已有一个本地项目目录，但 agent 不是从 repo 根目录起跑；prompt 只说明“这台机器上有个项目文件夹叫 xxx”，更接近真实用户提问，也会额外测到“代码发现/环境定位”能力

## Runtime 协议
- `default`: 沿用当前最小可运行封装，适合快速验证
- `memory-enabled`: 为每个 `agent × task` 创建隔离 runtime state；第 1 轮前清空 memory/session，之后按第 2/3/4/5 轮连续复用同一 state
- `memory-enabled` 的状态目录位于 `data/runtime_state/`，不会清理或覆盖默认 `~/.openclaw`、`~/.hermes`、`~/.claude`
- `claude-code` 在 Kimi OpenAI-compatible 后端下固定使用 `--thinking disabled`，这是 provider 兼容设置，不影响隔离 memory/session 的实验定义

## 推荐实验设计

### Phase 1: Pilot
- 数据集: `princeton-nlp/SWE-bench_Verified`
- 样本量: 20 题
- 模式: `openclaw --mode workspace`
- Prompt: native
- 目标: 验证 runner、token telemetry、evaluation import、分析脚本全链路稳定

### Phase 2: Main Study
- 样本量: 100 题起步；如果资源允许再扩到 Verified 全量
- 每个配置至少重复 3 次，避免单次随机波动
- 固定同一模型、同一 timeout、同一预算、同一宿主机和 Docker 版本

### Phase 3: Case Study
- 对成功样本选 3-5 个代表性实例
- 对失败样本做错误分类：理解错误、搜索路径错误、验证失败、环境依赖失败、过度编辑

## 必须固定的控制变量
- `openclaw` 版本
- 底层模型 ID
- 数据集 split 和 instance 列表
- `run_id`
- 宿主机信息和 Python 版本
- Docker 版本
- timeout
- 是否允许环境安装
- prompt 模板

## 必须保留的原始产物
- `swebench_output/<agent>.<run_id>.jsonl`
- `data/raw/` 下的 token 记录和 success 记录
- `vendors/swe-bench` 的 evaluation summary
- 代表性样本的 workspace diff 和 session log 路径

## 推荐命令

```bash
# 1. 冒烟
python run.py smoke

# 2. 单题 workspace 验证
python run.py swebench \
  --agent openclaw \
  --mode workspace \
  --instance-ids astropy__astropy-12907 \
  --run-id pilot_astropy

# 3. 导入 SWE-bench harness 结果
python run.py swebench \
  --evaluate swebench_output/openclaw.pilot_astropy.jsonl \
  --run-id pilot_astropy

# 4. 聚合分析
python run.py compare --run-id pilot_astropy
python run.py visualize --output results/

# 5. memory-enabled runtime，多轮观察同一 case
python run.py swebench \
  --agent all \
  --mode repo-mentioned \
  --instance-ids astropy__astropy-12907 \
  --run-id repo_mentioned_mem5 \
  --runtime-profile memory-enabled \
  --rounds 5 \
  --timeout 0
```

## 结果发布前检查
- task success 来自 harness，不来自人工主观判定
- token 指标来自 session/CLI telemetry，不是估算值
- 分析图表只比较同一实验批次（同一 `run_id` 或同一 protocol）
- 明确说明当前结论适用于“OpenClaw + 固定模型 + native prompt + 固定工具环境”

## Prompt 约定
- `native`: 用户消息只包含“请在当前仓库修复这个问题”加原始 issue 文本
- `repo-mentioned`: 用户消息只包含 repo 名加原始 issue 文本，不额外提供本地代码
- `guided`: 仅允许用于调试 runner，不进入正式结论

## 当前仓库状态
- OpenClaw 已支持真实 workspace 模式，可直接在本地 git 工作区改文件并导出 patch
- Memory quick test 适合冒烟，不适合直接作为正式论文结论
- Hermes / Claude Code 目前仍缺少与 OpenClaw 等价的 repo-in-the-loop runner；在补齐前，不应对外宣称公平横评
