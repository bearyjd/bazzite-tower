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

**Status (2026-11-27): items 1–8 resolved. Item 9 (simplification) has its ranked
proposal and is being executed in reviewed waves; see item 9.**

Two lessons from the completed items still apply:

- **Specs in this file went stale faster than the code.** Items 4 and 5 both
  described a repo that no longer existed by the time they ran (item 4 said 265
  lines / 7 concerns against an actual 380 / 18; item 5 prescribed a `hadolint`
  invocation that hangs). Two codemaps carried two further wrong line counts.
  **Re-measure before implementing an entry here; do not trust its numbers.**
- **Prefer filenames to line numbers in docs.** The line ranges in
  `docs/downstream-change-tracking.md` were already drifting before item 4
  touched anything. They are now `build.d/` filenames, which cannot.

Append new work as numbered items and keep the closed items as history.

---

## 1. Reconcile the dangling `i915-resume-fix-check.timer` reference — DONE (2026-07-07), later superseded by item 8

`Containerfile` cited a host watcher timer that did not exist. Implemented it as
`i915-resume-fix-check.{service,timer}` + a libexec helper, enabled in
`build.sh`, with `tests/smoke.sh` assertions. Item 8 removed all of it
(2026-10-10), so the files named here are gone. Lesson that still applies: do not
leave a comment that claims a safety net exists unless the repo can show it.

---

## 2. Write a bug-report triage protocol into `CLAUDE.md` — DONE (shipped earlier; verified 2026-08-06)

`CLAUDE.md` already has "Triaging a bug report" (build/CI-reproducible vs
hardware-log-dependent) and "Hardware-specific tuning" (no hardware value changes
without evidence or an explicit instruction). It points at the RUNBOOK
health-check table and `scripts/tower-diagnostic.sh` rather than copying them, on
purpose: a second copy would drift. `docs/research/i915-bug-report/` is the
write-up template.

---

## 3. Give `ci/base-diff.py` a fixture-based unit test — DONE (2026-07-07)

Added `tests/test-base-diff.sh` with fixture manifest pairs under
`tests/fixtures/base-diff/` (7 scenarios, 12 assertions), run by `just test-ci`.
Plain bash was chosen over pytest because the repo has no Python dependency
manifest. The recipe is in the `AUTO-GENERATED:commands` block of
`docs/CONTRIBUTING.md` and the README.

---

## 4. Split `build_files/build.sh` into per-concern scripts — DONE (2026-08-07)

`build.sh` is now a thin runner that `bash`-executes `build_files/build.d/*.sh` in
filename order (12 scripts at the time; the set has grown since). Plan:
`.claude/PRPs/plans/build-sh-split.plan.md`. The extraction was proven lossless
(concatenated bodies matched the old file byte for byte), and contiguous ranges
were used because order is load-bearing (`40-sysusers-fixup.sh` after
`30-docker-ce.sh`, before `60-libvirt-services.sh`). Docs now cite `build.d/`
filenames, not line ranges. Lesson: the spec here was stale (265 lines / 7
concerns vs an actual 380 / 18); re-measure before implementing.

---

## 5. Add a fast, buildless Containerfile lint — DONE (2026-08-07)

Shipped as `just lint-containerfile`: hadolint 2.14.0 from a digest-pinned
container, run in seconds, not a CI gate. The `Containerfile` is copied to a temp
dir and linted by path. hadolint's stdin form (`podman run -i ... < Containerfile`)
hangs and looks like it printed output, which gives a false pass, so do not use it.
Verified both ways: clean input exits 0, known-bad input exits 1. Also: a long
comment directly above a `just` recipe becomes its `--list` doc string, so keep a
blank line between the rationale and a one-line summary.

---

## 6. Reconcile `just format` with the repo's actual style — RESOLVED (2026-08-06)

Do not reformat the tree. Indentation is pinned to 4 spaces in `.editorconfig`;
that is the only place shfmt tuning works, because any printer flag on the command
line disables `.editorconfig` lookup. Of ~807 residual shfmt diff lines, 615 (76%)
are `tests/*.sh`, where one-line helpers (`bad()`, `hard()`, `soft()`, `skip()`)
are deliberate; the other 192 are undecided opinions. `build_files/build.sh`'s
column-0 `if` body was a real defect and was indented directly. `just format` is
not a required gate. See `docs/CONTRIBUTING.md` "Code style".

---

## 7. Run the downstream OpenSnitch patch's tests somewhere automatic — DONE (2026-10-07)

`just test-snitchwatch-daemon-patch` (`tests/test-snitchwatch-daemon-patch.sh`)
applies `build_files/firewall/snitchwatch-system-daemon-shutdown.patch` to upstream
`b404c4c` and runs the Go (`-race`, then plain) and C harness tests, offline in a
`--network=none` container. CI runs it as
`.github/workflows/snitchwatch-daemon-patch.yml` on path-filtered PRs; it is not a
required check. Three known upstream `ui` race tests are skipped by name in the
`-race` pass only. Reintroducing the r2 `nfq_handle_packet` bug makes the gate
exit 1; a drifted Go toolchain exits 3 as `TOOLCHAIN DRIFT`. Background:
`docs/research/snitchwatch-system-bridge.md`.

---

## 8. Retire the i915 resume-regression watcher (item 1) — DONE (2026-10-10)

Removed `i915-resume-fix-check.{service,timer}`, its helper,
`build.d/85-i915-watcher.sh`, the smoke assertions, the `bazzite-tower-health` line
and the codemap rows. Its premise ended when the kernel moved to 7.2, which carries
the fix (`bc7ba80`, 2026-08-28; `docs/research/i915-bug-report/UPSTREAM-FIX-STATUS-2026-08-28.md`),
and it never ran: `OnBootSec=5min` had elapsed before the timer started, so
`OnUnitActiveSec=1d` never began. If resume trouble is suspected on 7.2:
`journalctl -k -b | grep -iE 'flip_done|dpll'`; `bootc rollback` is the mitigation.
**Lesson:** an "is it enabled" assertion cannot catch a timer that never fires.

---

## 9. Simplify the image codebase while preserving ThinkPad capabilities — IN PROGRESS (proposal delivered 2026-11-27)

**Status:** the ranked proposal (five read-only analyses plus a capability
inventory) was delivered on 2026-11-27 and is executed in reviewed waves: safety-net
tests first, then low-risk cuts, each as its own branch/PR with an independent
review. Hardware-fact tuning is untouched. The proposal's full text is the local,
untracked `.omc/results/item9-proposal-2026-11-27.md`.

- **Scope (original spec):** inventory the image's capabilities and ThinkPad P1
  customizations (graphics, suspend/resume, hardware tuning, Snitchwatch, update and
  rollback) and trace each to its build steps, files and checks; look for duplicate
  logic, obsolete compatibility paths and local code upstream Bazzite/Fedora now
  covers (verify against the pinned base, both image profiles); deliver a ranked
  proposal with estimated reduction, affected capabilities, regression risks and a
  verification plan (offline / built image / ThinkPad), and bring it back for review
  before editing implementation code.
- **Owner decisions, 2026-11-27:** the Portmaster spike is abandoned; the r13
  candidate/release pipeline is retired; Docker stays (used sometimes); the ISO path
  is verified; stale docs are deleted, not archived.

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
