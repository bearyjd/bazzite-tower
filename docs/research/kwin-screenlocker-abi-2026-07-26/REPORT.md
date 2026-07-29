# KDE Plasma 6.7.3 kwin ↔ kscreenlocker 6.6.4 ABI skew → black screen at login (latest.20260726)

> **Status (2026-07-29): root cause confirmed, no code fix needed in this repo.** The
> `latest.20260726` deployment ships `kwin`/`kwin-libs` at `6.7.3-1.fc44` while
> `kscreenlocker` was left at `6.6.4-1.fc44` in the same transaction — an upstream Fedora
> KDE / Bazzite base-repo desync baked into that day's image, not something
> `build_files/build.sh` or `system_files/` in this repo controls (this repo doesn't touch
> base KDE packages). Mitigation: stay on the known-good `latest.20260705` deployment
> (already the active default) until a later upstream base build re-syncs the two packages,
> then re-upgrade normally.

## Symptom

After `ujust upgrade` staged and booted `latest.20260726`, the screen goes black/frozen
immediately after Plasma Boot Splash — no login prompt ever appears. Greenboot rolled the
system back to `latest.20260705` twice.

## Investigation

Initially suspected: NVIDIA 580.142 / i915 GPU-handoff regression on this hybrid-graphics
(Meteor Lake i915 + NVIDIA Optimus) machine, given a `[drm] *ERROR* [CRTC:148:pipe A] DSB
poll error` line in dmesg right before the crash. **This hypothesis was refuted** — see
below.

### 1. Package diff between the two deployments

```
rpm-ostree db diff 46438ed9...0705 12ac0f31...0726
```

140 packages upgraded, 1 added (`blake3`). **Zero kernel or `nvidia-*` packages** in the
diff — the i915 DSB line is a pre-existing/benign message, not the cause. The entire diff
is KDE Plasma `6.6.4 → 6.6.5/6.7.3`, KF6 `6.25.0 → 6.28.0`, and Qt6 `6.10.3 → 6.11.1`.
Notably: `kwin`, `kwin-libs`, `kwin-common`, `libplasma`, `plasma-login-manager`, and
`kcm-plasmalogin` all jumped to `6.7.3`.

### 2. Journal evidence (100% reproducible)

Every boot into the `latest.20260726` deployment slot produces the identical crash signature:

```
Starting plasma-login-kwin_wayland.service - KDE Window Manager (Login Manager Version)...
/usr/bin/kwin_wayland: symbol lookup error: /lib64/libkwin.so.6: undefined symbol: _ZN12ScreenLocker7KSldApp14inhibitSuspendEv
plasma-login-kwin_wayland.service: Main process exited, code=exited, status=127/n/a
plasma-login-kwin_wayland.service: Failed with result 'exit-code'.
Failed to start plasma-login-kwin_wayland.service - KDE Window Manager (Login Manager Version).
Dependency failed for plasma-login-wayland.target.
...
Failed to create wl_display (No such file or directory)
Could not load the Qt platform plugin "wayland" in "" even though it was found.
Could not load the Qt platform plugin "xcb" in "" even though it was found.
ANOM_ABEND ... comm="plasma-login-gr" exe="/usr/libexec/plasma-login-greeter" sig=6 res=1
ANOM_ABEND ... comm="plasma-login-wa" exe="/usr/bin/plasma-login-wallpaper" sig=6 res=1
```

`kwin_wayland` fails to even start (`exit-code 127`, dynamic linker symbol lookup
failure) — there is no compositor, so both the Wayland and X11 greeter fallbacks fail to
get a display, and the greeter/wallpaper processes abort with `SIGABRT`. This exactly
matches the reported black/frozen screen with no login prompt.

Boots that landed on the `latest.20260705` deployment slot in the same time window (e.g.
the boot immediately before the last failed attempt) started `plasma-login-kwin_wayland`
and reached `plasma-kwin_wayland.service` (the real session compositor) cleanly — proving
the crash is deployment-specific, not hardware-state-dependent.

### 3. Root cause: version skew, not a GPU/driver bug

`_ZN12ScreenLocker7KSldApp14inhibitSuspendEv` demangles to
`ScreenLocker::KSldApp::inhibitSuspend()` — a symbol kwin 6.7.3 expects from a matching
6.7.x `kscreenlocker`/`libKScreenLocker`. Checked both deployments directly:

```
$ rpm-ostree db list <good-commit>   | grep -i screenlock
 kscreenlocker-6.6.4-1.fc44.x86_64
$ rpm-ostree db list <broken-commit> | grep -i screenlock
 kscreenlocker-6.6.4-1.fc44.x86_64
```

`kscreenlocker` is **6.6.4-1.fc44 in both** — it was not part of the 140-package upgrade
that bumped `kwin`/`kwin-libs`/`libplasma` to 6.7.3. The image build on 2026-07-26 pulled
from upstream Fedora/Bazzite base repos at a moment where `kwin` 6.7.3 builds were
available but the companion `kscreenlocker` 6.7.x build was not (or was excluded),
producing an internally-inconsistent package set. This repo's `build_files/build.sh` does
not install or pin any KDE/Plasma packages — they come entirely from the upstream base
image — so there is nothing to fix in `bazzite-tower` itself.

## Current safe state (confirmed 2026-07-29)

```
$ rpm-ostree status
State: idle
Deployments:
  ostree-unverified-registry:ghcr.io/bearyjd/bazzite-tower:latest
                  Version: latest.20260726  (broken, present but not default)
● ostree-unverified-registry:ghcr.io/bearyjd/bazzite-tower:latest
                  Version: latest.20260705  (known-good, current default/booted)
```

`ostree admin status` confirms the `*` default boot entry is already the good
`latest.20260705` deployment — no immediate action needed. Neither deployment is
`ostree admin pin`ned yet.

## Remediation plan (least → most invasive)

1. **Do nothing further right now.** The default boot target is already the good
   deployment; simply don't manually select the `latest.20260726` GRUB entry.
2. **Pin the known-good deployment** for extra safety before attempting another upgrade:
   `sudo ostree admin pin <index-of-20260705>` — guarantees it survives regardless of
   deployment retention/pruning.
3. **Dry-run future upgrades before deploying:** `rpm-ostree upgrade --preview` (or
   `--check`) to inspect the incoming package diff for `kwin`/`kscreenlocker` version
   alignment before committing to a new deployment.
4. **Re-run `ujust upgrade`** once a preview shows `kscreenlocker` has caught up to
   whatever `kwin` version ships (i.e. the upstream repo desync has healed on a later
   day's base image). Old deployments are not auto-pruned by default, so `latest.20260705`
   remains available via `rpm-ostree rollback` / GRUB regardless.
5. **Not recommended:** hand-patching the broken deployment's `/usr` (`ostree admin
   unlock`) to force-install a matching `kscreenlocker` build. This is fragile,
   per-deployment, and unnecessary — the fix belongs upstream, not on this machine.

## Not related

- NVIDIA 580.142 driver / DKMS — unchanged between deployments, not implicated.
- Kernel — unchanged (`6.19.11-ogc1.1.fc44` in both), not implicated.
- The [i915 MTL s2idle-resume regression](../i915-mtl-resume-2026-06-20.md) — a separate,
  unrelated bug (display PLL on resume, not login-time compositor startup).
