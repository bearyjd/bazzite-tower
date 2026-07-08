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

## 2. Write a bug-report triage protocol into `CLAUDE.md` (small effort, largest attention savings)

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

## 4. Split `build_files/build.sh` into per-concern scripts (medium effort, fixes the main structural obstacle)

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

---

## 5. Add a fast, buildless Containerfile lint (small-medium effort, closes a verification gap)

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
