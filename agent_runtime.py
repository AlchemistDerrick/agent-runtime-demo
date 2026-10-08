"""An AI-assisted reference implementation of an observable tool-call runtime.

This demo executes structured calls supplied by a model adapter or a caller.
It does not connect to a live language model or execute arbitrary Python/shell.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable


class ToolError(ValueError):
    pass


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Tool:
    validate: Callable[[dict[str, Any]], dict[str, Any]]
    run: Callable[[dict[str, Any]], Awaitable[Any]]
    timeout_seconds: float = 2.0


class Journal:
    def __init__(self, path: str | Path):
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS successes ("
            "call_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
            "result_json TEXT NOT NULL)"
        )
        self.connection.commit()

    def lookup(self, call_id: str, fingerprint: str) -> tuple[bool, Any]:
        row = self.connection.execute(
            "SELECT fingerprint, result_json FROM successes WHERE call_id = ?",
            (call_id,),
        ).fetchone()
        if row is None:
            return False, None
        if row[0] != fingerprint:
            raise ToolError("call_id_conflict: an existing ID has different inputs")
        return True, json.loads(row[1])

    def record(self, call_id: str, fingerprint: str, result: Any) -> None:
        encoded = json.dumps(result, allow_nan=False, sort_keys=True)
        with self.connection:
            self.connection.execute(
                "INSERT INTO successes VALUES (?, ?, ?)",
                (call_id, fingerprint, encoded),
            )

    def close(self) -> None:
        self.connection.close()


class Runtime:
    def __init__(self, journal: Journal):
        self.journal = journal
        self.tools: dict[str, Tool] = {}
        self.events: list[dict[str, Any]] = []
        self._locks: dict[str, asyncio.Lock] = {}

    def register(self, name: str, tool: Tool) -> None:
        if name in self.tools:
            raise ToolError("duplicate_tool")
        if not math.isfinite(tool.timeout_seconds) or tool.timeout_seconds <= 0:
            raise ToolError("invalid_timeout")
        self.tools[name] = tool

    async def execute(self, call: ToolCall) -> Any:
        if not isinstance(call.call_id, str) or not call.call_id or len(call.call_id) > 200:
            raise ToolError("invalid_call_id")
        if call.tool not in self.tools:
            raise ToolError("tool_not_allowed")
        if not isinstance(call.arguments, dict):
            raise ToolError("arguments_must_be_an_object")
        tool = self.tools[call.tool]
        arguments = tool.validate(call.arguments)
        canonical = json.dumps(
            {"tool": call.tool, "arguments": arguments},
            sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        lock = self._locks.setdefault(call.call_id, asyncio.Lock())
        async with lock:
            found, result = self.journal.lookup(call.call_id, fingerprint)
            if found:
                self.events.append({"call_id": call.call_id, "tool": call.tool, "status": "cached"})
                return result
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(tool.run(arguments), tool.timeout_seconds)
                self.journal.record(call.call_id, fingerprint, result)
            except Exception as exc:
                self.events.append({
                    "call_id": call.call_id, "tool": call.tool,
                    "status": "failed", "error_type": type(exc).__name__,
                })
                raise
            self.events.append({
                "call_id": call.call_id, "tool": call.tool, "status": "completed",
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
            })
            return result


def validate_numbers(arguments: dict[str, Any]) -> dict[str, Any]:
    if set(arguments) != {"values"}:
        raise ToolError("expected_only_values")
    values = arguments["values"]
    if not isinstance(values, list) or not 1 <= len(values) <= 10000:
        raise ToolError("expected_1_to_10000_values")
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise ToolError("expected_finite_numbers")
    return {"values": values}


async def summarize(arguments: dict[str, Any]) -> dict[str, Any]:
    values = arguments["values"]
    total = math.fsum(values)
    return {"count": len(values), "sum": total, "mean": total / len(values)}


def validate_text(arguments: dict[str, Any]) -> dict[str, Any]:
    if set(arguments) != {"text"} or not isinstance(arguments["text"], str):
        raise ToolError("expected_only_text")
    if len(arguments["text"]) > 100000:
        raise ToolError("text_too_large")
    return arguments


async def normalize_text(arguments: dict[str, Any]) -> dict[str, str]:
    return {"text": " ".join(arguments["text"].split())}


def make_runtime(path: str | Path) -> Runtime:
    runtime = Runtime(Journal(path))
    runtime.register("numbers.summary", Tool(validate_numbers, summarize))
    runtime.register("text.normalize", Tool(validate_text, normalize_text))
    return runtime


async def demo() -> None:
    runtime = make_runtime("demo.sqlite3")
    try:
        calls = [
            ToolCall("example-1", "numbers.summary", {"values": [10, 20, 30]}),
            ToolCall("example-2", "text.normalize", {"text": "  A   structured\nworkflow  "}),
            ToolCall("example-1", "numbers.summary", {"values": [10, 20, 30]}),
        ]
        results = [await runtime.execute(call) for call in calls]
        print(json.dumps({"results": results, "events": runtime.events}, indent=2))
    finally:
        runtime.journal.close()


if __name__ == "__main__":
    asyncio.run(demo())
