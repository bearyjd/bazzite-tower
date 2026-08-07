# Plan — split `build_files/build.sh` into per-concern scripts

Scopes `.agent_native/agent_roadmap.md` item 4, the last open roadmap item.
Written 2026-08-07. Not yet implemented.

## The spec is stale — read this before the roadmap entry

Item 4 describes a **265-line file with seven concerns** and proposes a nine-file
split. Measured 2026-08-07:

| Source | Claims | Actual |
|---|---|---|
| `agent_roadmap.md` item 4 | 265 lines, 7 concerns | **380 lines, 18 sections** |
| `docs/CODEMAPS/image-build.md:15` | "309 lines" | " |
| `docs/CODEMAPS/architecture.md:33` | "276 lines" | " |

Three documents, three different wrong numbers. OpenSnitch, Portmaster, the
firewall parameterisation, Cockpit, SMART and the i915 watcher all landed after
item 4 was written. The nine filenames it proposes no longer map onto the file.
Fix the counts in the same change.

## What makes this safer than it looks

- **No shell state crosses a proposed file boundary.** Zero function definitions.
  The only variable assignments are `docker-ce.repo` heredoc content (not shell
  state at all) and three real ones at 314-316 (`opensnitch_version`,
  `opensnitch_rpm`, `opensnitch_sha256`). Those three are consumed only within
  the OpenSnitch block, which lands whole in `95-firewall.sh`, so nothing is read
  across a boundary.

  An earlier draft claimed "zero top-level variable assignments". That was wrong,
  and the way it was wrong is instructive: the survey grepped `^[a-z_]+=`, which
  anchors at column 0, and the OpenSnitch block was indented by PR #38 earlier
  the same day. The repo's own fix hid the evidence from the repo's own survey.
  Grep for `^\s*[a-z_][a-z0-9_]*=` when re-checking.
- **The section banners are already the seams.** All 18 are `# ── … ──` headers;
  the split is a pure line-range extraction, not a rewrite.
- **A precedent exists.** `build_files/firewall/portmaster.sh` (60 lines) is
  already a separate script.

## What makes it risky

- **Execution order is load-bearing.** The sysusers fixup (112-142) sits between
  the Docker install and libvirt daemon enablement. Any grouping of
  non-contiguous sections silently reorders build steps. **Every file below
  covers one contiguous range.** This is the single most important constraint.
- `build.sh` is the build entry point, the highest-consequence file in the repo.

## Decision: numbered `build.d/`, executed with `bash`

Chosen 2026-08-07 over (a) `source` and (b) feature directories.

```
build_files/
  build.sh                    <- thin runner (keeps lines 1-4)
  build.d/
    10-virt-packages.sh  …  99-cleanup.sh
  firewall/portmaster.sh      <- stays where it is
```

Runner body: `for f in /ctx/build.d/*.sh; do bash "$f"; done`

- Numbers keep execution order visible in the filename. Feature directories
  (`virt/`, `docker/`) read better but make order invisible, and order matters.
- `bash` per script gives real isolation: a stray `cd` or variable in one script
  cannot leak into the next. `source` would match the existing portmaster
  precedent more closely but gives up that isolation.

## File map — every range contiguous, order preserved

Lines 1-4 (shebang, title comment, `set -euo pipefail`) stay in the runner.
Ranges below cover 5-380 exactly, with no gaps and no overlap (376 lines).

| File | Lines | Src range | Concern |
|---|---:|---|---|
| `10-virt-packages.sh` | 16 | 5-20 | QEMU / libvirt / KVM stack |
| `20-dev-tooling.sh` | 11 | 21-31 | DX-equivalent dev tooling |
| `30-docker-ce.sh` | 80 | 32-111 | Docker CE repo + install |
| `40-sysusers-fixup.sh` | 31 | 112-142 | sysusers / orphan-shadow workaround |
| `50-docker-networking.sh` | 5 | 143-147 | `iptable_nat` at boot |
| `60-libvirt-services.sh` | 53 | 148-200 | modular daemons, NAT autostart, polkit, first-boot oneshot |
| `70-guards-monitoring.sh` | 26 | 201-226 | Wi-Fi guard, SMART, Cockpit |
| `80-ras-microcode.sh` | 20 | 227-246 | RAS / MCE, microcode |
| `85-i915-watcher.sh` | 11 | 247-257 | i915 resume-regression watcher |
| `90-power-thermal.sh` | 17 | 258-274 | CPU power/thermal baseline |
| `95-firewall.sh` | 104 | 275-378 | firewall dispatch **and** OpenSnitch |
| `99-cleanup.sh` | 2 | 379-380 | `dnf clean all` |

`40-sysusers-fixup.sh` is isolated on purpose — item 4 names it the most fragile
piece and it stays exactly one concern.

