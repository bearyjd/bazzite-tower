# CLAUDE.md — bazzite-tower

Guidance for an AI agent working in this repository. Read this before making
changes. It reflects commands and conventions that actually exist in
`Justfile`, `.github/workflows/`, and `docs/` — nothing here is invented.
For the *why* behind design choices, read `README.md` first; for how pieces
fit together, read `docs/CODEMAPS/architecture.md`.

## What this repo is

A [bootc](https://github.com/bootc-dev/bootc) OS-image repo: a declarative
Fedora/Bazzite derivative. There is no application runtime, database, or
frontend — the "program" is a container image (`Containerfile` + `build_files/`
+ `system_files/`) that becomes a bootable OS on one specific machine (a
ThinkPad P1, Meteor Lake CPU, NVIDIA Optimus GPU). It publishes to
`ghcr.io/bearyjd/bazzite-tower`, cosign-signed by digest.

Because it targets one real, specific laptop, a lot of the tuning in this repo
(audio, suspend, thermal, NVMe, RAS/MCE) is **hardware-fact-derived**, not
generic best practice. See "Hardware-specific tuning" below before touching
any of it.

## Commands (from `Justfile` — do not invent new ones; extend this table if you add a recipe)

| Command | Purpose |
|---|---|
| `just build` | Build the container image locally with podman (adds `SHA_HEAD_SHORT` build-arg when the git tree is clean) |
| `just smoke` | Offline-assert the built image — **this is the exact CI gate** run in `build.yml` before push (`tests/smoke.sh`) |
| `just build-qcow2` | Turn the image into a bootable qcow2 via bootc-image-builder (needs sudo + KVM + tens of GB free) |
| `just run-vm-qcow2` | Boot the qcow2 in qemu; browser console at `localhost:8006` |
| `just spawn-vm` | Boot via `systemd-vmspawn` instead (no browser console) |
| `just build-iso-live` | Build the `installer/` payload + titanoboa live/installer ISO → `./output/` (UNVERIFIED path per `.claude/PRPs/reports/titanoboa-iso-workflow-report.md` — the runtime boot-in-VM gate has not been run) |
| `just lint` | `shellcheck` every `*.sh` in the repo |
| `just format` | `shfmt --write` every `*.sh` — **not** a required gate; do not run it over existing files (see "Code style") |
| `just check` / `just fix` | Check / auto-format `Justfile`/`*.just` syntax |
| `just clean` | Remove build artifacts (`output/`, manifests, `*_build*`) |

Run `just` with no arguments for the live list (`Justfile` is the source of
truth; `docs/CONTRIBUTING.md` has an `AUTO-GENERATED:commands` block that must
be kept in sync if you add/change a recipe — update both in the same change).

**Do not run `just build`, `just build-qcow2`, `just build-iso-live`, or
anything that invokes `podman build` unless the task explicitly requires
building the image.** These are slow, resource-heavy, and (for VM/ISO builds)
require sudo/KVM. Prefer static verification first (see "Verification loop"
below) and only build when a change genuinely needs the built image to
verify.

## Repository layout

- `Containerfile` — build entry: `FROM` base → `COPY system_files/` → `RUN
  build_files/build.sh` → `bootc container lint`.
- `build_files/build.sh` — all image customization (dnf installs, systemd
  unit enables, the sysusers/orphan-shadow workaround). Currently one 276-line
  script covering several unrelated concerns — see "Structural note" below
  before making an unrelated change land in the middle of someone else's
  section.
- `system_files/` — static content `COPY`ed verbatim to `/`: systemd units,
  `kargs.d/*.toml` fragments, libexec helpers, ujust recipes, sysctl/modprobe/
  journald drop-ins. **One concern per file** here — e.g. i915 display and
  suspend kargs are separate `.toml` fragments so each can be reverted alone.
  Follow this pattern for any new fragment.
- `installer/` — separate payload builder for the live/installer ISO
  (titanoboa input); has its own `Containerfile`.
- `disk_config/*.toml` — bootc-image-builder configs (qcow2/raw/iso).
- `ci/base-diff.py` — diffs the upstream base image's package manifest against
  the last-seen one, filtered to a "blast radius" regex of packages our
  customizations depend on. Powers `base-watch.yml`.
- `tests/smoke.sh` — offline, runs `podman run --rm -i <image> bash -s <
  tests/smoke.sh`. Asserts that everything `build.sh`/`system_files/` are
  *trying* to achieve is actually present in the built image (units enabled,
  users resolvable, kargs fragments present, etc.). This is the CI gate — a
  failure here means `:latest` is never updated.
- `tests/boot-check.sh` — runtime, runs inside the booted image via `podman
  exec` (systemd as PID 1 via `podman run --systemd=always`). Proves things
  actually *work*, not just that they're present — e.g. connects to
  `qemu:///system` end-to-end.
- `scripts/tower-diagnostic.sh` — full local health sweep (SOF audio, MCE/RAS,
  i915 resume, thermals, SMART, rpm-ostree). Run with `sudo` for complete
  output.
- `docs/RUNBOOK.md` — day-2 operations: install/switch, update, rollback,
  health checks, common issues, the audio/RAS/thermal design notes.
- `docs/CODEMAPS/` — token-lean architecture maps: `architecture.md`,
  `image-build.md`, `system-files.md`, `iso-build.md`, `ci-cd.md`,
  `dependencies.md`. **Update the relevant codemap in the same change that
  alters its subject** — this is an existing repo convention, not new.
- `docs/downstream-change-tracking.md` — the CI failure model: what breaks
  silently vs. loudly, and which layer (smoke gate / boot test / base-watch)
  catches each failure mode.
- `docs/research/i915-bug-report/` — a worked example of a hardware bug
  investigation (`REPORT.md`, `BISECT-RUNBOOK.md`, raw log captures). Use this
  as the template for any future hardware investigation.
- `.claude/PRPs/` — prior planning/report artifacts from past feature work
  (e.g. the titanoboa ISO workflow). Check here before starting related work —
  it may already document what's verified vs. not.
- `.agent_native/agent_roadmap.md` — a prioritized list of agent-readiness
  gaps in this repo (verification gaps, structural obstacles) with concrete,
  agent-startable acceptance criteria. Check it before assuming a gap you've
  found is undocumented.

## Verification loop (in order of speed/cost — prefer the cheapest that answers the question)

1. **`just lint` / `just check`** — shellcheck + Just-syntax check. Seconds,
   no container involved.
2. **`just smoke`** against an already-built local image, if one exists and
   is fresh — offline assertions, seconds.
3. **`just build`** — only when a change needs the actual image rebuilt to
   verify (e.g. a `build_files/build.sh` or `system_files/` change). Then
   run `just smoke` against the result. This is the same order `build.yml`
   runs in (build → smoke gate → push), so it mirrors CI exactly.
4. **`tests/boot-check.sh` / `boot-test.yml`** — only needed to prove
   *runtime* behavior (socket activation, service ordering) beyond what
   `tests/smoke.sh`'s on-disk checks can show. Heavier; CI runs it on
   image-affecting PRs and weekly, not on every change.
5. **VM/qcow2/ISO builds** (`just build-qcow2`, `just build-iso-live`) —
   reserve for changes that specifically touch the disk-image or ISO build
   path (`disk_config/`, `installer/`). Needs sudo + KVM; do not run
   speculatively.

A change is **not verified complete** until at minimum step 1 passes and, if
`build_files/` or `system_files/` changed, step 2 or 3 has run. Do not claim
a fix works based on reading the code alone when a cheap verification step is
available.

## Hardware-specific tuning — read before touching any of this

This image targets **one physical machine**. A meaningful fraction of the
repo (kargs in `system_files/usr/lib/bootc/kargs.d/`, the RAS/microcode/
thermal sections of `build_files/build.sh`, the SOF audio bypass, the NVMe
APST workaround, the base-image kernel pin in `Containerfile`) encodes
**hardware facts verified on that specific box**, not general Fedora/Bazzite
best practice. Examples already documented: Meteor Lake CPU cache MCEs,
Realtek ALC287 + TI TAS2781 side-codec, Samsung 990 EVO Plus NVMe APST
quirk, and a specific i915 kernel regression window (see
`docs/research/i915-mtl-resume-2026-06-20.md`).

**Rule:** never change a hardware-tuning value (a `kargs.d/*.toml` fragment,
the RAS/thermal/audio sections of `build.sh`, or a "verified" fact in
`docs/RUNBOOK.md`) without one of:
- an explicit instruction from the user to change the default, or
- concrete evidence for *this* machine (a `journalctl`, `smartctl`,
  `ras-mc-ctl`, or `/proc/cmdline` excerpt) that the current value is wrong.

If a bug report describes hardware behavior (audio glitches, resume
slowness, thermal throttling, SMART warnings, MCEs) and no such evidence is
attached, **ask for it** — point at the relevant row in `docs/RUNBOOK.md`'s
health-check table or run the matching section of
`scripts/tower-diagnostic.sh` — rather than guessing at a fix.

## Triaging a bug report

Classify first, before writing any code:

- **Build/CI-reproducible** — packaging, systemd unit presence/enablement,
  kargs fragment presence, Docker/libvirt setup, anything `tests/smoke.sh` or
  `tests/boot-check.sh` already assert or could be extended to assert. Fully
  agent-reproducible: build locally (or reason from the Containerfile/
  build.sh/system_files diff), run `just smoke`, extend the test if the
  regression isn't yet covered.
- **Hardware-log-dependent** — SOF audio storms, i915 resume timing, RAS/MCE
  decode, NVMe SMART/thermal behavior. **Not reproducible without either
  physical access to the ThinkPad P1 or the reporter's own log output.**
  Request the specific command from `docs/RUNBOOK.md`'s health-check table or
  `scripts/tower-diagnostic.sh` before proposing a fix.

`docs/research/i915-bug-report/` is a real worked example of the second
category done right: raw capture, root-cause writeup, bisect runbook. Follow
that shape for any new hardware investigation.

## Code style (from `docs/CONTRIBUTING.md`)

- **Bash** — must pass `just lint` (shellcheck). Indentation is 4 spaces,
  pinned in `.editorconfig`. **`just format`-clean is NOT a requirement and you
  should not run `just format` over existing files** — it would restructure ~807
  lines of working, shellcheck-clean shell. Be precise about why, because the
  reason differs by directory: **615 of those 807 lines (76%) are `tests/*.sh`**,
  where shfmt expands multi-statement one-line functions and drops column
  padding — and this repo deliberately keeps helpers like `bad()`, `hard()`,
  `soft()` and `skip()` on one line so long runs of assertions read as a
  scannable list. The other **192 lines are not a considered objection**, just
  shfmt opinions (redirect spacing, case indent) nobody has ruled on. So do not
  cite "the idiom" to wave away a formatting problem outside `tests/` — in
  `build_files/build.sh` shfmt was *right*, and the fix was to indent the
  column-0 `if` body directly rather than reformat the tree.
  Scripts use `set -euo pipefail` (or `-uo pipefail` where a script
  intentionally continues past failing checks to report all of them, e.g.
  `tests/smoke.sh`/`tests/boot-check.sh` — match the existing pattern in the
  file you're editing).
- **Just** — `just check` must pass; `just fix` formats.
- **kargs / units / TOML** — one concern per file (see "Repository layout"
  above).
- Keep `docs/CODEMAPS/` token-lean; update the relevant codemap in the same
  change that alters its subject.

## Commits & PRs (from `docs/CONTRIBUTING.md`)

- Conventional-commit subjects: `feat:`, `fix:`, `docs:`, `ci:`, `refactor:`,
  `chore:`, scoped where useful (`fix(ci): …`, `feat(wifi-debug): …`).
- Before opening a PR: `just smoke` passes locally, `just lint` and `just
  check` are clean, touched behavior is reflected in the README and/or
  CODEMAPS, commit subjects follow the convention above.
- `build.yml` (build → smoke gate, no push on PRs) and `boot-test.yml` (when
  build-affecting paths change) run automatically on PRs.

## Hard rules

- **Never commit, stage, or force-add `cosign.key`.** It is gitignored and
  currently untracked (verified via `git ls-files` and `git log --all --
  cosign.key`) — keep it that way. The actual CI signing secret is the
  `SIGNING_SECRET` repository secret (see `docs/RUNBOOK.md`'s rotation
  instructions), not this file. Never remove `cosign.key` from `.gitignore`.
- **Never weaken or remove a `tests/smoke.sh` or `tests/boot-check.sh`
  assertion** to make a change pass — if a change legitimately changes the
  intended behavior, update the assertion to match the *new* intent and say
  so explicitly; don't silently delete the check.
- **Never bump or unpin the base image tag in `Containerfile`** without
  re-reading `docs/research/i915-mtl-resume-2026-06-20.md` and confirming the
  upstream regression it documents is actually fixed (check for a real fix
  commit, not just a version number moving past 7.0). This is the highest-
  consequence line in the repo — see `.agent_native/agent_roadmap.md` item 1
  for a known gap in the "is it safe yet" verification story.
- **Do not run full container/VM/ISO builds speculatively** — see
  "Verification loop" above for the cheaper alternatives to try first.
- **`docs/**` changes don't trigger an image rebuild** (`build.yml` has
  `paths-ignore: ['**/README.md', 'docs/**']`) — safe to iterate on docs
  without spending CI build minutes, but also means a docs-only PR won't get
  the smoke/boot-test signal even if it describes behavior — double check the
  described behavior against the actual code, don't take a docs change's word
  for it.
