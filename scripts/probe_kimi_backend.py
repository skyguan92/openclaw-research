"""Probe the Kimi /models endpoint to record the actual backend model.

Kimi's `kimi-for-coding` alias silently points to different underlying models
over time. For reproducibility we capture the full /models payload (id +
display_name + context_length + capabilities) at the start of each run and
write it alongside the result so post-hoc analysis knows exactly which
backend served the traffic.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests


def probe(base_url: str | None = None, api_key: str | None = None) -> dict:
    base_url = base_url or os.environ["KIMI_BASE_URL"].rstrip("/")
    api_key = api_key or os.environ["KIMI_API_KEY"]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": os.environ.get("KIMI_CLIENT_HEADER", "claude-code/2.1.5"),
    }
    resp = requests.get(f"{base_url}/models", headers=headers, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    return {
        "probed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_url": base_url,
        "models": payload.get("data", []),
    }


def main() -> int:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    info = probe()
    rendered = json.dumps(info, indent=2, ensure_ascii=False)
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
