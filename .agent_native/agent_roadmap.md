# Agent-Native Roadmap — bazzite-tower

Audit date: 2026-07-07. This repo is **already unusually mature** for agent
handoff: it has CODEMAPS, a RUNBOOK, a CONTRIBUTING guide, a documented CI
failure model (`docs/downstream-change-tracking.md`), an offline smoke gate
(`tests/smoke.sh`) and a runtime boot test (`tests/boot-check.sh`), and even a
prior worked example of an agent-driven hardware-bug investigation
(`docs/research/i915-bug-report/`). The gaps below are the remaining
**5–15% surface** standing between "well-documented personal project" and
"an agent can pick up a raw issue and close it unattended."

Ranked by **Human-Attention-Saved per Unit of Effort** (highest first).

**Status (2026-08-07): 6 of 6 resolved. Nothing open.**

Every item in this audit is closed. Two things are worth carrying forward more
than the individual fixes:

- **Specs in this file went stale faster than the code.** Items 4 and 5 both
  described a repo that no longer existed by the time they ran (item 4 said 265
  lines / 7 concerns against an actual 380 / 18; item 5 prescribed a `hadolint`
  invocation that hangs). Two codemaps carried two further wrong line counts.
  **Re-measure before implementing an entry here; do not trust its numbers.**
- **Prefer filenames to line numbers in docs.** The line ranges in
  `docs/downstream-change-tracking.md` were already drifting before item 4
  touched anything. They are now `build.d/` filenames, which cannot.

New gaps should be appended as item 7 onward rather than edited into the closed
entries above.

---

## 1. Reconcile the dangling `i915-resume-fix-check.timer` reference (tiny effort, real correctness bug) — DONE (2026-07-07)

**Resolution: (a) implemented.** Added
`system_files/usr/lib/systemd/system/i915-resume-fix-check.{service,timer}` +
`system_files/usr/libexec/i915-resume-fix-check`, enabled the timer in
`build_files/build.sh` (new "i915 resume-regression watcher" section right
after the microcode install), and added smoke-test assertions in
`tests/smoke.sh` (`check_enabled "i915-resume-fix-check.timer"` + helper
executable check), mirroring the existing service+helper pattern
(`bazzite-tower-power-tuning`, `bazzite-tower-wifi-backend-guard`). Also
documented the new unit/timer/helper in `docs/CODEMAPS/system-files.md`.

The helper is kernel-version-gated: on a pre-7.0 kernel (the current pin) it's
a no-op log line; on 7.0+ it greps the current boot's journal for the known-bad
cx0 DPLL/`flip_done` signature (per `docs/research/i915-bug-report/COMMENT.md`'s
`journalctl` recipe) and logs a warning if found. The timer runs 5 minutes
after boot and then daily (`OnBootSec=5min`, `OnUnitActiveSec=1d`,
`Persistent=true`), so a resume-triggered failure earlier in a long-uptime
session still gets caught. A clean run does not by itself certify the upstream
bug is fixed — the helper's own log message says so — so the Containerfile
comment and `docs/research/i915-mtl-resume-2026-06-20.md` remain the source of
truth for "is it safe to unpin yet."

(a) was chosen over (b) because the watcher concept was clearly deliberate,
not a stray idea: `docs/research/i915-mtl-resume-2026-06-20.md:126` explicitly
says "the watcher must be repointed" (from an earlier, now-corrected root-cause
signature) "done alongside this report" — i.e. a watcher was assumed to exist
and be maintained, and `docs/research/i915-bug-report/BISECT-RUNBOOK.md:84`
even references a host-side sibling script
(`~/scripts/check-i915-resume-fix.sh`, outside this repo) using the *old*
signature. Implementing the repo-tracked, machine-checkable version — with the
corrected signature — is more faithful to that intent than deleting the
sentence.

