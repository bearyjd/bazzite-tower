# Staying in sync with upstream Bazzite without silently breaking

**Status:** Layers 1–5 implemented (gate + smoke tests + runtime boot test +
upstream package-diff early warning + issue notifications). The Section 4
digest-pin decision is the only open item.
**Goal:** keep `bazzite-tower` riding the cutting edge of upstream Bazzite
(`ghcr.io/ublue-os/bazzite-nvidia:stable`) while guaranteeing that an upstream
change can never silently land a broken image on the laptop — and that we get a
GitHub-issue notification the moment something does break.

---

## 1. The problem, stated precisely

The image is `FROM ghcr.io/ublue-os/bazzite-nvidia:stable` plus
[`build_files/build.sh`](../build_files/build.sh). CI today
([`build.yml`](../.github/workflows/build.yml)) validates exactly two things, on
a weekly Sunday cron:

1. the container **builds**, and
2. `bootc container lint` passes.

It then pushes `:latest` (and dated tags) on every green build, and the laptop
rebases onto `:latest`. **"Build is green" is not the same as "image works."**
Our two highest-risk areas fail in different ways, and only one of those ways is
currently caught:

| Area | Dominant failure mode | Caught by CI today? |
|------|----------------------|---------------------|
| **QEMU / libvirt** — the `qemu` sysusers/orphan-shadow dance, the five modular `virt*.socket` enables, `libvirtd.service` masking, default-network autostart symlink | Mostly **build-time** (a renamed package or a unit that no longer exists makes `systemctl enable` error) **but also silent runtime** (the qemu-user logic stops creating the user → `virtqemud` crash-loops on boot, yet the build was green) | Build-time: **yes**. Runtime: **no** |
| **Wi-Fi backend guard** | Almost entirely **silent runtime** — depends on NetworkManager's `wifi.backend` config semantics, the presence of `iwd`/`wpa_supplicant`, and unit ordering. None of that is exercised by a `dnf install` | **No** |
| **Docker CE / `$releasever`** | **Build-time** — Fedora bumps (e.g. F44 → F45) before Docker CE publishes that `baseurl`, so the repo 404s | **Yes** (weekly) |

The blind spot is the **silent runtime** column: the QEMU sysusers hack and the
*entire* Wi-Fi guard are precisely the kind of logic that keeps compiling while
quietly doing nothing after an upstream change.

The specific upstream changes that can trigger each failure:

- **QEMU user (`build.sh` lines ~112–131):** the base image moves from shipping
  orphan `qemu:` shadow/gshadow lines to something else; `systemd-sysusers`
  behavior changes; a packaging change ships (or stops shipping) the qemu
  sysusers snippet. Our guarded `groupadd`/`useradd` fallback covers a lot, but
  a *rename* of the user or a libvirt change in how it resolves the user would
  slip through.
- **Modular libvirt unit names (`build.sh` lines ~143–158):** upstream renames
  or consolidates `virtqemud.socket` / `virtproxyd.socket` / etc., or flips back
  toward monolithic `libvirtd`. `systemctl enable <missing-unit>` errors at
  build (caught), but a *semantic* change — e.g. `libvirtd.service` no longer
  existing to mask — could change behavior silently.
- **Wi-Fi guard (`system_files/usr/libexec/bazzite-tower-wifi-backend-guard`):**
  NetworkManager changes the `wifi.backend` config key, the conf.d precedence,
  or the default supplicant; `iwd`/`wpa_supplicant` packaging changes. The guard
  greps real paths (`/etc/NetworkManager`, `/usr/lib/NetworkManager`) and calls
  `systemctl is-enabled iwd` — any of those assumptions can drift.
- **Polkit rule (`build.sh` lines ~173–182):** a polkit major bump changes the
  JS rules API, so `wheel → qemu:///system` access stops applying.
- **kargs (`system_files/usr/lib/bootc/kargs.d/00-iommu.toml`):** bootc changes
  how `kargs.d` is read, so IOMMU silently stops being applied.

---

## 2. Design principles

1. **Tests should encode *intent*, not just existence.** Assert the things
   `build.sh` is *trying* to achieve (qemu user resolvable, virt sockets
   enabled, guard active) so they break loudly when an upstream change undoes
   them.
2. **The laptop must only ever pull a tested image.** Decouple "we built an
   image" from "we promoted it to the tag the laptop tracks."
3. **Prevention over rollback.** `bootc rollback` exists, but never rebasing
   into a broken state in the first place is strictly better.
4. **Notify through GitHub issues** (chosen channel): tracked, deduplicated,
   free, and GitHub already emails on scheduled-workflow failure by default.

