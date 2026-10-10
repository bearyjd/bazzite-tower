#!/usr/bin/env python3
"""Synthetic-only contracts; never opens the user's transcript directories."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/agent-token-report.py"
SPEC = importlib.util.spec_from_file_location("agent_token_report", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
SECRET = "SYNTHETIC_PRIVATE_MESSAGE_DO_NOT_EMIT"


def assistant(mid: str, day: str, output: int = 5) -> dict:
    return {"type": "assistant", "timestamp": day + "T12:00:00Z", "message": {
        "id": mid, "model": "claude-test", "usage": {
            "input_tokens": 100_000, "cache_creation_input_tokens": 100_000,
            "cache_read_input_tokens": 200_000, "output_tokens": output},
        "content": [{"type": "text", "text": SECRET},
                    {"type": "tool_use", "id": "tool-1", "name": "Read", "input": {"text": SECRET}}]}}


def token_event(day: str, total: int, rate: float = 25) -> dict:
    return {"type": "event_msg", "timestamp": day + "T12:00:00Z", "payload": {
        "type": "token_count", "info": {"total_token_usage": {
            "input_tokens": total - 10, "cached_input_tokens": total // 2,
            "output_tokens": 10, "reasoning_output_tokens": 2, "total_tokens": total},
            "last_token_usage": {"input_tokens": 400_000}},
        "rate_limits": {"primary": {"used_percent": rate}, "secondary": {"used_percent": 50}}}}


class TokenReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.claude = self.home / ".claude/projects"
        self.codex = self.home / ".codex/sessions"

    def write(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    def test_claude_stream_dedup_filters_volume_and_privacy(self) -> None:
        user = {"type": "user", "uuid": "user-1", "timestamp": "2026-10-10T12:00:00Z",
                "message": {"content": [{"type": "tool_result", "tool_use_id": "tool-1",
                                          "content": [{"type": "text", "text": SECRET},
                                                      {"type": "image", "data": SECRET}]}]}}
        self.write(self.claude / "tower/main.jsonl", [assistant("old", "2026-10-01"),
                   assistant("one", "2026-10-10"), assistant("one", "2026-10-10", 15), user, user])
        self.write(self.claude / "tower/subagents/sub.jsonl", [assistant("two", "2026-10-10")])
        self.write(self.claude / "other/main.jsonl", [assistant("other", "2026-10-10")])
        data = MODULE.report(self.claude, self.codex, "2026-10-03", "tower")
        total = data["claude"]["total"]
        self.assertEqual(total["turns"], 2)
        self.assertEqual(total["output"], 20)
        self.assertEqual(total["cost_index"], 490_100)
        self.assertEqual(total["large_context_share_percent"], 100)
        self.assertEqual({row["kind"] for row in data["claude"]["by_kind"]}, {"main", "subagent"})
        self.assertEqual(data["claude"]["tool_results"][0]["chars"], len(SECRET))
        self.assertEqual(data["claude"]["tool_results"][0]["calls"], 1)
        self.assertNotIn(SECRET, json.dumps(data))
        self.assertNotIn(SECRET, MODULE.text_report(data))
        self.assertLessEqual(len(MODULE.text_report(data).splitlines()), 40)

    def test_codex_cumulative_deltas_since_duplicates_reset_and_latest_rate(self) -> None:
        latest = token_event("2026-10-11", 160, 70)
        latest["payload"]["info"] = None
        self.write(self.codex / "session.jsonl", [
            {"type": "session_meta", "payload": {"cwd": "/project/tower", "source": {"subagent": {}}}},
            {"type": "turn_context", "payload": {"model": "codex-test"}},
            token_event("2026-10-01", 100), token_event("2026-10-10", 160),
            token_event("2026-10-10", 160), token_event("2026-10-10", 20), latest,
            {"type": "event_msg", "payload": {"type": "agent_message", "message": SECRET}}])
        data = MODULE.report(self.claude, self.codex, "2026-10-03", "tower")
        total = data["codex"]["total"]
        self.assertEqual(total["total"], 80)
        self.assertEqual(total["turns"], 2)
        self.assertEqual(total["cache_read"], 40)
        self.assertEqual(data["codex"]["by_model"][0]["model"], "codex-test")
        self.assertEqual(data["codex"]["rate_limits"][0]["used_percent"], 70)
        self.assertEqual(data["codex"]["by_kind"][0]["kind"], "subagent")
        self.assertEqual(MODULE.report(self.claude, self.codex, "2026-10-03", "absent")["codex"]["total"]["total"], 0)
        self.assertNotIn(SECRET, json.dumps(data))

    def test_malformed_missing_and_cli(self) -> None:
        self.write(self.claude / "tower/empty.jsonl", [{"type": "assistant", "message": None}, {}])
        with (self.claude / "tower/empty.jsonl").open("a") as stream:
            stream.write("broken json\n[]\n")
        data = MODULE.report(self.claude, self.codex, "2026-10-03", "")
        self.assertEqual(data["errors"]["malformed_records"], 2)
        # Run only against the synthetic HOME; never invoke the real-log CLI here.
        env = {**os.environ, "HOME": str(self.home)}
        result = subprocess.run([sys.executable, str(SCRIPT), "--since", "2026-10-03", "--json"],
                                env=env, check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)["claude"]["total"]["turns"], 0)
        invalid = subprocess.run([sys.executable, str(SCRIPT), "--since", "bad-date"],
                                 env=env, capture_output=True)
        self.assertEqual(invalid.returncode, 2)

    def test_invalid_rates_and_exact_date_format(self) -> None:
        rows = [token_event("2026-10-10", 100)]
        for percent in (float("nan"), float("inf"), -1, 101, True):
            event = token_event("2026-10-11", 100, percent)
            event["payload"]["rate_limits"]["secondary"] = None
            rows.append(event)
        self.write(self.codex / "session.jsonl", rows)
        data = MODULE.report(self.claude, self.codex, "2026-10-03", "")
        self.assertEqual(data["codex"]["rate_limits"][0]["used_percent"], 25)
        self.assertNotIn("cost_index", data["codex"]["total"])
        with self.assertRaises(MODULE.argparse.ArgumentTypeError):
            MODULE.since_date("20261003")

    def test_just_forwards_literal_arguments(self) -> None:
        marker = self.home / "must-not-exist"
        project = f"project with spaces; touch {marker}; $(echo forbidden)"
        result = subprocess.run(["just", "token-report", "--since", "2026-10-03",
                                 "--project", project, "--json"],
                                cwd=SCRIPT.parents[1], env={**os.environ, "HOME": str(self.home)},
                                check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)["project_filter"], project)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