Verification: `just lint` (shellcheck, clean — pre-existing unrelated SC2329
info-level note on `tests/boot-check.sh` only) and `bash -n`/`shellcheck` on
the new helper directly, both clean. `just check` fails, but on a pre-existing
Justfile formatting drift unrelated to this change (confirmed via `git diff
--name-only`, which shows only `build_files/build.sh`, `tests/smoke.sh`, and
`docs/CODEMAPS/system-files.md` touched — `Justfile` untouched). `just build`
was intentionally not run per task constraints, so `tests/smoke.sh`'s new
assertions are unexecuted against a real image; they're written to the exact
pattern already proven by every other `check_enabled`/helper-executable pair
in that file.

Original problem statement kept below for reference.

---

**Problem:** `Containerfile:21` says *"the host watcher `i915-resume-fix-check.timer`
flags it"* — but no such timer, service, or helper exists anywhere in the repo
(`grep -rn i915-resume-fix-check .` matches only that one comment). Either the
watcher was planned and never built, or it was renamed/removed and the comment
was never updated. Either way, an agent (or human) reading the Containerfile
today is told a safety net exists that doesn't — a false verification signal
sitting right next to the highest-stakes pin in the repo (the kernel version
pin that dodges the i915 resume regression).

**Why it's high value:** it's a 15-minute fix that removes a landmine from the
single most consequential comment block in the codebase — the one that
governs *when it's safe to move off the pinned base image*.

**Acceptance criteria (pick one, both are agent-executable):**
- **(a) Implement it:** add `system_files/usr/lib/systemd/system/i915-resume-fix-check.timer`
  + a matching `.service` + a small libexec helper that checks (on a schedule)
  whether the running kernel is still pre-7.0 or, once on 7.0+, greps
  `dmesg`/journal for the known-bad `flip_done` storm signature and logs a
  warning. Enable it in `build_files/build.sh` next to the other unit
  enables. Add smoke-test assertions in `tests/smoke.sh` (unit present +
  enabled) mirroring the pattern for every other unit in that file.
- **(b) Retract the claim:** if the watcher was never meant to be more than an
  idea, delete the sentence from the `Containerfile` comment and replace it
  with a pointer to a manual recheck procedure (see item 4 below).

Either resolves the mismatch; (a) is more agent-native going forward since it
gives a machine-checkable signal instead of a comment.

**Files:** `Containerfile:21-23`, `build_files/build.sh`, `tests/smoke.sh`,
optionally new `system_files/usr/lib/systemd/system/i915-resume-fix-check.*`.

---

## 2. Write a bug-report triage protocol into `CLAUDE.md` — DONE (shipped earlier; verified 2026-08-06)

**Resolution: the deliverable already exists.** `CLAUDE.md` grew both required
sections at some point after this item was written, and the item was never
marked. Audited 2026-08-06 against the acceptance criteria below:

| Criterion | Where | Status |
|---|---|---|
| "Triaging a bug report" section, two-path classification | `CLAUDE.md` §"Triaging a bug report" | **Met** — Build/CI-reproducible vs Hardware-log-dependent, with the "not reproducible without physical access or the reporter's logs" caveat stated explicitly |
| The never-change-hardware-values-without-evidence rule | `CLAUDE.md` §"Hardware-specific tuning" | **Met** — names `kargs.d/*.toml`, build.sh's RAS/thermal/audio sections and RUNBOOK "verified" facts, and requires either an explicit user instruction or a `journalctl`/`smartctl`/`ras-mc-ctl`/`/proc/cmdline` excerpt |
| Cross-link `i915-bug-report/` as the write-up template | `CLAUDE.md` §"Repository layout" and §"Triaging a bug report" | **Met** — cited in both, as "a real worked example of the second category done right" |
| The exact per-subsystem commands to request | `CLAUDE.md` §"Triaging a bug report" | **Met by reference, not by inlining** — it points at `docs/RUNBOOK.md`'s health-check table and `scripts/tower-diagnostic.sh` rather than copying them |

That last row is a deliberate deviation, not a gap. This repo treats the RUNBOOK
table as the single source of truth and elsewhere forbids duplicating it (see
`docs/CONTRIBUTING.md` on the README Justfile table). Inlining the commands into
`CLAUDE.md` would create a second copy to drift — which is the failure mode item
6 had to correct. A pointer satisfies the intent: a fresh agent is told exactly
where to look and that it must ask before guessing.

**Nothing to build.** This entry is bookkeeping only.

