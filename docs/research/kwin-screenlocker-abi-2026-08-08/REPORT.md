# kwin 6.7.4 ↔ kscreenlocker 6.6.4 ABI skew recurs → black screen at login (latest.20260808)

> **Status (2026-08-08, updated): recurrence of the 2026-07-26 incident, same failure mode, one**
> **point release later. Root cause fixed at the source, on the second attempt** —
> `build_files/build.d/05-pin-kde-packages.sh` appends an `exclude=` line to `/etc/dnf/dnf.conf`'s
> `[main]` section, so this build's own dnf transactions can no longer introduce the skew itself.
> The *first* attempt at this fix (PR #45, initial commit) was a no-op — see "Correction" below —
> and was itself caught by the `tests/smoke.sh` gate this same PR added, before anything shipped.
> The corrected mechanism is verified working: reproducing the exact `10-virt-packages.sh` dnf
> transaction against the pinned base image with the fix in place completes cleanly and leaves
> `kwin`/`kf6-kwindowsystem`/`kscreenlocker`/`libplasma` at their original matched versions.
> `tests/smoke.sh` also gained a `kwin`/`kscreenlocker` major.minor version-match assertion, plus a
> second check that the `exclude=` line is actually present in `/etc/dnf/dnf.conf` — so a future
> skew already baked into the base image, or a future regression of the fix mechanism itself, both
> fail the build instead of shipping under `:latest`.

## Correction (2026-08-08, same day)

The first version of `05-pin-kde-packages.sh` wrote:

```bash
mkdir -p /etc/dnf/dnf.conf.d
cat >/etc/dnf/dnf.conf.d/05-pin-kde-plasma.conf <<'EOF'
[main]
exclude=kwin kwin-libs ...
EOF
```

This was a complete no-op. The assumption — that dnf reads a `dnf.conf.d/*.conf` drop-in
directory, by analogy with `sysctl.d`/`modprobe.d`-style conventions used elsewhere in this repo
(e.g. `system_files/usr/lib/sysctl.d/99-tower-swappiness.conf`) — doesn't hold for this base
image's package manager. `ghcr.io/ublue-os/bazzite-nvidia:44.20260429` runs **dnf5 5.4.1.0**, not
dnf4, and `/etc/dnf/` there contains `dnf5-aliases.d`, `dnf5-plugins`, `libdnf5-plugins`,
`libdnf5.conf.d`, `protected.d`, `repos.override.d`, `vendors.d` — no `dnf.conf.d`. (dnf5 *does*
have a real drop-in directory, `/etc/dnf/libdnf5.conf.d/*.conf`, confirmed via `man dnf5.conf`'s
"DROP-IN CONFIGURATION DIRECTORIES" section — just not the name assumed here.)

PR #45's own CI run caught this directly: the `safe-pin` matrix leg's `tests/smoke.sh` step failed
with `kwin=6.7.4, kscreenlocker=6.6.4` — the identical skew the PR claimed to fix, because the
exclude was written to a file dnf5 never reads. The build log confirms `kwin`/`kf6-kwindowsystem`
still upgrading inside `10-virt-packages.sh`'s `dnf install`, completely unobstructed by the dead
config file from the script that ran immediately before it. This is exactly the failure mode the
`tests/smoke.sh` gate added in the same PR exists to catch — and it did, before anything reached
`:latest`.

The fix: `/etc/dnf/dnf.conf` is the file dnf5 actually loads (confirmed by reading its live
content in the base image — a `[main]` section with `install_weak_deps=False` etc. already
present). Appending the `exclude=` line there, guarded on `[main]` actually being found first,
was verified two ways: (1) an explicit `dnf install -y kf6-kwindowsystem --refresh` after the
append fails with `Argument 'kf6-kwindowsystem' matches only excluded packages.`; (2) reproducing
`10-virt-packages.sh`'s exact install list against a fresh pull of the pinned base image, with the
fix applied first, completes with exit 0 and leaves the whole KDE Plasma family untouched at its
original version.

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
`build.d/` by filename order) appends `exclude=kwin kwin-libs kwin-common kwin-wayland kwin-x11
kscreenlocker libplasma libplasma-* plasma-workspace plasma-workspace-common
plasma-workspace-libs plasma-desktop kdecoration kf6-kwindowsystem` to `/etc/dnf/dnf.conf`'s
`[main]` section (guarded — refuses to run if `[main]` isn't found) so none of this repo's own dnf
transactions can touch them. See "Correction" above for why it's `/etc/dnf/dnf.conf` and not a
`dnf.conf.d/` drop-in. Whatever the pinned base image ships for that family survives the build
untouched. Advancing the Containerfile `BASE_IMAGE` pin to a later, internally-consistent upstream
release still picks up that release's own matched set — this exclude only stops *this build* from
perturbing it in between base bumps. Chose `exclude=` over dnf5's `versionlock` plugin
(`/etc/dnf/versionlock.toml`, already present in the base image): versionlock pins an exact NEVRA,
which would need manual bumping on every `BASE_IMAGE` advance; exclude just steps aside for
whatever the new base ships. Tradeoff: if a future `build.d/` addition genuinely needs a newer
kwin-family package, the transaction hard-fails with "matches only excluded packages" rather than
a version-lock-specific error.

Belt-and-suspenders, two `tests/smoke.sh` checks: a "KDE Plasma version consistency" check that
reads `kwin` and `kscreenlocker`'s installed versions via `rpm -q` and fails the build if their
major.minor doesn't match (catches a skew already baked into a future base image, not just one
this build could introduce itself), and a mechanism-level check that `exclude=.*kwin` is actually
present in `/etc/dnf/dnf.conf` (catches a future regression of the fix mechanism itself, like the
one described in "Correction" above, without waiting for the symptom to reappear).

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
