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
| `just build-portmaster-spike` | Build the disabled Portmaster VM-spike image; never a default/host image |
| `just smoke` | Offline-assert the built image — **the exact CI gate** (`tests/smoke.sh`) |
| `just build-qcow2` | Turn the image into a bootable qcow2 via bootc-image-builder |
| `just run-vm-qcow2` | Boot the qcow2 in qemu; browser console at `localhost:8006` |
| `just spawn-vm` | Boot via `systemd-vmspawn` instead (no browser console) |
| `just run-vm-ssh` | Boot a login/SSH-capable VM (e.g. the Portmaster spike gate) — prints the `ssh` command, no console needed |
| `just build-iso-live` | Build the `installer/` payload + titanoboa live/installer ISO → `./output/` |
| `just lint` | `shellcheck` every `*.sh` |
| `just lint-containerfile` | `hadolint` the `Containerfile` in seconds, no image build — not a CI gate |
| `just format` | `shfmt --write` every `*.sh` — **not** a required gate, see "Code style" |
| `just check` / `just fix` | Check / auto-format Just syntax |
| `just test-ci` | Fixture-based tests for `ci/base-diff.py` (offline, no live upstream manifests needed) |
| `just clean` | Remove build artifacts (`output/`, manifests, `*_build*`) |
<!-- END AUTO-GENERATED:commands -->

Run `just` with no arguments for the full list. Per-recipe detail and the build-time
environment variables (`DEFAULT_TAG`, `BIB_IMAGE`) live in the
[README "Justfile Documentation"](../README.md#justfile-documentation) section — that
table is the single source of truth; don't duplicate it here.

## Code style

- **Bash** — must pass `just lint` (shellcheck). Scripts use `set -euo pipefail`.
  Indentation is 4 spaces, pinned in `.editorconfig`.
  **`just format`-clean is *not* a requirement**, and running `just format` on
  existing files is discouraged — it would restructure ~807 lines of working,
  shellcheck-clean shell. The reason is not uniform across the tree, and it
  matters which half you are in:

  | Where | Lines | Why it differs from shfmt |
  |---|---:|---|
  | `tests/*.sh` | 615 (76%) | **Deliberate.** shfmt expands multi-statement one-line functions (`bad()`, `hard()`, `soft()`, `skip()`) and drops column padding; keeping them on one line is what lets long runs of assertions read as a scannable list. |
  | everywhere else | 192 | **Undecided.** Ordinary shfmt opinions — redirect spacing, case indent. Nobody has ruled on these; they are not a defended style. |

  Do not invoke the `tests/` idiom to dismiss a formatting problem outside
  `tests/`. In `build_files/build.sh` shfmt was correct — the `FIREWALL_DAEMON`
  wrap had left an `if` body at column 0 — and the right fix was to indent that
  block directly, not to reformat the tree or to leave it alone.

  Tuning does not rescue a full adopt either: even at `-i 4 -ci -kp -sr -bn` the
  floor is ~805, because shfmt preserves *single*-statement one-liners but has no
  flag for multi-statement ones. Note also that passing any printer flag on the
  command line disables `.editorconfig` lookup, so such flags belong in
  `.editorconfig`, not the Justfile. `just format` remains available for new
  files where you want a starting point.
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
- [ ] `just lint-containerfile` is clean *if you touched the `Containerfile`*
      (seconds, no build — cheaper than finding out from a failed CI build)
- [ ] Touched behaviour is reflected in the README and/or CODEMAPS
- [ ] Commit subjects follow the convention above

## What CI runs on your PR

`build.yml` (build → **smoke gate**, no push on PRs) and `boot-test.yml` (when build
paths change) run automatically. The smoke gate runs *before* any push, so a broken
image is never published. Failure model and the full workflow matrix:
[docs/CODEMAPS/ci-cd.md](./CODEMAPS/ci-cd.md) and
[docs/downstream-change-tracking.md](./downstream-change-tracking.md).