---

<details>
<summary>Original problem statement, kept for context</summary>

**Problem:** the RUNBOOK's "Common issues" table and the `i915-bug-report/`
folder show the *right instincts already exist* (classify the symptom,
gather specific logs, check against a known cause), but that judgment is
tribal — it lives in one person's head, exercised once, and isn't written
down as a repeatable procedure. A fresh agent given "audio is broken again"
or "VMs won't start" has to rediscover, from scratch, which of the two
fundamentally different reproduction paths applies:

- **Build/CI-reproducible** (packaging, systemd units, kargs, Docker,
  smoke/boot-test assertions) — the agent can reproduce and verify locally
  with `podman build` + `just smoke` + `tests/boot-check.sh`, no hardware
  needed.
- **Hardware-log-dependent** (SOF audio storms, i915 resume timing, RAS/MCE
  decode, NVMe SMART/APST) — the agent categorically **cannot** reproduce
  without either physical access to the ThinkPad P1 or the user pasting
  specific log output, and must not guess at thresholds or "fix" hardware
  tuning values without that evidence.

**Why it's high value:** this single classification, written once, prevents
the most likely agent failure mode on this repo: confidently "fixing" a
hardware-tuning value (an EPP setting, an SOF karg, an NVMe latency knob) with
no evidence it applies to the reporter's machine, because nothing tells the
agent to ask for `journalctl`/`dmesg` output first.

**Acceptance criteria:**
- `CLAUDE.md` (see item in this same deliverable set) contains a "Triaging a
  bug report" section with: the two-path classification above, the exact
  commands to request from the user for each hardware subsystem (mirroring
  `scripts/tower-diagnostic.sh` and the RUNBOOK health-check table), and an
  explicit rule: *"Never change a value in `system_files/**/kargs.d/`,
  `build_files/build.sh`'s power/audio/RAS sections, or `docs/RUNBOOK.md`'s
  hardware facts without either (a) a `journalctl`/`smartctl`/`ras-mc-ctl`
  excerpt from the reporter, or (b) an explicit instruction to change the
  default for all hardware."*
- Cross-link `docs/research/i915-bug-report/` as the template for how a
  hardware investigation should be written up (`REPORT.md`, `BISECT-RUNBOOK.md`,
  raw log captures) so future ones follow the same shape.

**Files:** `CLAUDE.md` (new), cross-reference `docs/RUNBOOK.md`,
`scripts/tower-diagnostic.sh`, `docs/research/i915-bug-report/`.

</details>

---

## 3. Give `ci/base-diff.py` a fixture-based unit test (small effort, closes a real verification gap) — DONE (2026-07-07)

**Resolution: dependency-light shell alternative.** Added
`tests/test-base-diff.sh` + six committed fixture manifest pairs under
`tests/fixtures/base-diff/` (first run, first-run-vs-GITHUB_OUTPUT gating,
blast-radius bump, non-blast-radius change ignored, package added, package
removed, GITHUB_OUTPUT file-write path — 7 scenarios total, 12 assertions),
run via a new `just test-ci` recipe. Went with the roadmap's "stay
dependency-light" alternative over pytest because the repo has no Python
dependency manifest (`pyproject.toml`/`requirements.txt`) anywhere to declare
pytest in, and every existing test (`tests/smoke.sh`, `tests/boot-check.sh`)
is already plain bash with the same `pass`/`bad`/`check`-style assertions —
`tests/test-base-diff.sh` matches that convention and gets picked up by `just
lint` (shellcheck) automatically since it's a `*.sh` file.

