# Observable agent tool runtime

An AI-assisted demonstration project prepared for Andrey Zhdanov's portfolio.
It is a reference implementation, not a claim of previous commercial work.

The runtime accepts structured tool calls and provides an explicit tool
allowlist, argument validation, bounded execution, a SQLite success journal,
input fingerprints and metadata-only execution events.

## Run

Requires Python 3.11 or newer. No third-party packages or network access needed.

```bash
python3 agent_runtime.py
python3 -m unittest -v
```

The example calculates a numeric summary, normalizes text and replays a call.
The repeated call returns its persisted result. Running the example again
demonstrates recovery across process restarts.

## Design choices

- A call ID is tied to the canonical tool name and arguments. Reusing an ID
  with different inputs raises an explicit conflict instead of returning
  an unrelated cached result.
- Successful results are persisted. Failures and timeouts are observable
  but do not become successful checkpoints.
- Calls with the same ID are serialized inside one runtime instance.
- Audit events contain identifiers, status and timing, rather than argument
  bodies or credentials.
- The included tools are deterministic. For external side effects, the tool
  provider must supply its own idempotency protocol: a process can crash
  between an external action and recording its result.

This project demonstrates the execution layer beneath a tool-using agent.
It does not contain a live LLM adapter, a cross-process scheduler or an OS
sandbox. The tests exercise durable recovery, concurrent duplicate calls,
input conflicts, rejected tool names and cancellation without false success.