---

## 3. The plan (layered, in priority order)

> **Implemented (Layers 1, 2, 5).** `build.yml` now runs `tests/smoke.sh`
> against the freshly built image *before* the login/push steps, so a failing
> assertion stops the job and `:latest` is never overwritten. On a
> push/schedule failure it opens (or comments on) a deduplicated `ci-failure`
> issue, and closes it again when the default branch goes green. Run the same
> checks locally with `just smoke`. Layers 3 and 4 below are still to do.

### Layer 1 — Promotion gating (highest value)

**Today:** every green build pushes `:latest`; the laptop rebases onto it. A bad
upstream bump can reach the laptop before we know.

**Change to:**

1. Build → push a **candidate** tag (by-SHA, e.g. `:sha-<short>`, and/or
   `:testing`), always.
2. Run the test layers below against that candidate.
3. **Only retag/promote the candidate to `:latest`** (and the dated tags) **if
   the tests pass.**

The laptop keeps tracking `:latest`. A broken upstream bump now fails the gate,
`:latest` stays pinned to the last-good image, and an issue is opened — the
laptop never rebases into a broken state. This is the single most valuable
change and is what makes "cutting edge" and "stable" compatible.

Mechanically in `build.yml`: split into `build` (push candidate) → `test` →
`promote` jobs, where `promote` runs `skopeo copy`/`podman tag` from the
candidate digest to `:latest` and the dated tags, gated on `needs: test`.

### Layer 2 — Cheap container smoke tests (seconds, no VM)

Run `podman run --rm <candidate> <cmd>` assertions. Proposed checks, each tied
to a line of `build.sh` or a `system_files/` artifact:

**QEMU / libvirt**
- `getent passwd qemu` and `id qemu` succeed → guards the sysusers/orphan-shadow
  dance.
- `systemctl is-enabled virtqemud.socket virtnetworkd.socket virtnodedevd.socket
  virtnwfilterd.socket virtstoraged.socket virtproxyd.socket` → all `enabled`.
- `systemctl is-enabled libvirtd.service` → `masked`.
- `test -L /etc/libvirt/qemu/networks/autostart/default.xml` → autostart symlink
  present.
- `command -v qemu-system-x86_64 virsh virt-install` → all resolve.
- polkit rule file `/etc/polkit-1/rules.d/50-libvirt-wheel.rules` present.

**Wi-Fi**
- `systemctl is-enabled bazzite-tower-wifi-backend-guard.service` → `enabled`.
- `test -x /usr/libexec/bazzite-tower-wifi-backend-guard`.
- `command -v wpa_supplicant` → present (the backend the guard falls back to).

**Boot args / first-boot**
- `test -f /usr/lib/bootc/kargs.d/00-iommu.toml`.
- `systemctl is-enabled bazzite-tower-firstboot.service` → `enabled`.

**Docker**
- `command -v docker` and `docker --version`; `containerd --version`.

Implement as a small `tests/smoke.sh` that loops over assertions and exits
non-zero on the first failure, called from the `test` job. Near-zero cost,
catches a large fraction of regressions.

### Layer 3 — Boot test (the only thing that proves *runtime* behavior)

