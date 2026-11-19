# Codemap: Cloison variant image (M1 scaffold)

Plan: `docs/prp/cloison-kvm-compartments.md` (sections 4, 9, 10). Status: **scaffold only**.

## Status of the plan
A Codex critic (2026-11-16) returned **REWORK** on the plan. The plan must be
revised before M2 starts. Findings that shape this scaffold:
- 2: signature-policy bootstrap is missing (the daily deployment must trust the
  new repo before the first enforced switch). Therefore nothing is published
  and `policy.json` / `registries.d` are untouched.
- 12: milestone gates contradict each other, and smoke assertions that are
  unconditional conflict with later milestones. Therefore `smoke-cloison.sh`
  is M1-specific, and later milestones must change it explicitly.
- 13: the parent must be a recorded, verified digest, not ":latest just
  published". Therefore `PARENT_IMAGE` is required and digest-only.

## Owner decisions
- **D1**: separate repo name `ghcr.io/bearyjd/bazzite-tower-cloison`.
- **D5**: local builds only until M3. No CI workflow, no publish.

## What exists
| Path | Role |
|---|---|
| `Containerfile.cloison` | `FROM ${PARENT_IMAGE}` (digest enforced by a RUN backstop); labels `io.github.bearyjd.cloison.parent[-source-commit,-profile]`; writes `/usr/share/cloison/parent-digest`; `bootc container lint` |
| `cloison/build.sh` | Runs `cloison/build.d/*.sh` in filename order (mirrors `build_files/build.sh`) |
| `cloison/build.d/10-cloison-marker.sh` | Writes `/usr/share/cloison/variant` = `cloison-m1-scaffold`; nothing else |
| `cloison/system_files/usr/share/cloison/README` | One-paragraph note |
| `tests/test-cloison-isolation.sh` | Daily `Containerfile`, `build_files/`, `system_files/` must exist and not mention cloison; `Containerfile.cloison` FROM only `${PARENT_IMAGE}`/`scratch`; Justfile/workflow mentions are NOTES, a workflow referencing `Containerfile.cloison` FAILS (no CI leg until M3) |
| `tests/test-cloison-isolation-mutations.sh` | Applies each isolation violation to a temp copy and asserts the isolation test fails |
| `tests/smoke-cloison.sh` | Offline, M1 assertions (marker, parent-digest, daily kargs present, no cloison kargs/units/presets, no vfio-pci.ids binding, usbguard not enabled; fails loudly on missing dirs/systemctl). **UNVERIFIED against a built image** |
| `just build-cloison <parent@sha256:...>` / `just smoke-cloison` | Local recipes; the build refuses a non-digest parent and checks the `cloison.parent` label equals it (host-side `podman inspect`) |

Usage: `just build-cloison ghcr.io/bearyjd/bazzite-tower@sha256:<64 hex> <source-commit> <profile>`.

## What does not exist (deliberately)
Daemon, guest templates, packages, enabled units, kargs, VFIO, USBGuard, CI
workflow, publishing, signing, trust bootstrap, `registries.d`/`policy.json`
entries, boot-check. Daily `Containerfile`, `build_files/`, `system_files/` and
all existing test assertions are unchanged.

## Notes
- The daily kargs already carry iommu/vfio fragments; the M1 smoke asserts no
  *new* Cloison-related ones, not their absence.
- Lint: `just lint-containerfile Containerfile.cloison`.
