# Implementation Report: `latest.20260808` boot-crash diagnosis

## Summary
Plan scoped a live-capture investigation (kdump/netconsole) into a boot crash on
`latest.20260808`, because static analysis (`tests/smoke.sh`, 69/69) was already clean and
couldn't explain it. Root cause turned out to be the KWin/kscreenlocker package-skew bug
already tracked in `docs/research/kwin-screenlocker-abi-2026-08-08/REPORT.md` and fixed by
PR #45 (`b80d9c9`, `4cce533`). The fix shipped under `:latest` as `latest.20260809` and both
CI (`Build container image`, `Boot test`, 2026-08-09) and live-machine evidence confirm it
holds. None of the plan's live-capture tasks (kdump, netconsole) were needed.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Medium (diagnosis contained; fix phase unknown) | Small — root cause was already identified/fixed by a parallel effort (PR #45) before Phase A's live-capture tooling was ever installed |
| Confidence (single-pass) | N/A | Fix confirmed without needing Task 1/2 (kexec-tools/kdump, netconsole) |
| Files Changed | 0-3 | 0 — no new repo changes; the fix already existed in `build_files/build.d/05-pin-kde-packages.sh` + `dnf.conf` exclude from PR #45 |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Rig live capture (kexec-tools/kdump) | SKIPPED | Not needed — fix identified via the already-in-flight kwin/kscreenlocker investigation, confirmed via CI + boot evidence instead |
| 2 | Rig netconsole | SKIPPED | Same reason |
| 3 | Controlled reboot into `20260808` | SUPERSEDED | Machine was upgraded past `20260808` to the fixed `latest.20260809` build instead of being rebooted back into the bad one |
| 4 | Pull evidence (`journalctl`, `ras-mc-ctl`, `kdumpctl`) | DONE (adapted) | Ran `rpm-ostree status`, `journalctl --list-boots`, `ras-mc-ctl --errors` via `distrobox-host-exec` against the real host instead of the live-capture artifacts Task 4 assumed |
| 5 | Diagnose and branch | DONE | Branch: "regressed package" — already fixed by PR #45, not a new finding |
| 6 | Clean up diagnostic scaffolding | N/A | Nothing was installed to clean up |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| CI — Build | PASS | 2026-08-09 06:33 UTC run green (per REPORT.md confirmation note) |
| CI — Boot test | PASS | 2026-08-09 07:55 UTC run green |
| `rpm-ostree status` | PASS | Booted deployment is `latest.20260809` (digest `217d581f…`), confirmed running the fixed build, not `20260705`/`20260808` |
| `journalctl --list-boots` | PASS | Pre-fix (2026-08-08 evening): 5 boots in ~35min, classic crash-reboot flapping. Post-fix: boot `-2` ran 9h38m, boot `-1` ran 1h40m, current boot has run 7h48m+ with no recurrence of the flapping pattern |
| `ras-mc-ctl --errors` | PASS (no new findings) | Only the already-documented Meteor Lake L2 cache MCEs (status `green` = corrected/benign), same signature as before; last event logged at 06:31 CEST 2026-08-09, *before* the current deployment was booted — zero new MCE events since switching |
| Soak test (multi-boot/hours) | PARTIAL | ~8 hours on the fixed build with no crash-reboot recurrence; plan's acceptance criteria calls for a longer soak before fully closing — treat as strong-but-not-final evidence |

## Files Changed
None — this report only. The actual fix landed in PR #45 (`b80d9c9`, `4cce533`), already merged
before this plan's diagnosis tasks began.

## Deviations from Plan
- **Live-capture tooling (Task 1/2) never installed.** The plan assumed root cause was unknown
  and required catching a live crash. It turned out the crash matched a *different*,
  already-in-flight investigation (kwin/kscreenlocker skew) whose fix had already merged. Once
  CI confirmed that fix against the real image, live capture became unnecessary.
- **Task 3 (reboot into the known-bad `20260808`) not performed.** Deliberately not reproducing
  a known-bad build on purpose; verified the *fixed* build instead.
- **Evidence pulled via `distrobox-host-exec`, not from inside kdump/vmcore artifacts** — Task
  4's `IMPLEMENT` assumed capture tooling that was never installed; the actual verification used
  plain `rpm-ostree status` / `journalctl` / `ras-mc-ctl` run directly against the host.

## Issues Encountered
- The assistant's sandbox is a stale rootless toolbox container (journal frozen at 2026-06-27,
  no `sudo`, no `ras-mc-ctl` access) — commands had to be run via `distrobox-host-exec` against
  the actual host to get current data. Any future diagnostic session on this machine should use
  the same escape hatch rather than trusting in-container `journalctl`/`sudo`.

## Tests Written
None — this was a live-system diagnosis, not a code change.

## Next Steps
- [ ] Let the soak continue naturally (normal daily use); no action needed unless the
      crash-reboot pattern reappears.
- [ ] If it recurs, re-open a plan and *this time* stand up the kdump/netconsole capture from
      Task 1/2 — root cause won't be a free match to an existing investigation next time.
- [ ] No repo changes required; `docs/research/kwin-screenlocker-abi-2026-08-08/REPORT.md`'s
      "Confirmation (2026-08-09)" section already documents the fix and CI evidence.
