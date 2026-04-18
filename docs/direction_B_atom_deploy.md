# Atom (GB10) 部署指南 · Direction B Phase 1

**状态**：3 runtime 冒烟通过（openclaw + hermes + openclaude 全部返回 PONG）
**更新**：2026-04-18
**机器**：`aitopatom-070b` (Tailscale 100.105.58.16) — NVIDIA GB10 / aarch64 / Ubuntu 24.04

---

## 0 · 连接信息

```bash
ssh qujing@100.105.58.16           # 或 aitopatom-070b.tail94228b.ts.net
# sudo 需要密码（不存本文档）
```

**共存生产服务**（**不要停**）：
- `voxcpm2-voxcpm2-nanovllm` (container)
- `voxcpm2_svc` (container, uvicorn 8010，占 52 GB GPU)
- `qwen3-asr` (container, 8000 port)

这 3 个 container 都是 `restart_policy=unless-stopped`，`systemctl restart docker` 会自动恢复。

---

## 1 · 一次性系统配置（本部署已完成）

### 1.1 · QEMU binfmt（x86_64 模拟）

不是 Phase 1 必需（arm64 image 可用），但装了方便将来：

```bash
docker run --privileged --rm tonistiigi/binfmt:latest --install amd64
```

### 1.2 · Docker 走 mihomo 代理（拉境外 image）

`/etc/systemd/system/docker.service.d/http-proxy.conf`:
```
[Service]
Environment="HTTP_PROXY=http://127.0.0.1:7890"
Environment="HTTPS_PROXY=http://127.0.0.1:7890"
Environment="NO_PROXY=localhost,127.0.0.1,172.17.0.0/16,10.0.0.0/8,192.168.0.0/16,100.64.0.0/10,docker.m.daocloud.io,mirror.baidubce.com"
```

然后 `sudo systemctl daemon-reload && sudo systemctl restart docker`。

**验证**：`docker pull swebench/sweb.eval.arm64.astropy_1776_astropy-12907:latest` 能拉（6 min 首次）。

---

## 2 · Phase 1 Workspace 布局

```
/home/qujing/phase1-workspace/
├── openclaw-research/           # git clone + .venv
│   ├── .venv/                   # uv venv (Python 3.12.3)
│   ├── adapters/
│   ├── benchmarks/
│   ├── data/
│   ├── docs/
│   ├── scripts/
│   └── ...
├── .hf_cache/                   # HF_HOME (因为 ~/.cache/huggingface 被 root 占)
└── (未来) snapshots/            # §9.1 snapshot tree
```

**启动模板**：
```bash
ssh qujing@100.105.58.16
cd ~/phase1-workspace/openclaw-research
source .venv/bin/activate
export HF_HOME=/home/qujing/phase1-workspace/.hf_cache
export KIMI_API_KEY="..."  # 从 .env 或手设
```

---

## 3 · Runtime 安装状态

| Runtime | 状态 | 路径 | 验证命令 |
|---|---|---|---|
| openclaw | ✅ 2026.4.15 (041266a) | `~/.nvm/versions/node/v22.22.0/bin/openclaw` | `openclaw --version` |
| hermes | ✅ 0.10.0 (2026.4.16) | `~/.hermes/hermes-agent/.venv/bin/hermes` | 源：`github.com/NousResearch/hermes-agent`，Python 3.11 venv |
| openclaude | ✅ 0.1.8 (Open Claude) | `~/.bun/bin/bun ~/projects/openclaude/dist/cli.mjs` | `dist/` rsync from 本机 mac；`bun install` 补 node_modules (456 pkgs) |

**ping-pong 实测延迟**（见 §7.3）：openclaw 首次 26.8s（MCP bootstrap 除尽后 ~2s）/ hermes 2.4s / openclaude 2.2s。

---

## 3.1 · Kimi 凭据与后台模型

`.env` 在 `~/phase1-workspace/openclaw-research/.env`（`.gitignore` 已覆盖，权限 600）：
```
KIMI_BASE_URL=https://api.kimi.com/coding/v1
KIMI_API_KEY=sk-kimi-...  # 见密钥管理；不写入本文档
KIMI_MODEL=kimi-for-coding
KIMI_CLIENT_HEADER=claude-code/2.1.5
```

