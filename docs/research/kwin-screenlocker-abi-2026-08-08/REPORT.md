# kwin 6.7.4 ↔ kscreenlocker 6.6.4 ABI skew recurs → black screen at login (latest.20260808)

> **Status (2026-08-08): recurrence of the 2026-07-26 incident, same failure mode, one point
> release later. Root cause now fixed at the source** — `build_files/build.d/05-pin-kde-packages.sh`
> excludes the whole KDE Plasma/KWin package family from this repo's own dnf transactions, so this
> build can no longer introduce the skew itself. `tests/smoke.sh` also gained a
> `kwin`/`kscreenlocker` major.minor version-match assertion so any future skew already baked into
> the base image fails the build instead of shipping under `:latest`.

## Symptom

`latest.20260808` was pulled and deployed as the update to `latest.20260705` (the previously
known-good deployment, itself the mitigation for the 2026-07-26 incident). On boot into
`latest.20260808`: black screen, no login prompt. The user had to manually select the previous
GRUB boot entry to get back into a working system; `bootc status` afterward showed
`latest.20260808` sitting in the **Rollback image** slot (not booted), `latest.20260705` still
Booted.

No persistent journal entry exists for the failed boot attempt itself — it never got far enough
(or the machine was power-cycled fast enough) for anything to flush to disk before the manual
GRUB fallback. Root cause was established from the package diff and the prior incident's captured
crash signature, not a fresh log capture of this specific boot.

## Investigation

### 1. False leads ruled out first (see the two prior investigations in this session)

Before landing on the KDE skew, two other candidate causes were investigated and ruled out:

- **The tracked i915 Meteor Lake cx0 DPLL s2idle-resume regression**
  (`docs/research/i915-mtl-resume-2026-06-20.md`). Ruled out: the running kernel is
  `6.19.11-ogc1.1.fc44.x86_64` on every relevant boot — the pinned known-good line, unaffected by
  the 7.0+-only regression. `latest.20260808` did not change the Containerfile's `BASE_IMAGE` pin.
- **A kernel-level network softlockup** found in the same debugging session (`native_queued_spin_lock_slowpath`
  in `__dev_queue_xmit`, triggered by heavy `docker_open5gs` container/veth churn) — a real, separate,
  recurring bug on this machine, but unrelated to booting `latest.20260808`: `journalctl` confirms
  that incident happened entirely on the already-booted `latest.20260705` deployment, hours before
  `latest.20260808` was ever attempted.

### 2. Package diff between the booted and rollback deployments

```
$ rpm-ostree db diff 46438ed99e5f9cb75d965d60e4bc6af95b0ee1de2ccda5e2487025076997309b \
                      b4f8cfd3306e094b0c26769cd5e6aa01b351d1c5502c49025ca432f1802252d7
  kf6-kwindowsystem        6.25.0-1.fc44 -> 6.28.0-1.fc44
  kwin                     6.6.4-2.fc44  -> 6.7.4-1.fc44
  kwin-common              6.6.4-2.fc44  -> 6.7.4-1.fc44
  kwin-libs                6.6.4-2.fc44  -> 6.7.4-1.fc44
  libplasma                6.6.4-1.fc44  -> 6.7.4-1.fc44
  plasma-desktop           6.6.4-1.fc44  -> 6.6.5-1.fc44
  plasma-workspace         6.6.4-1.fc44  -> 6.6.5-2.fc44
  plasma-workspace-common  6.6.4-1.fc44  -> 6.6.5-2.fc44
  plasma-workspace-libs    6.6.4-1.fc44  -> 6.6.5-2.fc44
```

`kscreenlocker` does not appear in the diff at all — 160 packages moved, `kscreenlocker` was not
one of them, so it's still `6.6.4-1.fc44` in `latest.20260808` while `kwin`/`kwin-libs`/
`kwin-common`/`libplasma` jumped to `6.7.4`. This is the exact same shape of skew as the 2026-07-26
incident (there it was `kwin` 6.7.3 vs `kscreenlocker` 6.6.4), one `kwin` point release later.
Given the July report's confirmed crash signature (`kwin_wayland: symbol lookup error: ...
undefined symbol: _ZN12ScreenLocker7KSldApp14inhibitSuspendEv`, exit code 127, no compositor, both
Wayland and X11 greeter fallbacks failing) and the user's independently reported black
screen/no-login-prompt on this exact package skew, the same failure mode is treated as confirmed
without needing to re-capture the crash log.

### 3. Why this repo's own build can cause the skew, even with an unchanged BASE_IMAGE pin

The Containerfile's `ARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia:44.20260429` was not touched
between the `latest.20260705` and `latest.20260808` builds (confirmed via `git log`), and nothing
in `build_files/build.d/` runs `dnf upgrade`/`dnf update` — every install is `dnf install -y
<specific package list>`. That still doesn't make the base image's already-installed packages
immune: `dnf install` resolves the *whole* transaction against live Fedora/COPR repo metadata at
build time, and the solver can pull a newer NEVRA of an already-installed, unrelated package
(here: `kwin`/`libplasma`, pulled in as part of resolving one of the unrelated `dnf install` calls
in `10-virt-packages.sh`, `20-dev-tooling.sh`, etc.) if that's part of the "best" transaction on
the day CI happens to build — independent of whether a matching `kscreenlocker` build exists in
the same repo snapshot that day. The base image itself ships an internally consistent set; this
repo's own later dnf activity was capable of un-syncing it.

## Fix

`build_files/build.d/05-pin-kde-packages.sh` (new, runs before every other `dnf install` in
`build.d/` by filename order) writes a dnf exclude for the whole KDE Plasma/KWin family
(`kwin kwin-libs kwin-common kwin-wayland kwin-x11 kscreenlocker libplasma libplasma-*
plasma-workspace plasma-workspace-common plasma-workspace-libs plasma-desktop kdecoration
kf6-kwindowsystem`) so none of this repo's own dnf transactions can touch them. Whatever the
pinned base image ships for that family survives the build untouched. Advancing the Containerfile
`BASE_IMAGE` pin to a later, internally-consistent upstream release still picks up that release's
own matched set — this exclude only stops *this build* from perturbing it in between base bumps.

Belt-and-suspenders: `tests/smoke.sh` gained a "KDE Plasma version consistency" check that reads
`kwin` and `kscreenlocker`'s installed versions via `rpm -q` and fails the build if their
major.minor doesn't match — catching a skew already baked into a future base image, not just one
this build could introduce itself.

## Remediation for the currently-affected machine

Same as the 2026-07-26 incident, no new action needed beyond what already happened:

1. `latest.20260705` (the known-good deployment) is already the booted default. Nothing further
   required.
2. Do not manually select the `latest.20260808` GRUB entry — it reproduces the crash.
3. Once this fix ships under a new `:latest` build, `rpm-ostree upgrade --preview` before deploying
   is still worth doing as a habit, though the exclude + smoke-test gate should mean a shipped
   `:latest` can no longer carry this specific skew.

## Not related

- The i915 Meteor Lake s2idle-resume regression (`docs/research/i915-mtl-resume-2026-06-20.md`) —
  separate bug, separate subsystem, kernel unaffected here.
- The `docker_open5gs` network softlockup investigated in the same session — real, recurring, but
  happened on the already-booted `latest.20260705` deployment hours before `latest.20260808` was
  ever attempted; unrelated to this boot failure.
- `bazzite-nvidia:stable`/`bazzite-nvidia:latest` as a "switch back" option — does not fix this
  class of bug (same upstream KDE repo, same skew risk) and reintroduces the *unfixed* i915 kernel
  regression this repo pins around. Not recommended.
