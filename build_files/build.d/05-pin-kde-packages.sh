#!/usr/bin/env bash
# 05-pin-kde-packages.sh — stop this build's own dnf transactions from
# skewing the KDE Plasma/KWin package family away from what the pinned base
# image already ships as one internally-consistent set.
#
# Two independent incidents (docs/research/kwin-screenlocker-abi-2026-07-26/
# and -2026-08-08/) show `kwin`/`kwin-libs`/`kwin-common`/`libplasma` landing
# a point release ahead of `kscreenlocker` in our own built image, even though
# the Containerfile's BASE_IMAGE pin was unchanged between the two builds and
# nothing in build.d runs `dnf upgrade`. Root cause: the later `dnf install -y
# <unrelated package>` calls elsewhere in build.d (10-virt-packages.sh,
# 20-dev-tooling.sh, etc.) still resolve against upstream Fedora/COPR repo
# metadata live at build time, and dnf's solver can pull a newer kwin/libplasma
# as part of satisfying an unrelated transaction on a day when the matching
# kscreenlocker build isn't in the same repo snapshot yet. `kwin` then dlopens
# a `kscreenlocker` symbol (`ScreenLocker::KSldApp::inhibitSuspend()`) that
# doesn't exist in the older build still on disk, `kwin_wayland` dies with
# exit 127, and there is no compositor to log in to — a black screen with no
# login prompt, indistinguishable at a glance from a driver crash.
#
# Fix: exclude the whole family from every dnf transaction in this build, so
# whatever the base image already shipped (guaranteed internally consistent,
# since it was built as one coherent Bazzite release) survives untouched. This
# does not freeze these packages forever — advancing the Containerfile's
# BASE_IMAGE pin still picks up whatever matched set the new base ships; this
# file only stops *our own* build from perturbing it in between.
#
# Chose dnf exclude over dnf5's versionlock plugin: versionlock pins an exact
# NEVRA, which would need manual bumping on every BASE_IMAGE advance (more
# brittle, not less); exclude just steps aside and lets whatever the new base
# ships through untouched. Tradeoff: if a future build.d/ addition genuinely
# needs a newer kwin-family package, the dnf transaction will hard-fail with
# "matches only excluded packages" rather than a version-lock-specific error.
#
# CORRECTION (see docs/research/kwin-screenlocker-abi-2026-08-08/REPORT.md,
# "Correction" section): the first version of this fix wrote a
# `/etc/dnf/dnf.conf.d/*.conf` fragment, copying the sysctl.d-style drop-in
# convention used elsewhere in this repo. This base image runs dnf5, which has
# no such directory (`/etc/dnf/` here is dnf5-aliases.d, dnf5-plugins,
# libdnf5-plugins, libdnf5.conf.d, protected.d, repos.override.d, vendors.d —
# no dnf.conf.d). That first version was a complete no-op, silently verified
# by PR #45's own CI smoke gate, which is exactly what that gate is for.
# `/etc/dnf/dnf.conf` is the file dnf5 actually loads; write to it directly.
#
# Must run before any other build.d script that calls `dnf install`, hence 05-
# (before 10-virt-packages.sh).
set -euo pipefail

grep -q '^\[main\]$' /etc/dnf/dnf.conf || {
    echo "05-pin-kde-packages.sh: /etc/dnf/dnf.conf has no [main] section, refusing to append blind" >&2
    exit 1
}

cat >>/etc/dnf/dnf.conf <<'EOF'
exclude=kwin kwin-libs kwin-common kwin-wayland kwin-x11 kscreenlocker libplasma libplasma-* plasma-workspace plasma-workspace-common plasma-workspace-libs plasma-desktop kdecoration kf6-kwindowsystem
EOF
