# Allow build scripts to be referenced without being copied into the final image
FROM scratch AS ctx
COPY build_files /

# Base: bazzite KDE + proprietary NVIDIA driver. Chosen over the -open variant
# for more reliable suspend/resume and power management on this Optimus laptop —
# the open kernel modules still have known gaps there (NVIDIA's own docs flag
# power management; upstream hybrid-laptop suspend bugs remained open into 2026).
# Swap to ghcr.io/ublue-os/bazzite-nvidia-open:stable to use the open modules.
# Desktop variant — not deck-based, tracks F44+.
#
# PINNED to a 6.19.x-ogc base (FC44) to dodge the Meteor Lake i915 cx0 PHY-A
# s2idle-resume regression. Root cause (git-verified 2026-06-20): the cx0
# DPLL-framework rewrite landed in kernel 7.0 (lead commit 1a7fad2aea74), is absent
# in 6.19, and is still unreverted upstream — so EVERY 7.0.x-ogc base corrupts the
# C10 PLL on resume (~30s flip_done storm), while 6.19.x is the only confirmed-good
# kernel. No karg/driver workaround exists (PSR/DC/FBC, xe, runtime-PM all ruled out).
# Pinning here (vs :stable) also predates the 7.0-ogc-jump "MCE storm".
#   :stable      = 7.0.9-ogc3.2  (regressed)
#   44.20260429  = 6.19.11-ogc1  (this pin — verified known-good)
# Re-evaluate when upstream fixes the framework path — the host watcher
# i915-resume-fix-check.timer flags it. Full analysis + sources:
# docs/research/i915-mtl-resume-2026-06-20.md
FROM ghcr.io/ublue-os/bazzite-nvidia:44.20260429

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
    /ctx/build.sh

### LINTING
RUN bootc container lint
