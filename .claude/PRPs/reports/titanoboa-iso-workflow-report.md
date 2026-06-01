# Implementation Report: Titanoboa-based installable ISO workflow

## Summary
Scaffolded the titanoboa live-ISO path: a new `build-iso.yml` CI workflow (primary path, plain `ublue-os/titanoboa` action against the published image) and a local `just build-iso-live` recipe. Static validation passes. The runtime gate (does titanoboa build a bootable ISO from our image) is **not yet verified** — it is the plan's spike (Task 1) and needs a CI dispatch from `main` or a run on a KVM-capable machine.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Large | Large (unchanged) |
| Confidence (single-pass) | 6/10 | Accurate — stopped at the spike gate as designed |
| Files Changed | 4 (simple path) | 2 code + 2 PRP artifacts (docs deferred) |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | SPIKE: image builds bootable ISO? | NOT RUN | Runtime; needs CI-from-main or the P1. This is the gate. |
| 1b | Payload-builder fallback | N/A | Only if Task 1 fails |
| 2 | Create `build-iso.yml` | DONE | Primary path, titanoboa pinned `5c457c3…`, mirrors build.yml/build-disk.yml patterns. Statically valid. |
| 3 | `just build-iso-live` recipe | DONE (unverified) | Documented `main.sh` clone method; marked UNVERIFIED. Justfile parses. |
| 4 | Sync docs + memory | DEFERRED | Deliberate: won't claim "ISO works" before the spike proves it |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis | PASS | `build-iso.yml` YAML parses; perms `contents:read/packages:read/issues:write`; `just --list` parses with `build-iso-live` |
| Unit Tests | N/A | CI/shell workflow — no unit-testable logic |
| Build | NOT RUN | The "build" here = titanoboa producing an ISO; that is the spike |
| Integration (boot) | NOT RUN | ISO boot-in-VM is the hard acceptance gate (#3418); needs KVM hardware |
| Edge Cases | PARTIAL | SIGNING_SECRET gating + s3-toggle handled by mirrored patterns; not runtime-exercised |

## Files Changed

| File | Action | Lines |
|---|---|---|
| `.github/workflows/build-iso.yml` | CREATED | +150 |
| `Justfile` | UPDATED | +22 / -1 (flagged dead BIB `build-iso`, added `build-iso-live`) |
| `.claude/PRPs/plans/titanoboa-iso-workflow.plan.md` | (from /prp-plan) | — |
| `.claude/PRPs/reports/titanoboa-iso-workflow-report.md` | CREATED | this file |

## Deviations from Plan
- **Task 4 (docs) deferred.** Committing docs that say "live ISO works" before the spike verifies it would be a false claim. Docs flip after the spike passes.
- **Plan NOT archived.** The skill archives the plan on completion; this plan is spike-gated and not complete, so it stays in `plans/` (not `completed/`).
- **No `just build-iso-live` runtime check.** Can't run titanoboa in this sandbox (not the P1; heavy). Recipe is best-effort from titanoboa's documented `main.sh`, marked UNVERIFIED.

## Issues Encountered
- **Can't run the spike from here.** This environment is not the bazzite-tower deployment (missing kernel/grub/iso.yaml) and can't run a heavy titanoboa build. The spike must run via CI-from-main or on the P1.
- **Dispatch chicken-and-egg.** A new `workflow_dispatch`-only workflow isn't dispatchable from a feature branch (GitHub reads the workflow list from the default branch). To run the CI spike, `build-iso.yml` must first land on `main` — which is safe because it's dormant (dispatch/schedule only, never runs on push).

## Tests Written
None — CI/workflow change; verification is build + boot, not unit tests.

## Next Steps
- [ ] **Run the spike** (the actual gate), one of:
  - Merge `build-iso.yml` to `main` (dormant until dispatched), then `gh workflow run build-iso.yml -f platform=amd64` and watch — confirms the BUILD half.
  - Run `just build-iso-live` on the P1 — confirms BUILD + lets you boot-test the ISO.
- [ ] If the build fails on titanoboa's ISO contract → Task 1b (port bazzite `installer/`), re-scope XL.
- [ ] Once an ISO boots: do Task 4 (flip README/ci-cd/memory to "live ISO works").
- [ ] `/code-review` then `/prp-pr`.
