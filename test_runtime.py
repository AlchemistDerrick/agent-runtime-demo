import asyncio
import tempfile
import unittest
from pathlib import Path

from agent_runtime import Journal, Runtime, Tool, ToolCall, ToolError, make_runtime


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "journal.sqlite3"
        self.runtime = make_runtime(self.path)

    async def asyncTearDown(self):
        self.runtime.journal.close()
        self.temp.cleanup()

    async def test_restart_recovers_a_successful_result(self):
        call = ToolCall("durable", "numbers.summary", {"values": [2, 4]})
        expected = await self.runtime.execute(call)
        self.runtime.journal.close()
        self.runtime = make_runtime(self.path)
        self.assertEqual(await self.runtime.execute(call), expected)
        self.assertEqual(self.runtime.events[-1]["status"], "cached")

    async def test_concurrent_duplicate_calls_execute_handler_once(self):
        invocations = []

        async def slow(arguments):
            invocations.append(1)
            await asyncio.sleep(0.02)
            return {"ok": True}

        self.runtime.register("once", Tool(lambda arguments: arguments, slow))
        call = ToolCall("same", "once", {})
        results = await asyncio.gather(self.runtime.execute(call), self.runtime.execute(call))
        self.assertEqual(results, [{"ok": True}, {"ok": True}])
        self.assertEqual(len(invocations), 1)

    async def test_reusing_an_id_with_changed_input_is_rejected(self):
        await self.runtime.execute(ToolCall("fixed", "numbers.summary", {"values": [1]}))
        with self.assertRaisesRegex(ToolError, "call_id_conflict"):
            await self.runtime.execute(ToolCall("fixed", "numbers.summary", {"values": [99]}))

    async def test_unregistered_commands_and_invalid_arguments_are_rejected(self):
        with self.assertRaisesRegex(ToolError, "tool_not_allowed"):
            await self.runtime.execute(ToolCall("bad", "os.system", {"command": "echo nope"}))
        for values in ([True], [float("nan")], [], ["1"]):
            with self.assertRaises(ToolError):
                await self.runtime.execute(ToolCall("invalid", "numbers.summary", {"values": values}))

    async def test_timeout_does_not_create_a_success_checkpoint(self):
        async def blocked(arguments):
            await asyncio.sleep(10)

        self.runtime.register("bounded", Tool(lambda arguments: arguments, blocked, 0.02))
        with self.assertRaises(TimeoutError):
            await self.runtime.execute(ToolCall("timeout", "bounded", {}))
        count = self.runtime.journal.connection.execute(
            "SELECT COUNT(*) FROM successes WHERE call_id = ?", ("timeout",)
        ).fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(self.runtime.events[-1]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
