"""
MemoryAgentBench Adapter — 把三个 agent 接入 ICLR 2026 记忆评测。

MemoryAgentBench 评测 4 项核心能力:
    1. Accurate Retrieval (AR) — 准确召回
    2. Test-Time Learning (TTL) — 运行时学习
    3. Long-Range Understanding (LRU) — 长程理解
    4. Conflict Resolution (CR) — 矛盾检测

本 adapter 提供两种用法:
    1. 生成 MemoryAgentBench 兼容的 agent config，然后调用其 main.py
    2. 独立运行简化版记忆测试（不依赖完整 MemoryAgentBench 环境）

用法:
    python -m adapters.memory_adapter --agent openclaw --generate-config
    python -m adapters.memory_adapter --agent all --quick-test
"""

import argparse
import json
import time
from pathlib import Path

import yaml

AGENTS = ["openclaw", "hermes", "claude-code"]
CONFIGS_DIR = Path(__file__).resolve().parent.parent / "vendors" / "memory-agent-bench" / "configs" / "model_conf"
RESULTS_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


def generate_agent_config(agent_name: str) -> Path:
    """
    为 MemoryAgentBench 生成 agent config YAML。

    三个 agent 都走同一个 Kimi API，但 agent_name 不同，
    这样 MemoryAgentBench 可以区分结果。
    """
    config = {
        "agent_type": "Long_context_agent",
        "model_name": "kimi-for-coding",
        "api_base": "https://api.kimi.com/coding/v1",
        "max_tokens": 4096,
        "temperature": 0.0,
        "display_name": agent_name,
        # MemoryAgentBench 从环境变量读 API key (OPENAI_API_KEY)
    }

    CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    config_path = CONFIGS_DIR / f"{agent_name}.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)

    print(f"  config written: {config_path}")
    return config_path


# ── 快速记忆测试（独立于 MemoryAgentBench）──────────────────────────

def _build_filler_messages(count: int) -> list[dict]:
    """生成填充用的无关对话。"""
    msgs = []
    for i in range(count):
        msgs.append({"role": "user", "content": f"unrelated topic {i+1}: discuss the weather today."})
        msgs.append({"role": "assistant", "content": f"Sure, about topic {i+1}..."})
    return msgs


QUICK_MEMORY_TESTS = [
    {
        "id": "ar-1",
        "category": "accurate_retrieval",
        "description": "单轮事实召回",
        "setup_messages": [
            {"role": "user", "content": "记住以下项目信息：项目名=Nexus，技术栈=Rust+PostgreSQL，负责人=张明，截止日期=2026-06-30，部署环境=AWS EKS。"},
            {"role": "assistant", "content": "好的，我已记住这些项目信息。"},
            {"role": "user", "content": "帮我写一个快速排序算法。"},
            {"role": "assistant", "content": "这是一个快速排序实现..."},
            {"role": "user", "content": "HTTP 状态码 418 是什么意思？"},
            {"role": "assistant", "content": "418 I'm a teapot..."},
            {"role": "user", "content": "解释一下 CAP 定理。"},
            {"role": "assistant", "content": "CAP 定理是分布式系统的基础理论..."},
            {"role": "user", "content": "Python 的 GIL 是什么？"},
            {"role": "assistant", "content": "GIL (Global Interpreter Lock) 是..."},
        ],
        "query": "请回忆一下之前我告诉你的项目信息，包括项目名、技术栈、负责人、截止日期和部署环境。",
        "expected_facts": ["Nexus", "Rust", "PostgreSQL", "张明", "2026-06-30", "AWS EKS"],
    },
    {
        "id": "cr-1",
        "category": "conflict_resolution",
        "description": "矛盾信息检测",
        "setup_messages": [
            {"role": "user", "content": "我们的数据库选型已经确定了，用 PostgreSQL。"},
            {"role": "assistant", "content": "好的，已记录数据库选型为 PostgreSQL。"},
            {"role": "user", "content": "对了，我们的主数据库是 MongoDB，查询性能不错。"},
        ],
        "query": None,  # 最后一条 setup 消息本身就是测试
        "check": "contradiction_detected",
        "contradicting_facts": ["PostgreSQL", "MongoDB"],
    },
    {
        "id": "lru-1",
        "category": "long_range_understanding",
        "description": "长上下文信息利用",
        "setup_messages": [
            {"role": "user", "content": "重要：API 的认证方式统一用 JWT，token 过期时间设为 7200 秒。"},
            {"role": "assistant", "content": "明白了。"},
        ] + _build_filler_messages(15),
        "query": "我之前说的 API 认证方式和 token 过期时间分别是什么？",
        "expected_facts": ["JWT", "7200"],
    },
]


