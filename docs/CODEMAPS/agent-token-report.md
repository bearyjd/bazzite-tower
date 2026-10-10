# Local agent token report

`just token-report` runs `scripts/agent-token-report.py` with Python's standard
library. It opens `~/.claude/projects/**/*.jsonl` and
`~/.codex/sessions/**/*.jsonl` read-only and emits aggregates only. It never
writes transcripts or prints messages, tool inputs, commands, or tool results.
`--since YYYY-MM-DD` uses record dates (inclusive); the default is seven days
before today. `--project substring` matches Claude project-directory names or
Codex session cwd. `--json` includes complete day/project/kind/model groups;
text prints top groups and stays within 40 lines.

Claude assistant usage is deduplicated globally by `message.id`, keeping maximum
counters across streaming records and the first included record's metadata.
The relative cost index is input + 1.25 × cache write + 0.1 × cache read +
5 × output; it is not a dollar estimate. Context is input + cache write + cache
read; the report includes the fraction of turns at or above 400k. Tool result
volume counts text characters by tool name and estimates tokens as chars/4;
images are excluded. User UUID/tool ID/block index suppress replayed results.

Codex cumulative `total_token_usage` snapshots become deltas, including a
baseline before `--since`. Duplicate snapshots add nothing; total counter resets
start a new segment. Input includes cached input and output includes reasoning;
these subdivisions must not be added again. No Claude cost index is applied.
The latest valid `rate_limits.used_percent` is reported per limit ID and
primary/secondary window, including events without usage snapshots. A window's
duration is not inferred from its name. Codex model comes from `turn_context`;
missing metadata is `unknown`. Context share uses `last_token_usage.input_tokens`
for nonzero deltas, so turns here mean usage updates, not user prompts.

Malformed records and unreadable files are counted without printing their
contents or paths. Missing transcript roots yield empty aggregates. Truncated
logs can overstate their first Codex delta because no earlier baseline exists.
Logs copied under multiple Codex filenames are not deduplicated by session ID.

`python3 tests/test-agent-token-report.py` creates tiny synthetic logs in a
temporary HOME to verify deduplication, filters, cumulative deltas, resets,
latest rates, content privacy, malformed input, and the CLI without opening
real transcripts. The tool does not participate in image builds or CI gates.
