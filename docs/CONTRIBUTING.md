# Contributing

`bazzite-tower` is a personal bootc OS image, but every change goes through a real
CI gate before `:latest` is published. This guide covers the local loop; for the
*why* behind the image's choices read the [README](../README.md), and for how the
pieces fit together read [docs/CODEMAPS/](./CODEMAPS/architecture.md).

## Prerequisites

| Need | For |
|---|---|
| `podman`, `just`, `git` | building and smoke-testing the image |
| `shellcheck`, `shfmt` | `just lint` / `just format` |
| KVM + `sudo` (+ tens of GB free) | disk-image / VM / ISO builds (`build-qcow2`, `run-vm-*`, `build-iso-live`) |

`bootc-image-builder` and `qemux/qemu` are pulled on demand by the recipes — no manual install.

## Local loop

<!-- AUTO-GENERATED:commands (from Justfile) -->
| Command | Purpose |
|---|---|
| `just build` | Build the container image locally (adds `SHA_HEAD_SHORT` when the tree is clean) |
| `just smoke` | Offline-assert the built image — **the exact CI gate** (`tests/smoke.sh`) |
| `just build-qcow2` | Turn the image into a bootable qcow2 via bootc-image-builder |
| `just run-vm-qcow2` | Boot the qcow2 in qemu; browser console at `localhost:8006` |
| `just spawn-vm` | Boot via `systemd-vmspawn` instead (no browser console) |
| `just build-iso-live` | Build the `installer/` payload + titanoboa live/installer ISO → `./output/` |
| `just lint` | `shellcheck` every `*.sh` |
| `just format` | `shfmt --write` every `*.sh` |
| `just check` / `just fix` | Check / auto-format Just syntax |
| `just clean` | Remove build artifacts (`output/`, manifests, `*_build*`) |
<!-- END AUTO-GENERATED:commands -->

Run `just` with no arguments for the full list. Per-recipe detail and the build-time
environment variables (`DEFAULT_TAG`, `BIB_IMAGE`) live in the
[README "Justfile Documentation"](../README.md#justfile-documentation) section — that
table is the single source of truth; don't duplicate it here.

## Code style

- **Bash** — must pass `just lint` (shellcheck) and be `just format`-clean (shfmt).
  Scripts use `set -euo pipefail`.
- **Just** — `just check` must pass; `just fix` formats.
- **kargs / units / TOML** — one concern per file (e.g. i915 display and suspend
  are separate `kargs.d/*.toml` fragments) so each can change or be reverted alone.
- Keep [CODEMAPS](./CODEMAPS/architecture.md) token-lean; update the relevant
  codemap in the same change that alters its subject.

## Commits & PRs

Conventional-commit subjects (`feat:`, `fix:`, `docs:`, `ci:`, `refactor:`, `chore:`),
scoped where useful (`fix(ci): …`, `feat(wifi-debug): …`).

**Before opening a PR:**

- [ ] `just smoke` passes locally (the image still satisfies the gate)
- [ ] `just lint` and `just check` are clean
- [ ] Touched behaviour is reflected in the README and/or CODEMAPS
- [ ] Commit subjects follow the convention above

## What CI runs on your PR

`build.yml` (build → **smoke gate**, no push on PRs) and `boot-test.yml` (when build
paths change) run automatically. The smoke gate runs *before* any push, so a broken
image is never published. Failure model and the full workflow matrix:
[docs/CODEMAPS/ci-cd.md](./CODEMAPS/ci-cd.md) and
[docs/downstream-change-tracking.md](./downstream-change-tracking.md).
