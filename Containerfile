# BASE_IMAGE selects which published tag this Containerfile builds. Two variants
# are built in CI from this one file (see .github/workflows/build.yml):
#   default (unset)     -> the pin below, published as `:latest` (safe/default)
#   ghcr.io/ublue-os/bazzite-nvidia-open:stable
#                        -> published as `:latest-kernel` (opt-in, tracks
#                           whatever kernel/driver upstream currently ships —
#                           see the history below for what that's meant in
#                           practice)
ARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open:44.20260825
ARG FIREWALL_DAEMON=opensnitch
ARG VM_GATE_SSH=0

# Allow build scripts to be referenced without being copied into the final image
FROM scratch AS ctx
COPY build_files /

# Base: bazzite KDE + NVIDIA **open** kernel modules, F44+, desktop variant
# (not deck-based). This was proprietary (`bazzite-nvidia`) until 2026-08-28;
# see the dated history below for the full chain of reasoning. Short version:
#
# The Meteor Lake i915 cx0 PHY-A s2idle-resume regression (root cause,
# git-verified 2026-06-20: the cx0 DPLL-framework rewrite, lead commit
# 1a7fad2aea74, landed in kernel 7.0 and was absent in 6.19 — no karg/driver
# workaround existed; PSR/DC/FBC, xe, runtime-PM were all ruled out) forced
# this repo onto a 6.19.x pin for two months while every 7.0.x/7.1.x kernel
# remained broken. The fix (commit 062499cc4813b5a3, "drm/i915/mtl+: Enable
# PPS before PLL", closes freedesktop #16042) landed in mainline 7.2 — but by
# the time 7.2 actually shipped (2026-08-20), upstream had forked the
# proprietary/`bazzite-nvidia` flavor onto its own `ogc-lts` kernel track
# (6.18.44), decoupled from the fixed 7.2.x main line, while `bazzite-nvidia-
# open` followed main to 7.2.0-ogc6.1. So the *only* upstream tag that both
# carries the i915 fix and is still actively tracked is the open-modules one.
#
# Decision (2026-08-28): move the default pin to `bazzite-nvidia-open`,
# pinned to `44.20260825` (kernel 7.2.0-ogc6.1, driver 610.57.04) rather than
# tracking `:stable`, for the same reproducibility reason as every prior pin
# here. This is a deliberate trade, not a strict upgrade:
#   - Driver `610.57.04` is NVIDIA's New Feature Branch (NFB, 11-month
#     support window), released 2026-08-03 — i.e. weeks old at pin time, not
#     the 3-year-support Long Term Support Branch (`580.x`) the proprietary
#     flavor carries. It's also what unlocks features the LTSB will not get
#     by policy (bugfix/security only) — DRM color-pipeline / HDR output,
#     newer Vulkan extensions, zero-copy NVDEC import for Wayland compositors.
#   - The hybrid GPU-switching stack changes with it: `bazzite-nvidia-open`
#     uses `cardwire` (eBPF-based, "early development" per its own README);
#     the proprietary/LTS flavor uses `supergfxctl` instead (an older,
#     previously-deprecated-then-restored tool with at least one reproduced
#     failure mode on this same Bazzite stack — ublue-os/hwe#200).
#   - No suspend/resume evidence specific to this exact hardware (Meteor Lake
#     + Ada RTX 4070 Max-Q, ThinkPad P1) was found for either flavor in either
#     direction — the field reports checked were all other platforms/GPUs.
# `bootc rollback` / re-pinning BASE_IMAGE back to a `bazzite-nvidia` tag is
# the mitigation if this regresses in practice. Full analysis:
# docs/research/i915-bug-report/UPSTREAM-FIX-STATUS-2026-08-28.md
#
# --- Prior history, while the default pin was proprietary `bazzite-nvidia` ---
#   :stable      = 7.1.5-ogc5.1  (still regressed as of 2026-08-08)
#   44.20260429  = 6.19.11-ogc1  (the pin used 2026-06-20 through 2026-08-28)
# 2026-07-04: fix landed in torvalds/linux master (062499cc4813b5a3) but was
#   not yet in any released 7.0.y/7.1.y stable kernel or bazzite-nvidia:stable.
#   docs/research/i915-bug-report/UPSTREAM-FIX-STATUS-2026-07-04.md
# 2026-07-13: bazzite-nvidia:stable jumped to 7.1.3-ogc3.4; fix confirmed
#   absent from linux-7.1.y at 7.1.3.
#   docs/research/i915-bug-report/UPSTREAM-FIX-STATUS-2026-07-13.md
# 2026-07-23: fix confirmed present in mainline v7.2-rc1+, but 7.2 had not
#   released and bazzite-nvidia:stable was still on 7.1.3-ogc5.1.
#   docs/research/i915-bug-report/UPSTREAM-FIX-STATUS-2026-07-23.md
# 2026-08-08: 7.2 still unreleased; bazzite-nvidia:stable at 7.1.5-ogc5.1.
#   docs/research/i915-bug-report/UPSTREAM-FIX-STATUS-2026-08-08.md
# 2026-08-28: 7.2 released with the fix — but bazzite-nvidia:stable had by
#   then moved to the unaffected-but-unfixed 6.18.44 `ogc-lts` track instead
#   of following it. Default pin moved to bazzite-nvidia-open (see above).
#   docs/research/i915-bug-report/UPSTREAM-FIX-STATUS-2026-08-28.md
FROM ${BASE_IMAGE}

# The production image remains on OpenSnitch.  `portmaster` is a deliberately
# disabled test variant: build it explicitly with --build-arg
# FIREWALL_DAEMON=portmaster and validate it in a VM before considering it for
# a published/default tag.  See docs/research/portmaster-bootc-spike.md.
ARG FIREWALL_DAEMON

# Off by default -- Bazzite does not enable sshd out of the box, and this
# leaves that alone. Only a VM-gate build run with --build-arg VM_GATE_SSH=1
# enables sshd.socket, and only a disk built from disk_config/vm-test.toml
# (not disk.toml, which is what ships and what the ThinkPad boots) has a user
# or key to log in with. `:latest` is unaffected either way. See
# docs/research/portmaster-bootc-spike.md's "VM-gate SSH" section.
ARG VM_GATE_SSH

# OCI image labels. These are baked into the image for local `podman build`;
# CI additionally layers ArtifactHub/metadata labels via docker/metadata-action.
LABEL org.opencontainers.image.title="bazzite-tower"
LABEL org.opencontainers.image.description="Bazzite desktop + QEMU/libvirt/Docker for ThinkPad P1"
LABEL org.opencontainers.image.source="https://github.com/bearyjd/bazzite-tower"

### SYSTEM FILES
# Static content baked verbatim into the image: systemd units, ujust recipes,
# and bootc kernel-argument fragments. Copied before build.sh runs so it can
# enable the units that land here.
COPY system_files/ /

### MODIFICATIONS
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    --mount=type=cache,dst=/var/cache \
    --mount=type=cache,dst=/var/log \
    --mount=type=tmpfs,dst=/tmp \
    FIREWALL_DAEMON="${FIREWALL_DAEMON}" VM_GATE_SSH="${VM_GATE_SSH}" /ctx/build.sh

### LINTING
RUN bootc container lint
