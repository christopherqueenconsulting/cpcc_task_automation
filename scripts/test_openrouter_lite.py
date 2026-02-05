#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Lite OpenRouter test using OPENROUTER_API_KEY from .env.

Usage:
    python3 scripts/test_openrouter_lite.py

The script loads a local .env file if present, then calls OpenRouter's
OpenAI-compatible endpoint with the model "openrouter/auto".
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from openai import AsyncOpenAI


def _load_dotenv(dotenv_path: Path) -> None:
    """Load a simple .env file into environment variables.

    This parser supports KEY=VALUE lines and ignores comments/blank lines.
    It does not override existing environment variables.
    """
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


async def _run_test() -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is not set. Add it to .env or the environment.")

    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "X-Title": "CPCC Task Automation",
            "HTTP-Referer": "https://github.com/gitchrisqueen/cpcc_task_automation",
        },
    )

    response = await client.chat.completions.create(
        model="openrouter/auto",
        messages=[{"role": "user", "content": "Return JSON: {\"ok\": true}"}],
    )

    choice = response.choices[0].message.content if response.choices else None
    print(f"model_used: {response.model}")
    print(f"content: {choice}")


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    _load_dotenv(repo_root / ".env")
    asyncio.run(_run_test())


if __name__ == "__main__":
    main()
