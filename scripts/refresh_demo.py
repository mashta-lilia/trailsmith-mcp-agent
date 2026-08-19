"""Prepare the demo for today: re-record fixtures, shift dates, rebuild scenario.

Run this the morning of a defence. It performs, in order:

  1. shifts every date in demo/*.json so day 1 is tomorrow (inside the forecast
     window) - unless --keep-dates is given;
  2. re-records the genuine OpenWeather fixtures (needs OWM_API_KEY);
  3. regenerates fixtures/scenario_storm/ from those fresh recordings, pinning
     the synthetic thunderstorm to the new day-2 date;
  4. prints the exact commands for each demo.

Usage:
    python scripts/refresh_demo.py
    python scripts/refresh_demo.py --start 2026-09-03   # explicit day-1 date
    python scripts/refresh_demo.py --keep-dates         # only re-record
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DEMO_DIR = REPO_ROOT / "demo"
GENUINE = REPO_ROOT / "fixtures" / "openweather"
SCENARIO = REPO_ROOT / "fixtures" / "scenario_storm"
STORM_CONDITIONS = "Conditions:  Thunderstorm thunderstorm with heavy rain"


def shift_dates(start: date) -> dict[str, list[str]]:
    """Rewrite every demo file so day 1 falls on `start`, keeping day spacing."""
    changed: dict[str, list[str]] = {}
    for path in sorted(DEMO_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        days = payload["itinerary"]["days"]
        for offset, day in enumerate(days):
            day["date"] = (start + timedelta(days=offset)).isoformat()
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        changed[path.name] = [d["date"] for d in days]
    return changed


def rebuild_storm_scenario(storm_date: str) -> None:
    SCENARIO.mkdir(parents=True, exist_ok=True)
    for src in sorted(GENUINE.glob("*.txt")):
        out, current = [], None
        for line in src.read_text(encoding="utf-8").splitlines():
            match = re.match(r"Date & Time:\s*(\d{4}-\d{2}-\d{2})", line)
            if match:
                current = match.group(1)
            if current == storm_date and line.strip().startswith("Conditions:"):
                line = STORM_CONDITIONS
            out.append(line)
        text = "\n".join(out) + "\n"
        (SCENARIO / src.name).write_text(text, encoding="utf-8")
        print(f"    {src.name}: {text.count('Thunderstorm')} storm entries")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", help="ISO date for day 1 (default: tomorrow)")
    parser.add_argument("--keep-dates", action="store_true",
                        help="do not touch demo dates, only re-record fixtures")
    args = parser.parse_args()

    if args.keep_dates:
        storm_date = json.loads(
            (DEMO_DIR / "itinerary_storm.json").read_text(encoding="utf-8")
        )["itinerary"]["days"][1]["date"]
        print(f"[1/3] Keeping existing demo dates (storm day: {storm_date})")
    else:
        start = date.fromisoformat(args.start) if args.start else \
            date.today() + timedelta(days=1)
        print(f"[1/3] Shifting demo dates so day 1 = {start.isoformat()}")
        for name, dates in shift_dates(start).items():
            print(f"    {name}: {dates[0]} .. {dates[-1]}")
        storm_date = (start + timedelta(days=1)).isoformat()

    print("\n[2/3] Re-recording genuine fixtures from the live API")
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "fetch_fixtures.py")],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    print("    " + "\n    ".join(proc.stdout.strip().splitlines() or ["(no output)"]))
    if proc.returncode != 0:
        print("    FAILED - is OWM_API_KEY set and active?", file=sys.stderr)
        print("    " + proc.stderr.strip()[:400], file=sys.stderr)
        return 1

    print(f"\n[3/3] Rebuilding storm scenario, thunderstorm pinned to {storm_date}")
    rebuild_storm_scenario(storm_date)

    # Confirm the parser can actually read the new day-2 storm.
    from agent.weather_parser import WeatherParseError, parse_weather_text
    ok = True
    for fixture in sorted(SCENARIO.glob("*.txt")):
        if "nowhere" in fixture.name:
            continue
        try:
            summary = parse_weather_text(
                fixture.read_text(encoding="utf-8"), storm_date)
            if not summary.thunderstorm:
                print(f"    WARNING {fixture.name}: no thunderstorm parsed")
                ok = False
        except WeatherParseError as exc:
            print(f"    WARNING {fixture.name}: {exc.code}")
            ok = False
    print("    storm parses on every settlement" if ok
          else "    storm scenario is NOT usable - check the dates above")

    print("\nReady. Demo commands (PowerShell):\n")
    print("  # 1. custom server alone, in its own terminal")
    print("  .venv\\Scripts\\python -m trailsmith_mcp\n")
    print("  # 2. proof of both connections, no credentials needed")
    print("  .venv\\Scripts\\python scripts\\verify.py\n")
    print("  # 3. clean run against the live API")
    print("  Remove-Item Env:\\REPLAY -ErrorAction SilentlyContinue")
    print("  .venv\\Scripts\\python -m agent.runner demo\\itinerary_clean.json\n")
    print("  # 4. storm run -> replanning fires")
    print('  $env:REPLAY=1; $env:FIXTURE_SET="scenario_storm"')
    print("  .venv\\Scripts\\python -m agent.runner demo\\itinerary_storm.json\n")
    print("  # 5. invalid input")
    print("  .venv\\Scripts\\python -m agent.runner demo\\itinerary_invalid.json\n")
    print("  # 6. failure demo: set OWM_API_KEY=broken in .env, clear REPLAY, rerun #3")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
