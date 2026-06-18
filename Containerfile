# Allow build scripts to be referenced without being copied into the final image
FROM scratch AS ctx
COPY build_files /

# Base: bazzite KDE + proprietary NVIDIA driver. Chosen over the -open variant
# for more reliable suspend/resume and power management on this Optimus laptop —
# the open kernel modules still have known gaps there (NVIDIA's own docs flag
# power management; upstream hybrid-laptop suspend bugs remained open into 2026).
# Swap to ghcr.io/ublue-os/bazzite-nvidia-open:stable to use the open modules.
# Desktop variant — not deck-based, tracks F44+.
# (Briefly pinned to FC43 6.17-ba while chasing an apparent "MCE storm" — that turned
# out to be benign CORRECTED L2-cache errors on s2idle RESUME, not failing RAM or a
# kernel-version bug. Reverted to :stable/FC44; the resume issue is handled separately
# via an intel_idle deep-C-state karg + a Lenovo BIOS/EC update. See .omc/HANDOFF.md.)
FROM ghcr.io/ublue-os/bazzite-nvidia:stable

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
