"""TrailSmith agent runner.

Usage: python -m agent.runner demo/itinerary_clean.json
Set REPLAY=1 in .env (or the environment) to serve recorded weather fixtures.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

from .orchestrator import build_options  # noqa: E402

START = time.monotonic()


def log(line: str) -> None:
    print(f"[{time.monotonic() - START:7.1f}s] {line}", flush=True)


def build_prompt(request: dict) -> str:
    return (
        "Plan and check this hike. The structured request follows as JSON:\n\n"
        "```json\n" + json.dumps(request, indent=2) + "\n```"
    )


async def run(request: dict, replay: bool) -> None:
    options = build_options(replay=replay)
    log(f"Starting agent (replay={'on' if replay else 'off'})")
    async for message in query(prompt=build_prompt(request), options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    brief = json.dumps(block.input)[:160]
                    log(f"TOOL CALL {block.name} {brief}")
                elif isinstance(block, TextBlock) and block.text.strip():
                    log(f"AGENT: {block.text.strip()}")
        elif isinstance(message, ResultMessage):
            log("--- FINAL RESULT ---")
            print(message.result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the TrailSmith agent")
    parser.add_argument("request_file", help="Path to a demo itinerary JSON file")
    args = parser.parse_args()

    request_path = Path(args.request_file)
    if not request_path.exists():
        sys.exit(f"Request file not found: {request_path}")
    request = json.loads(request_path.read_text(encoding="utf-8"))

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("NOTE: ANTHROPIC_API_KEY is not set; relying on an existing "
              "Claude Code CLI login. If the run fails to authenticate, copy "
              ".env.example to .env and fill it in.")

    replay = os.environ.get("REPLAY", "0") == "1"
    asyncio.run(run(request, replay))


if __name__ == "__main__":
    main()