Also added the `just test-ci` recipe to the `AUTO-GENERATED:commands` block in
`docs/CONTRIBUTING.md` and to the `README.md` "File Management" section (that
table is the actual source of truth per CONTRIBUTING.md's own note).

Verification: `just test-ci` — all 12 assertions pass. `just lint` — clean
(shellcheck on the new test script + fixtures directory has no `.sh` files to
lint). Confirmed the new `test-ci` Justfile recipe introduces no *additional*
`just check` formatting drift beyond the single pre-existing blank-line issue
in the Justfile (predates this session, unrelated — verified by diffing `just
--unstable --fmt --check -f Justfile` output against the current file; the
only difference is one blank line before the `lint:` recipe, present before
my edit). Did not attempt to fix that pre-existing drift — out of scope for
this item and not something this session introduced.



**Problem:** `ci/base-diff.py` is the only piece of custom *logic* in the repo
(everything else is declarative: Containerfile, kargs TOML, systemd units) —
regex-matching a blast-radius package list and diffing two manifests — yet it
has zero test coverage. An agent asked to widen the blast-radius regex (e.g.
"also watch `mesa*`") or fix an edge case (e.g. first-run behavior, a package
that's removed entirely) has no fast way to verify the change is correct
without triggering the real `base-watch.yml` workflow against live upstream
manifests, which isn't reproducible on demand and doesn't run in a PR.

**Why it's high value:** this is the one file in the repo an agent is likely
to be asked to modify *purely on its own logic* (not "add a package," not "add
a kargs fragment") — a "the base-bump issue didn't fire when it should have"
report is a debugging task that today requires reasoning about the script by
inspection instead of running it against a crafted before/after manifest.

**Acceptance criteria:**
- New `tests/test_base_diff.py` (pytest, matching the `.ruff_cache` already
  present in the repo root — ruff is evidently already in use) with fixture
  manifest pairs covering: first run (no old manifest), a blast-radius
  package version bump, a non-blast-radius package change (must be ignored),
  a package added, a package removed, and the `GITHUB_OUTPUT` file-write path
  (using `monkeypatch.setenv`/`tmp_path`).
  - Alternative if the repo prefers to stay dependency-light: a small
    `tests/test-base-diff.sh` that runs `ci/base-diff.py` against two
    committed fixture manifests under `tests/fixtures/` and greps the output —
    avoids adding pytest as a new dependency if that's a concern.
- `just lint` or a new `just test-ci` recipe runs it; document the recipe in
  `docs/CONTRIBUTING.md`'s command table (`AUTO-GENERATED:commands` block —
  keep it in sync, that block is regenerated from the Justfile).

**Files:** new `tests/test_base_diff.py` or `tests/test-base-diff.sh` +
fixtures, `Justfile` (new recipe), `docs/CONTRIBUTING.md` command table.

---

## 4. Split `build_files/build.sh` into per-concern scripts — DONE (2026-08-07)

**Resolution: extracted to `build_files/build.d/`, 12 scripts, one concern each.**
`build.sh` is now a 37-line runner that `bash`-executes each `build.d/*.sh` in
filename order. Scoped in
[`.claude/PRPs/plans/build-sh-split.plan.md`](../.claude/PRPs/plans/build-sh-split.plan.md),
which was itself reviewed by `/codex review` before implementation.

**The spec here was stale and could not be followed as written.** It describes a
265-line file with seven concerns and proposes nine filenames; the file was 380
lines with 18 sections by the time this ran. Two codemaps carried two further,
different, wrong counts (309 and 276). All corrected.

**Extraction proven lossless, not asserted.** Every one of the 12 files covers a
contiguous line range; the 12 ranges cover 5-380 with no gap and no overlap.
Stripping each file's 3-line header and concatenating in glob order reproduces
`build.sh` lines 5-380 **byte for byte** (`cmp` clean, 376 lines). Non-contiguous
grouping was rejected precisely because it would have silently reordered build
steps: `40-sysusers-fixup.sh` must run after `30-docker-ce.sh` and before
`60-libvirt-services.sh`.

**Two of the three hazards the plan named were real. One was not:**

1. ~~`portmaster.sh` lacks `set -euo pipefail` and would lose it under
   `bash`-per-script~~ — **false alarm.** `portmaster.sh` is `source`d by
   `95-firewall.sh`, not executed, and `set -e` propagates into sourced files
   (verified: a `set -e` parent sourcing a child with a failing command exits 1).
   It inherits from `95-firewall.sh` exactly as it did from `build.sh`.
   `portmaster.sh` was left untouched. Neither the plan nor the codex review
   caught this; running it did.
2. **`FIREWALL_DAEMON` inheritance** — real, and verified working. The
   Containerfile sets it as a `RUN` command-prefix, so the runner's shell has it
   and the child `bash` running `95-firewall.sh` inherits it.
3. **The relocated portmaster `source` path** — real. `dirname
   "${BASH_SOURCE[0]}"` now resolves to `build.d/`, so the path became
   `../firewall/portmaster.sh`. Verified to resolve to
   `build_files/firewall/portmaster.sh`.

**Docs updated** (the sweep the codex review expanded): `CLAUDE.md`,
`README.md`, `docs/CODEMAPS/image-build.md`, `docs/CODEMAPS/architecture.md`,
`docs/downstream-change-tracking.md`. The last of those carried `build.sh` *line
ranges*, one already stale before this change; they are now `build.d/` filenames,
which cannot drift the same way.

---

<details>
<summary>Original problem statement, kept for context</summary>

**Problem:** `build_files/build.sh` is 265 lines covering seven unrelated
concerns (virt stack, dev tooling, Docker CE repo+install, the sysusers/orphan-
shadow workaround, libvirt daemon enablement, Wi-Fi guard enablement, RAS/
microcode, power/thermal, cleanup) as **one script in one `RUN` layer**. This
is documented well internally (good comments), but it means:
- An agent fixing "Docker socket fails at boot" has to read and reason about
  the entire 265-line file to be sure a change to the sysusers section (lines
  ~112-141) doesn't interact with something later in the same script.
- A diff touching one concern (e.g. bumping `thermald` config) shows up in a
  file whose `git blame`/diff context is dominated by unrelated concerns,
  making review harder for both humans and an agent's own self-check pass.
- `CONTRIBUTING.md` already states the principle *"one concern per file"* for
  kargs/units/TOML — `build.sh` is the one place that principle isn't
  followed, at the level that would benefit from it most.

**Why it's worth the effort despite being non-trivial:** this is the
**structural obstacle** of the repo — the one file too entangled to touch
safely today. Splitting it is the highest-leverage single change for future
agent-driven edits, because almost every feature/bugfix request in this repo
(add a package, change a tuning default, add a new guard service) ultimately
touches this file.

**Acceptance criteria:**
- Create `build_files/build.d/` (or similar) with one script per concern:
  `10-virt-stack.sh`, `20-dev-tooling.sh`, `30-docker-ce.sh`,
  `40-sysusers-fixup.sh`, `50-libvirt-daemons.sh`, `60-wifi-guard-enable.sh`
  (or fold small enables together, agent's judgment, but keep the sysusers
  workaround isolated — it's the most fragile piece), `70-ras-microcode.sh`,
  `80-power-thermal.sh`, `90-cleanup.sh` — matching the existing `##` section
  headers in the current file almost 1:1, so the diff is a pure extraction,
  not a rewrite.
- `build_files/build.sh` becomes a thin runner: `set -euo pipefail; for f in
  /ctx/build.d/*.sh; do source "$f"; done` (or execute each — decide based on
  whether later scripts need earlier scripts' shell state; inspection
  suggests they don't, so `bash "$f"` per-script is safer/more isolated than
  `source`).
- Every comment in the current file moves with its section — **no comment
  content is lost**, since those comments are exactly the tribal-knowledge
  capture this audit is trying to preserve.
- `just lint` (shellcheck) and `just smoke` both still pass unmodified — this
  is a pure refactor, verified by running the full existing test suite with
  no new failures and no removed assertions.
- Update `docs/CODEMAPS/image-build.md` and `docs/CODEMAPS/architecture.md`
  (both currently describe `build.sh` as one 265-line file) to reflect the new
  layout.

**Files:** `build_files/build.sh` (becomes runner), new
`build_files/build.d/*.sh`, `docs/CODEMAPS/image-build.md`,
`docs/CODEMAPS/architecture.md`.

</details>

---

## 5. Add a fast, buildless Containerfile lint — DONE (2026-08-07)

**Resolution: shipped as `just lint-containerfile`.** hadolint 2.14.0, pulled
from a digest-pinned container so nothing needs installing locally. Runs in
seconds and never builds the image.

| Acceptance criterion | Status |
|---|---|
| `just lint-containerfile` recipe using hadolint | **Met** |
| Added to `docs/CONTRIBUTING.md` checklist + AUTO-GENERATED table | **Met** (also `CLAUDE.md`'s command table and verification loop, which this repo requires kept in sync) |
| Not a CI gate; prove it passes clean first | **Met** — passes clean, and it is *not* wired into `build.yml` |

**The invocation in the criteria above does not work.** They prescribe
`podman run --rm -i hadolint/hadolint < Containerfile` — hadolint's own README
form. Measured 2026-08-07 against `hadolint@sha256:27086352…`: stdin mode
**hangs** (killed at the timeout, exit 124), and because `podman -i` echoes
stdin back it *looks* like the linter emitted output. An early run of this
task recorded "zero findings, exit 0" from that path; the exit code was the
pipeline's `tail`, not hadolint. That is a false pass, the worst failure mode
for a linter.

Shipped form copies the `Containerfile` to a temp dir and lints it by path.
Verified both directions, which is the only way to trust a linter:

- Clean input → exits 0, prints `Containerfile: no findings`
- Known-bad input (`FROM ubuntu:latest`, unpinned `apt-get install`) → exits 1
  with `DL3007`, `DL3008`, `DL3015` at correct line numbers

The temp-dir copy is deliberate: `-v "$PWD:...:z"` would relabel the working
tree's SELinux context to run a linter, which is not a trade worth making.

**Also fixed during implementation:** the first draft put a long rationale
comment directly above the recipe, and `just` takes the *last* contiguous
comment line as the doc string — so `just --list` showed
`# deliberate change. See .agent_native/agent_roadmap.md item 5.`. A blank line
now separates the rationale from a one-line summary. Note `just format` has the
same defect today (`just --list` renders it as `# "Code style".`); not fixed
here to keep this change reviewable in isolation.

**Still not done, deliberately:** no `build.yml` gate. It passes clean today, but
wiring it in is a separate decision — and `hadolint` findings can change when the
pin moves, so a gate wants its own change with its own CI run.

---

<details>
<summary>Original problem statement, kept for context</summary>

**Problem:** the only static check on the `Containerfile` itself is `bootc
container lint`, which runs as the **last line of the `RUN` instruction
inside the real container build** — i.e. the *only* way to lint the
Containerfile today is to actually build the image, which this audit was
explicitly told not to do, and which takes real time/resources even when an
agent is allowed to. There is no fast, offline check of Containerfile syntax,
instruction ordering, or common anti-patterns (e.g. an accidental second
`FROM`, a `RUN` with an unpinned interactive `dnf` prompt, a missing
`--mount=type=cache` on a large install) before a full build is attempted.

**Why it's worth doing:** it gives an agent a **sub-second feedback loop** on
Containerfile edits (the highest-risk file to hand-edit, since it's the
literal build entry point) before spending the time/resources of a full
`podman build`.

**Acceptance criteria:**
- Add `hadolint` (or equivalent) as a new `just` recipe, e.g. `just
  lint-containerfile`, running `hadolint Containerfile` (container-based
  invocation via `podman run --rm -i hadolint/hadolint < Containerfile` needs
  no local install, consistent with how `bootc-image-builder` is already
  pulled on demand elsewhere in this Justfile).
- Add it to `docs/CONTRIBUTING.md`'s "Before opening a PR" checklist and the
  AUTO-GENERATED commands table.
- Do **not** add it as a blocking CI gate in the same change — first prove it
  passes clean on the current `Containerfile` (or file any findings) before
  wiring it into `build.yml`; this keeps the change reviewable in isolation.

**Files:** `Justfile` (new recipe), `docs/CONTRIBUTING.md`.

</details>

---

## 6. Reconcile `just format` with the repo's actual style — RESOLVED (2026-08-06)

**Resolution: mostly the convention moved — but one real defect was hiding
behind the convention.** Reformatting the tree would still have been a
regression. The original write-up overstated the case, though, by attributing
*all* residual drift to the compact-helper idiom; a re-measure the same day
showed a quarter of it was in files with no helpers at all.

Measured 2026-08-06 with `shfmt` 3.7.0:

```
shfmt defaults (tabs):                1325 diff lines
with .editorconfig pinning 4-space:    954 diff lines
  -> indentation accounted for:         371 lines (28%)
after fixing build.sh's column-0 body:  807 diff lines
  -> tests/*.sh (deliberate idiom):     615 lines (76%)
  -> everywhere else (undecided):       192 lines (24%)
```

Three distinct disagreements, not two:

1. **Indentation.** `shfmt` defaults to tabs; every script here uses 4 spaces
   (0 tab-indented lines across the tree). This *was* worth pinning, and now is,
   in `.editorconfig` — so the style is a property of the repo rather than of
   whichever `shfmt` version happens to be installed. Caveat found later:
   passing any printer flag on the command line **disables `.editorconfig`
   lookup**, so `shfmt -ci` measures 1332, worse than no flag. Tuning must live
   in `.editorconfig`.
2. **One-line functions — deliberate, and confined to `tests/`.** 615 of the
   807 residual lines are `tests/*.sh`, where `shfmt` expands multi-statement
   one-liners (`bad()`, `hard()`, `soft()`, `skip()`, `local x="$1"; shift`) and
   drops column padding. That idiom is what lets a long run of assertions read
   as a scannable list. Note `shfmt` *preserves* single-statement one-liners, so
   `pass()` is untouched — the disagreement is narrower than first stated.
3. **`build_files/build.sh` — shfmt was right (216 lines).** The
   `FIREWALL_DAEMON` wrap added in the parameterized-firewall work left the
   whole OpenSnitch block's body at **column 0** inside its `if`, an artifact of
   keeping that review diff small. This was a genuine readability defect, not a
   style preference, and the blanket "don't run `just format`" rule was
   concealing it. Fixed by indenting lines 295–376 directly — verified
   whitespace-only (`git diff -w` empty) and semantics-preserving (`shfmt -mn`
   output byte-identical before and after), which is a stronger guarantee than a
   rebuild for a pure-whitespace change. build.sh drift: 216 → 69.

**Lesson for future readers:** do not cite the `tests/` idiom to dismiss a
formatting complaint outside `tests/`. The remaining 192 non-test lines are
*undecided*, not defended.

**What changed:** `.editorconfig` added (pins 4-space, plus `end_of_line`,
`insert_final_newline`, `charset`). `docs/CONTRIBUTING.md` and `CLAUDE.md` no
longer claim shell must be `just format`-clean and now say plainly not to run
`just format` over existing files. The `format` recipe carries the same warning
at its definition, so it is visible at the point of use. `just lint`
(shellcheck) is and remains the enforced gate.

**Deliberately not done:** no repo-wide reformat, no `.git-blame-ignore-revs`,
no `shfmt --diff` CI gate. Those all presuppose that shfmt's output is the
target, which this resolution rejects. If a future contributor wants
shfmt-clean as a hard standard, that is a real choice — but it costs an 807-line
mechanical diff and the loss of the compact-helper idiom, and should be decided
deliberately rather than by running `just format` once and committing it. Tuning
does not offer a cheap middle: even at `-i 4 -ci -kp -sr -bn` the floor is ~805,
because `shfmt` has no flag to preserve multi-statement one-liners.

**Still open (deliberately not taken here):** scoping the `format` recipe to
`build_files/`, `scripts/`, `installer/` and excluding `tests/`. That would make
`just format` safe to run instead of a recipe whose own comment says not to run
it, and would cover the 192 undecided lines. It changes standing policy, so it
wants an explicit decision rather than being folded into a correction.

---

<details>
<summary>Original problem statement (2026-08-05), kept for context</summary>

**Problem:** `docs/CONTRIBUTING.md` says shell must be `just format`-clean.
It never has been. Measured 2026-08-05 with `shfmt` 3.7.0, **every** `*.sh`
file in the repo is unformatted, totalling ~1265 diff lines:

```
build_files/build.sh                            294
tests/smoke.sh                                  377
installer/src/build.sh                           24
tests/boot-check.sh                             174
tests/portmaster-vm-gate.sh                     175
scripts/tower-diagnostic.sh                      93
build_files/firewall/portmaster.sh               62
tests/test-base-diff.sh                          53
installer/src/titanoboa_hook_preinitramfs.sh     13
```

The drift is not recent: `git show HEAD:tests/smoke.sh | shfmt -d -` returns
303 lines on its own. Nothing enforces it — `just lint` runs only
`shellcheck`, `just format` *writes* rather than checks, and no CI job runs
either. So the stated convention and the actual tree have never agreed.

**Why it's worth doing:** the current state actively penalises whoever fixes
it. Any feature branch that runs `just format` folds ~1265 lines of mechanical
reformatting into its diff and buries the real change, so the rational move on
every individual branch is to skip it — which is exactly why it has never been
done. It also makes "is this branch format-clean?" unanswerable as a review
question, which an agent cannot resolve on its own without silently
reformatting files outside its change.

Ranked last deliberately: this is a convention-vs-reality mismatch, not a bug
(item 1), a missing guardrail (item 2), a missing test (item 3), or a
structural entanglement (item 4).

**Acceptance criteria:**
- Do the reformat as its **own commit touching nothing else**, so it can be
  reviewed as "mechanical, no behaviour change" and skipped with
  `git blame --ignore-rev`. Add its SHA to a `.git-blame-ignore-revs` file.
- Add a **non-writing** check so it cannot drift again — either a
  `just format-check` recipe running `shfmt --diff`, or fold `shfmt --diff`
  into `just lint` beside `shellcheck`. Update the AUTO-GENERATED commands
  block in `docs/CONTRIBUTING.md` if a recipe is added.
- Wire that check into `build.yml` (or a lightweight lint workflow) so CI
  catches drift. It is offline and fast, unlike the smoke gate.
- Add an `.editorconfig` pinning indent width and related settings. Today the
  style is whatever `shfmt`'s defaults happen to be for the installed version,
  which makes the convention silently version-dependent.
- Do **not** bundle this with a functional change.

**Files:** all nine `*.sh` files (reformat commit), `Justfile` (check recipe),
`docs/CONTRIBUTING.md`, `.github/workflows/`, `.editorconfig` (new),
`.git-blame-ignore-revs` (new).

</details>

---

## Items considered and deliberately not ranked above

- **Base-image digest pinning (Section 4 of `downstream-change-tracking.md`):**
  already identified and explicitly deferred by the repo's own maintainer as
  "a deliberate call" pending Layers 1–2 landing (which have landed). This is
  a legitimate next step but is a **judgment call about tradeoffs** (PR-per-
  bump noise vs. attribution), not a chokepoint that blocks agent autonomy —
  the existing floating-tag + promotion-gate design already lets an agent
  verify safety before any promotion. Revisit once items 1–5 land.
- **Splitting `docs/RUNBOOK.md`'s hardware facts into a machine-readable
  ledger:** genuinely nice-to-have (a single grep-able source for "what CPU/
  NVMe/codec is this box" instead of scattered prose), but it's information
  that's already documented, just not in the ideal shape — lower leverage
  than the five items above, which are either bugs (item 1), missing
  guardrails (item 2), missing tests (item 3), or a structural entanglement
  (item 4). Worth doing after the above.

---

## Security note (as requested)

`cosign.key` sits in the repo root but is **not committed** — verified via
`git ls-files` (only `cosign.pub` is tracked) and `git log --all -- cosign.key`
(empty; it has never been part of any commit). It's correctly listed in
`.gitignore`. The actual signing secret CI uses is the `SIGNING_SECRET`
repository secret (per `.github/workflows/build.yml` and `docs/RUNBOOK.md`'s
rotation instructions), not this file. No rotation action is needed on that
basis alone, but flag it for a human to confirm: this repo has not been
verified to be a git repo with any remote push history reviewed here — if
`cosign.key` was ever added and later removed with `git rm`, it could still
exist in an older commit's tree even though `git log --all -- cosign.key`
came back empty in this check (it didn't, but a human should independently
run `git log --all --full-history -- cosign.key` and, if paranoid, `git grep`
across all refs before treating this as fully settled). **Agents must never
add, force-add, or otherwise stage `cosign.key`, and must never remove it
from `.gitignore`.**