> **Implemented (with a hosted-runner-friendly twist).** GitHub-hosted runners
> have no `/dev/kvm`, so instead of a TCG VM boot,
> `.github/workflows/boot-test.yml` boots the image's own systemd as PID 1 with
> `podman run --systemd=always` (a path these ublue/Bazzite images are built to
> support) and runs `tests/boot-check.sh` inside it via `podman exec`. That
> reliably exercises socket activation, the oneshots and the guard, and — the key
> check — **connects to `qemu:///system`**, the end-to-end proof the qemu user
> resolves and `virtqemud` initializes (regression #8). Checks that genuinely
> need a real kernel/bootloader/netfilter (kargs application, full NM device
> management, the Docker daemon) are SOFT (reported, non-fatal); kargs presence
> stays covered by the smoke test. Runs on image-affecting PRs, weekly (Sun 07:00
> UTC, after the build), and on demand; scheduled failures open a
> `boot-test-failure` issue. The original VM-based sketch below is kept for
> reference.

Reuse the existing [`build-disk.yml`](../.github/workflows/build-disk.yml) qcow2
output. Boot it (TCG is acceptable if the runner has no `/dev/kvm`; we only need
to reach `multi-user.target`) and run in-guest:

- `systemctl is-active virtqemud` (socket-activate it first via
  `virsh -c qemu:///system list`).
- `virsh -c qemu:///system list` exits 0 → the qemu user + modular daemons
  actually work end-to-end.
- `nmcli -f WIFI g` / `nmcli radio wifi` and confirm the active backend is
  `wpa_supplicant` (not stranded on a non-running `iwd`) → the *only* real proof
  the Wi-Fi guard behaves.
- `bootc status` shows the kargs applied (IOMMU).

Heavier, so run it **nightly and pre-promotion**, not on every PR. This is what
closes the Wi-Fi and virtqemud silent-runtime gap that Layer 2 can only
approximate.

### Layer 4 — Upstream "what changed" early warning

> **Implemented.** `.github/workflows/base-watch.yml` runs daily (05:00 UTC,
> ahead of the Sunday build): it pulls `bazzite-nvidia:stable`, builds a package
> manifest, and `ci/base-diff.py` diffs it against the last-seen manifest stored
> at `docs/manifests/bazzite-nvidia-stable.txt`, filtered to the blast-radius
> regex. On a blast-radius change it opens/comments on a deduplicated
> `base-bump` issue with a `name old → new` summary, then commits the refreshed
> manifest back (`docs/**` is in `build.yml`'s `paths-ignore`, so that commit
> doesn't trigger an image rebuild). First run just records the baseline.

On each scheduled run, before/after pulling the base:

1. `podman run --rm ghcr.io/ublue-os/bazzite-nvidia:stable rpm -qa | sort` →
   current manifest.
2. Diff against the manifest from the last successful build (stored as a
   workflow artifact or committed under `docs/manifests/`).
3. Filter the diff to our blast radius: `qemu*`, `libvirt*`, `NetworkManager`,
   `iwd`, `wpa_supplicant`, `polkit`, `systemd`, `kernel`, `bootc`,
   `docker-ce` / `$releasever`.
4. If anything in that set changed, open/update a tracking issue:
   *"Base bumped: qemu 9.1→9.2, NetworkManager 1.50→1.52."*

This is the heads-up **even when tests still pass**, so a risky bump gets human
eyes before it has a chance to bite.

### Layer 5 — Notifications (GitHub issues)

- On any failure in the `test`/`promote`/boot jobs, **auto-open a deduplicated
  GitHub issue** (search for an open issue with a fixed marker label/title; if
  found, comment instead of opening a duplicate; auto-close when a later run
  goes green). A small `actions/github-script` step or the `gh` API covers this.
- GitHub additionally emails the cron's last editor on scheduled-workflow
  failure by default, so no extra wiring is needed for email.
- Phone push (ntfy.sh / Discord) is intentionally **out of scope** per the
  chosen channel; can be added later as one `curl` in the failure step.

---

## 4. Strategic option: pin the base by digest + Renovate

Current approach floats the `:stable` tag and relies on the promotion gate to
stay safe. A cleaner-attribution variant:

- **Pin the base to a digest** in the `Containerfile` and let
  [Renovate](../renovate.json) open a PR for each digest bump.
- Every upstream movement becomes its own PR that runs the full test gate,
  **automerges when green**, and is **flagged when red**.

Tradeoff: a PR per bump (automergeable), in exchange for never being more than
one *tested* PR behind and knowing exactly which bump broke things. The
floating-tag + promotion-gate approach also works; the digest pin just gives
finer-grained attribution. Recommend deciding this once Layers 1–2 land.

---

## 5. Suggested rollout order

1. **Layer 1 + Layer 2** together (`tests/smoke.sh` + split build/test/promote
   jobs + issue-on-failure). Highest value, lowest complexity, no VM.
2. **Layer 5** issue automation (folded into step 1's `test`/`promote` jobs).
3. **Layer 4** package-diff early warning (independent, low risk).
4. **Layer 3** boot test (nightly), once the cheaper layers are trusted.
5. Revisit **Section 4** (digest pin) as a deliberate call.

---

## 6. Concrete file touch-list (for when we implement)

- `.github/workflows/build.yml` — split into `build` → `test` → `promote`;
  candidate tag on build; promote gated on `needs: test`; issue-on-failure step.
- `tests/smoke.sh` *(new)* — Layer 2 assertions.
- `tests/boot-check.sh` *(new)* — in-guest Layer 3 assertions.
- `.github/workflows/boot-test.yml` *(new, nightly)* — build qcow2 + run
  `boot-check.sh`.
- `.github/workflows/base-watch.yml` *(new, scheduled)* — Layer 4 manifest diff
  + issue.
- `docs/manifests/bazzite-nvidia-stable.txt` *(new, bot-updated)* — last-seen
  base package manifest for diffing.
- `Containerfile` / `renovate.json` — only if we adopt Section 4 (digest pin).