**后台实际模型**：Kimi `/models` 端点返回 `display_name: "k2.6"`，`context_length: 262144`，`supports_reasoning: true`。
`kimi-for-coding` 只是 alias，真正服务的是 K2.6。

每次实验开跑前用 `scripts/probe_kimi_backend.py <out.json>` 把上面这段固化到 run metadata 里，
以便 Kimi 静默切换后台时我们能从数据里看出来。

---

## 3.2 · openclaw 配置修正

Atom 上原有的 `~/.openclaw/openclaw.json` 遗留了旧 gateway 的 `channels.feishu.accounts.default`
（与当前 schema 不符，会导致 `config validate` 失败），以及指向 `/home/qujing/aima-serve` 的 MCP server
（在 benchmark 的 isolated state 里也会被继承，首次调用耗时 5-10 min）。

处理：
1. **清掉坏 feishu**：`~/.openclaw/openclaw.json.bak-pre-phase1` 已保留备份，正本删除 `channels.feishu`。
2. **注入 kimi provider**：向 `models.providers.kimi` 写入 `baseUrl=https://api.kimi.com/coding/v1`、
   `api=openai-completions`、`authHeader=true`、`headers.User-Agent=claude-code/2.1.5`、
   models 列表包含 `kimi-for-coding`（`contextWindow=262144`，`reasoning=true`）。
3. **benchmark 隔离**：`adapters/runtime_state.py::_strip_openclaw_host_extensions` 在 seed 隔离状态时
   自动剥掉 `mcp` / `plugins` / `channels`，保证 phase1 run 不会继承 host 上的 AIMA MCP / 聊天集成。

---

## 4 · Harness 性能基线（arm64 GB10）

在 Phase 1a 启动前做过的 gold-patch 基准：

| 测量 | 数值 |
|---|---|
| Docker image pull (arm64 via proxy) | 6 min 首次 / 后续秒级 |
| container 启停 baseline | 0.233 s |
| pytest import baseline | 1.5 s |
| **完整 SWE-bench eval (astropy-12907 gold)** | **908 s (~15 min)** |
| Resolved | 1/1 ✓ |

**Phase 1 时长修正（基于 15 min/run）**：
- Phase 1a pilot: 60 run × 15 min = 15 h（单机串行）
- Phase 1c full: 285 run × 15 min = 71 h（串行）/ **~24 h 3-worker 并行**
- Cost 不变（~$28 Kimi tokens）

---

## 5 · 已知约束

1. **磁盘紧**：916 GB 盘 741 已用 (86%)。docker 占 ~300 GB，其中 247 可回收但里面可能有生产 image——**不要随手 `docker system prune -a`**。
2. **RAM 紧**：119 GB 总，生产服务 + VLLM 占 105 GB；Phase 1 可用 ~14 GB。astropy 这类 pytest 单 instance 峰值 < 2 GB，3 worker 并行 OK。
3. **sudo 需要密码**——自动化脚本不能假设免密 sudo。
4. **网络走 mihomo**：Docker daemon 已配；若 Python 需要访问境外（HF、GitHub），venv 内的 `HTTPS_PROXY=http://127.0.0.1:7890` 已生效（从 shell 继承）。

---

## 6 · 后续 TODO（按顺序）

- [x] 装 hermes（`git clone github.com/NousResearch/hermes-agent` + `uv venv --python 3.11` + `uv pip install -e .`）
- [x] 装 openclaude + bun（`curl -fsSL https://bun.sh/install | bash`；dist rsync；`bun install`）
- [x] 设 `KIMI_API_KEY`（`~/phase1-workspace/openclaw-research/.env`）
- [x] 3 runtime ping-pong 通过
- [ ] 跑 `python run.py swebench --agent all --instance-ids astropy__astropy-12907 --run-id atom_pilot` — 3 runtime 端到端 SWE-bench smoke
- [ ] 修 openclaw token=0 的 telemetry 问题（见 #27）
- [ ] 实装 §9 工程 prerequisites（snapshot save/restore、memory-event logger 等）
- [ ] 跑 Phase 1a pilot（20 instance × 3 runtime memory-off = 60 run，选出 ≤60% pass 的难题）