`95-firewall.sh` deliberately keeps the `case` dispatch (275-292) together with
the OpenSnitch `if` (293-378). Both branch on `FIREWALL_DAEMON`; splitting them
would spread a single conditional across two files. Keeping them together
dissolves the entanglement rather than engineering around it.

## Three hazards, all known before starting

1. **`portmaster.sh` has no `set -euo pipefail`.** It is
   `# shellcheck shell=bash` and inherits the flag today by being `source`d into
   `build.sh`. Under `bash`-per-script it would run **without `set -e`**, so a
   failing command inside it would no longer fail the build. Add the line to
   `portmaster.sh` and to all 12 new scripts.
2. **`FIREWALL_DAEMON` must still reach `95-firewall.sh`.** The Containerfile
   passes it as a command-prefix env var on the `RUN`
   (`FIREWALL_DAEMON="${FIREWALL_DAEMON}" /ctx/build.sh`), so it is in the
   runner's environment and a child `bash` inherits it. Verify, do not assume.
3. **The portmaster `source` path.** `build.sh:285` uses
   `source "$(dirname "${BASH_SOURCE[0]}")/firewall/portmaster.sh"`. Once that
   line lives in `build.d/95-firewall.sh`, `dirname` resolves to `build.d/`, not
   `build_files/`. The path must become `../firewall/portmaster.sh` or an
   absolute `/ctx/firewall/portmaster.sh`.

## Verification — a mechanical proof, not N builds

This is a pure extraction, so equivalence is checkable without building:

```
strip shebang + `set` lines from `cat build.d/*.sh`
  == build.sh lines 5-380, byte for byte
```

Same shape as the `shfmt -mn` proof used on the `build.sh` indent fix (PR #38):
prove the text is unchanged, then build **once** to prove the plumbing works.

Full gate order:

1. concatenation-equivalence check (above) — seconds
2. `just lint` (shellcheck, now across 13 files) and `just check`
3. `just build` **once**, then `just smoke`
4. **`just build-portmaster-spike`, then `just smoke bazzite-tower portmaster-spike`**
5. CI on the PR: both build variants plus `boot-test.yml`, which triggers here
   because `build_files/**` is in its path filter

**Step 4 is not optional, and it is the step this plan originally missed.**
`FIREWALL_DAEMON` is set **nowhere** in `build.yml` — CI's two matrix legs vary
only `BASE_IMAGE`, and `just build` takes the default OpenSnitch selector. So
steps 1-3 and 5 combined never execute the `portmaster)` branch, and therefore
never execute the relocated `source ../firewall/portmaster.sh` line. Hazard 3
below is precisely a change to that line. Without step 4, the plan's own named
hazard ships unverified and breaks `just build-portmaster-spike` for whoever
next picks up the spike.

Raised by `/codex review` on the first draft. Confirmed by grepping `build.yml`
for `FIREWALL_DAEMON`: no match.

No assertion in `tests/smoke.sh` may be weakened. If the split is correct, the
existing suite passes untouched — that is the whole point.

## Also in scope

- `docs/CODEMAPS/image-build.md` — rewrite the "build.sh sections (in order, 309
  lines)" list to describe `build.d/`
- `docs/CODEMAPS/architecture.md` — `build_files/build.sh` "(276 lines)"
- `CLAUDE.md` "Repository layout" — currently calls build.sh "one 276-line script
  covering several unrelated concerns"; that structural note becomes obsolete
- **`README.md`** — has a whole `## build.sh` section stating *"It is where every
  customization in this image lives … Edit this file to change what's in the
  image"*, plus a file-table row. That instruction becomes actively wrong: the
  answer will be "edit the right file in `build.d/`".
- **`docs/downstream-change-tracking.md`** — carries `build.sh` **line ranges** at
  lines 40, 46 and 56 (`~112-131`, `~143-158`, `~173-182`). Two are already stale
  against the current file (polkit is 183-193, not ~173-182) and the split
  invalidates all three. Replace line ranges with `build.d/` filenames, which
  cannot drift the same way.
- `agent_roadmap.md` item 4 — mark DONE, record the stale-spec correction

README and downstream-change-tracking were missed by the first draft and found by
`/codex review`. Note the pattern: both encode *"the customizations live in one
file"* as an assumption, which is exactly what this change falsifies. Grep for
`build.sh` repo-wide before declaring the doc sweep complete.

## Out of scope

- Any behaviour change. If a section looks wrong, note it, do not fix it here.
- Reordering build steps, merging `dnf install` calls (there are **9**: lines 7,
  22, 105, 214, 224, 236, 245, 264, 312), or consolidating the 16 scattered
  `systemctl enable` calls. All tempting, all separate changes.
- Moving `firewall/portmaster.sh` into `build.d/`. It is conditionally sourced,
  not unconditionally run, so it does not belong in a `*.sh`-glob directory.

## Effort

Extraction is mechanical (`sed` line ranges). The work is the three hazards plus
the codemap updates. One build cycle. Roughly 15 minutes of agent time plus the
build.
