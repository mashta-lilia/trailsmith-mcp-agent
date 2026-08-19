# TrailSmith documentation

Four kinds of document, kept separate on purpose — each answers a different
question.

## Learning: get it running

**[Quickstart](quickstart.md)** — clone to a working workflow in ~10 minutes.
Starts with the parts that need no API keys, then adds live weather and the agent.
Every command shows its real output.

## Understanding: how and why it is built this way

**[Architecture](architecture.md)** — process boundaries, the workflow diagram,
where each tool result is consumed, the value trace from forecast text to routing
decision, and trust boundaries.

**[Design rationale](design_rationale.md)** — why the existing server is
relevant, why each custom tool belongs at the MCP boundary, the subagent design,
which fixture set satisfies which requirement, and the honest limitations
(keyword-estimated precipitation, one wind value per run, coarse daylight model,
bounded route search).

## Reference: exact contracts

**[Tool contracts](tool_contracts.md)** — all four custom tools with purpose,
model-facing description, input and output schemas, error conditions, side
effects and a worked example; plus the observed contract of the OpenWeather
`weather` tool and the agent-side parser helper.

**[Dataset provenance](../data/PROVENANCE.md)** — where the trail data came from,
how `nearest_settlement` is assigned, the generated schema, and how to regenerate
it deterministically.

**[Storm scenario fixtures](../fixtures/scenario_storm/README.md)** — what the
synthetic input is, how it was derived, and why it is an input rather than a
canned answer.

## Doing a specific task

**[Troubleshooting](troubleshooting.md)** — every failure mode observed while
building this, with its exact symptom and fix. Start here when something behaves
differently from the quickstart.

**[Defence script](defence_script.md)** — the timed 10–15 minute demonstration
sequence, the prepared variations, and the points to disclose proactively.

---

## Reading order

| If you are… | Read |
|---|---|
| Running this for the first time | [Quickstart](quickstart.md) |
| Reviewing the design | [Architecture](architecture.md) → [Design rationale](design_rationale.md) |
| Calling the tools | [Tool contracts](tool_contracts.md) |
| Presenting it | [Defence script](defence_script.md) |
| Debugging | [Troubleshooting](troubleshooting.md) |

## Verifying the docs

The commands and outputs in the quickstart were captured from real runs. To
re-verify after a change:

```powershell
.venv\Scripts\python scripts\verify.py    # 12 checks, no credentials needed
.venv\Scripts\python scripts\walkthrough.py demo\itinerary_storm.json --fixtures scenario_storm
```
