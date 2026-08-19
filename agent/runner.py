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

from claude_agent_sdk import ClaudeSDKError  # noqa: E402

from .orchestrator import build_options  # noqa: E402

START = time.monotonic()

# The final report may contain non-ASCII (settlement names, degree signs);
# Windows consoles often default to a legacy codepage.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def log(line: str) -> None:
    print(f"[{time.monotonic() - START:7.1f}s] {line}", flush=True)


def build_prompt(request: dict) -> str:
    return (
        "Plan and check this hike. The structured request follows as JSON:\n\n"
        "```json\n" + json.dumps(request, indent=2) + "\n```"
    )


async def run(request: dict, replay: bool) -> None:
    options = build_options(replay=replay)
    fixture_set = os.environ.get("FIXTURE_SET", "openweather")
    mode = f"replay from fixtures/{fixture_set}" if replay else "live OpenWeather API"
    log(f"Starting agent ({mode})")
    log("MCP connections: 'weather' (existing, separate process) | "
        "'trailsmith' (custom, separate process) | "
        "'agent_local' (in-process agent helper, not part of the custom server)")
    try:
        await _drive(build_prompt(request), options)
    except ClaudeSDKError as exc:
        # One readable line instead of a chained 40-line traceback.
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
        log(f"AGENT FAILED: {first}")
        if "authenticate" in first.lower() or "oauth" in first.lower():
            log("Hint: run `claude /login`, or set ANTHROPIC_API_KEY in .env")
        raise SystemExit(1)


async def _drive(prompt: str, options) -> None:
    # Subagent completions also arrive as ResultMessage, so keep only the last
    # one and print the report once at the end instead of on every completion.
    final: ResultMessage | None = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    brief = json.dumps(block.input)[:160]
                    log(f"TOOL CALL {block.name} {brief}")
                elif isinstance(block, TextBlock) and block.text.strip():
                    # Progress preview only; the full text is printed once below.
                    preview = " ".join(block.text.strip().split())
                    log(f"AGENT: {preview[:200]}{'...' if len(preview) > 200 else ''}")
        elif isinstance(message, ResultMessage):
            final = message

    if final is None:
        log("Run ended with no result message.")
        return

    cost = final.total_cost_usd
    cost_text = f"${cost:.4f}" if cost is not None else "n/a"
    log(f"--- FINAL REPORT (subtype={final.subtype}, "
        f"turns={final.num_turns}, cost={cost_text}) ---")
    if final.subtype != "success":
        log(f"RUN STOPPED EARLY: {final.subtype} - budget or turn cap reached, "
            "this is not a completed model answer.")
    print(final.result)


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