def score_retrieval(response: str, expected_facts: list[str]) -> dict:
    """评分：事实召回准确率。"""
    found = sum(1 for fact in expected_facts if fact.lower() in response.lower())
    total = len(expected_facts)
    return {
        "recall_accuracy": found / total if total else 0,
        "facts_found": found,
        "facts_total": total,
    }


def score_contradiction(response: str, contradicting_facts: list[str]) -> dict:
    """评分：矛盾检测能力。"""
    response_lower = response.lower()
    mentioned_both = all(f.lower() in response_lower for f in contradicting_facts)
    contradiction_keywords = [
        "矛盾", "冲突", "不一致", "之前", "但是", "然而",
        "contradict", "conflict", "inconsisten", "however", "but",
    ]
    proactive = any(kw in response_lower for kw in contradiction_keywords)

    return {
        "detected": mentioned_both and proactive,
        "proactive": proactive,
        "mentioned_both_facts": mentioned_both,
    }


def _flatten_messages_to_prompt(messages: list[dict]) -> str:
    """将多轮对话扁平化为单条 prompt（用于 CLI 模式）。"""
    parts = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if role == "user":
            parts.append(f"[User]: {content}")
        elif role == "assistant":
            parts.append(f"[Assistant]: {content}")
    return "\n".join(parts)


def run_quick_test(agent_name: str, *, mode: str = "api") -> list[dict]:
    """
    运行快速记忆测试。

    mode="api" → 直接调用 Kimi API（绕过 agent 框架，用于验证 baseline）
    mode="cli" → 通过 agent CLI 调用（测试 agent 框架的实际表现）
    """
    from adapters.agent_runner import call_kimi_api, run_agent_cli

    results = []
    for test in QUICK_MEMORY_TESTS:
        print(f"  [{test['id']}] {test['description']} ({mode}) ...", end=" ", flush=True)

        messages = list(test["setup_messages"])
        if test.get("query"):
            messages.append({"role": "user", "content": test["query"]})

        if mode == "cli":
            # CLI 模式：将多轮对话扁平化为单条 prompt
            prompt = _flatten_messages_to_prompt(messages)
            prompt += "\n\n请根据上述对话内容回答最后一个问题。"
            result = run_agent_cli(agent_name, prompt, timeout=120)
        else:
            result = call_kimi_api(messages, max_tokens=2048)

        # 评分
        if test["category"] == "conflict_resolution":
            scores = score_contradiction(result.response, test["contradicting_facts"])
        else:
            scores = score_retrieval(result.response, test["expected_facts"])

        record = {
            "run_id": f"mem_{test['id']}_{agent_name}_{int(time.time())}",
            "agent": agent_name,
            "task_id": test["id"],
            "dimension": "memory",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "metrics": {
                **scores,
                "tokens_in": result.tokens_in,
                "tokens_out": result.tokens_out,
                "tokens_total": result.tokens_total,
                "latency_s": result.latency_s,
            },
            "notes": test["description"],
        }
        results.append(record)

        # 保存到 data/raw/
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        with open(RESULTS_DIR / f"{record['run_id']}.json", "w") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)

        status = "ok" if scores.get("recall_accuracy", 0) > 0.5 or scores.get("detected") else "fail"
        print(f"{status} {scores}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Memory Benchmark Adapter")
    parser.add_argument("--agent", choices=AGENTS + ["all"], required=True)
    parser.add_argument(
        "--generate-config",
        action="store_true",
        help="生成 MemoryAgentBench 兼容的 agent config",
    )
    parser.add_argument(
        "--quick-test",
        action="store_true",
        help="运行快速记忆测试（不依赖 MemoryAgentBench 完整环境）",
    )
    parser.add_argument(
        "--mode",
        choices=["api", "cli"],
        default="cli",
        help="测试模式: api=直接调 API, cli=通过 agent CLI（默认 cli）",
    )
    args = parser.parse_args()

    agents_to_run = AGENTS if args.agent == "all" else [args.agent]

    if args.generate_config:
        print("Generating MemoryAgentBench configs...")
        for agent in agents_to_run:
            generate_agent_config(agent)
        print("\nNext steps:")
        print("  cd vendors/memory-agent-bench")
        print(f"  OPENAI_API_KEY=$KIMI_API_KEY python main.py \\")
        print(f"    --agent_config configs/model_conf/{agents_to_run[0]}.yaml \\")
        print(f"    --dataset_config configs/data_conf/YOUR_DATASET.yaml")
        return

    if args.quick_test:
        for agent in agents_to_run:
            print(f"\n{'='*50}")
            print(f"Agent: {agent} (mode={args.mode})")
            print(f"{'='*50}")
            run_quick_test(agent, mode=args.mode)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
