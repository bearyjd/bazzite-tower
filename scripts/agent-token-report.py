#!/usr/bin/env python3
"""Read local agent JSONL logs; emit only aggregate usage, never message content.

Claude index weights (input/write/read/output): 1/1.25/0.1/5. This is a
relative index, not dollars. Codex counters are cumulative; report their deltas.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
import json
import math
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class Usage:
    input: int = 0
    cache_write: int = 0
    cache_read: int = 0
    output: int = 0
    reasoning: int = 0
    total: int = 0
    turns: int = 0
    large_context_turns: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(*(a + b for a, b in zip(asdict(self).values(), asdict(other).values())))

    @property
    def index(self) -> float:
        return self.input + 1.25 * self.cache_write + 0.1 * self.cache_read + 5 * self.output


@dataclass(frozen=True)
class Sample:
    day: str
    project: str
    kind: str
    model: str
    usage: Usage


def count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def day_of(value: Any) -> str:
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return ""


def label(value: Any) -> str:
    return value if isinstance(value, str) and value else "unknown"


def records(path: Path, errors: dict[str, int]) -> Iterator[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError
                    yield record
                except (ValueError, TypeError):
                    errors["malformed_records"] += 1
    except OSError:
        errors["unreadable_files"] += 1


def content_chars(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(part["text"]) for part in content
                   if isinstance(part, dict) and isinstance(part.get("text"), str))
    return 0


def claude(root: Path, since: str, project_filter: str, errors: dict[str, int]
           ) -> tuple[tuple[Sample, ...], tuple[dict[str, Any], ...], int]:
    messages: dict[str, Sample] = {}
    tools: dict[str, tuple[int, int]] = {}
    seen_results: set[tuple[str, str, int]] = set()
    files = 0
    for path in sorted(root.rglob("*.jsonl")):
        project = path.relative_to(root).parts[0]
        if project_filter not in project:
            continue
        files += 1
        kind = "subagent" if "subagents" in path.relative_to(root).parts else "main"
        names: dict[str, str] = {}
        for record in records(path, errors):
            message = mapping(record.get("message"))
            content = message.get("content")
            day = day_of(record.get("timestamp"))
            included = bool(day and day >= since)
            if record.get("type") == "assistant":
                for part in content if isinstance(content, list) else ():
                    if isinstance(part, dict) and part.get("type") == "tool_use":
                        names[label(part.get("id"))] = label(part.get("name"))
                usage = mapping(message.get("usage"))
                mid = message.get("id")
                if not included or not usage or not isinstance(mid, str) or not mid:
                    continue
                numbers = tuple(count(usage.get(key)) for key in (
                    "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
                previous = messages.get(mid)
                if previous:
                    numbers = tuple(max(a, b) for a, b in zip(numbers, (
                        previous.usage.input, previous.usage.cache_write,
                        previous.usage.cache_read, previous.usage.output)))
                current = Usage(*numbers, total=sum(numbers), turns=1,
                                large_context_turns=int(sum(numbers[:3]) >= 400_000))
                messages[mid] = replace(previous, usage=current) if previous else Sample(
                    day, project, kind, label(message.get("model")), current)
            elif record.get("type") == "user" and included:
                for index, part in enumerate(content if isinstance(content, list) else ()):
                    if not isinstance(part, dict) or part.get("type") != "tool_result":
                        continue
                    # UUID identifies copied/replayed user records across files.
                    key = (label(record.get("uuid")) if record.get("uuid") else str(path),
                           label(part.get("tool_use_id")), index)
                    if key in seen_results:
                        continue
                    seen_results.add(key)
                    name = names.get(label(part.get("tool_use_id")), "unknown")
                    calls, chars = tools.get(name, (0, 0))
                    tools[name] = (calls + 1, chars + content_chars(part.get("content")))
    return tuple(messages.values()), tuple(
        {"tool": name, "calls": calls, "chars": chars, "approx_tokens": chars / 4}
        for name, (calls, chars) in sorted(tools.items(), key=lambda item: (-item[1][1], item[0]))), files


def codex(root: Path, since: str, project_filter: str, errors: dict[str, int]
          ) -> tuple[tuple[Sample, ...], tuple[dict[str, Any], ...], int]:
    samples: list[Sample] = []
    rates: dict[tuple[str, str], dict[str, Any]] = {}
    files = 0
    keys = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")
    for path in sorted(root.rglob("*.jsonl")):
        project, kind, model = "unknown", "main", "unknown"
        previous = (0,) * len(keys)
        selected_file = False
        for record in records(path, errors):
            payload = mapping(record.get("payload"))
            if record.get("type") == "session_meta":
                project = label(payload.get("cwd"))
                source = payload.get("source")
                kind = "subagent" if isinstance(source, dict) and "subagent" in source else "main"
            elif record.get("type") == "turn_context":
                model = label(payload.get("model"))
            if payload.get("type") != "token_count":
                continue
            timestamp = label(record.get("timestamp"))
            day = day_of(timestamp)
            included = bool(day and day >= since and project_filter in project)
            if included:
                selected_file = True
                limits = mapping(payload.get("rate_limits"))
                for window in ("primary", "secondary"):
                    limit = mapping(limits.get(window))
                    percent = limit.get("used_percent")
                    if (isinstance(percent, (int, float)) and not isinstance(percent, bool)
                            and math.isfinite(percent) and 0 <= percent <= 100):
                        key = (label(limits.get("limit_id")), window)
                        if key not in rates or timestamp > rates[key]["timestamp"]:
                            rates[key] = {"limit_id": key[0], "window": window,
                                          "used_percent": percent, "timestamp": timestamp}
            info = mapping(payload.get("info"))
            total = mapping(info.get("total_token_usage"))
            if not total:
                continue
            current = tuple(count(total.get(key)) for key in keys)
            delta = tuple(max(0, a - b) for a, b in zip(current, previous))
            # A counter reset starts a new cumulative segment.
            if current[-1] < previous[-1]:
                delta = current
            previous = current
            if included and any(delta):
                inp, cached, out, reasoning, tokens = delta
                context = count(mapping(info.get("last_token_usage")).get("input_tokens"))
                samples.append(Sample(day, project, kind, model, Usage(
                    input=inp, cache_read=cached, output=out, reasoning=reasoning,
                    total=tokens, turns=1, large_context_turns=int(context >= 400_000))))
        files += int(selected_file)
    return tuple(samples), tuple(rates[key] for key in sorted(rates)), files


def summarize(samples: tuple[Sample, ...], weighted: bool = False) -> dict[str, Any]:
    def values(usage: Usage) -> dict[str, Any]:
        return {**asdict(usage), **({"cost_index": usage.index} if weighted else {}),
                "large_context_share_percent": 100 * usage.large_context_turns / usage.turns if usage.turns else 0}

    total = Usage()
    groups: dict[str, dict[str, Usage]] = {key: {} for key in ("day", "project", "kind", "model")}
    for sample in samples:
        total += sample.usage
        for dimension, group in groups.items():
            key = getattr(sample, dimension)
            group[key] = group.get(key, Usage()) + sample.usage
    return {"total": values(total), **{
        f"by_{dimension}": tuple({dimension: key, **values(usage)} for key, usage in sorted(group.items()))
        for dimension, group in groups.items()}}


def report(claude_root: Path, codex_root: Path, since: str, project: str) -> dict[str, Any]:
    errors = {"malformed_records": 0, "unreadable_files": 0}
    claude_samples, tools, claude_files = claude(claude_root, since, project, errors)
    codex_samples, rates, codex_files = codex(codex_root, since, project, errors)
    return {"since": since, "project_filter": project,
            "notes": "Claude index weights 1/1.25/0.1/5; relative index, not dollars. Codex input includes cached input; reasoning is part of output. Tool tokens approximate chars/4.",
            "claude": {**summarize(claude_samples, weighted=True), "files": claude_files, "tool_results": tools},
            "codex": {**summarize(codex_samples), "files": codex_files, "rate_limits": rates},
            "errors": errors}


def text_report(data: dict[str, Any]) -> str:
    lines = [f"Agent token report since {data['since']} (project filter: {json.dumps(data['project_filter'] or 'all')})",
             data["notes"]]
    for provider in ("claude", "codex"):
        section = data[provider]
        total = section["total"]
        index = f" index={total['cost_index']:.1f}" if provider == "claude" else ""
        lines.append(f"{provider}: files={section['files']} turns={total['turns']} input={total['input']} write={total['cache_write']} cached={total['cache_read']} output={total['output']} total={total['total']}{index}")
        lines.append(f"  >=400k context: {total['large_context_turns']} ({total['large_context_share_percent']:.1f}%)")
        for dimension in ("day", "project", "kind", "model"):
            rows = section[f"by_{dimension}"]
            top = sorted(rows, key=lambda row: (-row["total"], row[dimension]))[:3]
            lines.append(f"  by {dimension} (top 3/{len(rows)}): " + "; ".join(
                f"{json.dumps(row[dimension], ensure_ascii=True)}={row['total']}" for row in top))
    lines.append("Claude tool_result volume (top 8; chars and approximate tokens):")
    for tool in data["claude"]["tool_results"][:8]:
        lines.append(f"  {json.dumps(tool['tool'])}: calls={tool['calls']} chars={tool['chars']} ~tokens={tool['approx_tokens']:.0f}")
    lines.append("Codex latest rate_limits.used_percent (top 8 windows):")
    for rate in data["codex"]["rate_limits"][:8]:
        lines.append(f"  {json.dumps(rate['limit_id'])}/{rate['window']}: {rate['used_percent']}% at {json.dumps(rate['timestamp'])}")
    lines.append(f"Skipped: malformed={data['errors']['malformed_records']} unreadable_files={data['errors']['unreadable_files']}")
    return "\n".join(lines)


def since_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=since_date, default=date.today() - timedelta(days=7), metavar="YYYY-MM-DD")
    parser.add_argument("--project", default="", help="substring of Claude project directory or Codex cwd")
    parser.add_argument("--json", action="store_true", help="emit complete aggregates as JSON")
    args = parser.parse_args()
    data = report(Path.home() / ".claude/projects", Path.home() / ".codex/sessions", args.since.isoformat(), args.project)
    print(json.dumps(data, indent=2) if args.json else text_report(data))


if __name__ == "__main__":
    main()
