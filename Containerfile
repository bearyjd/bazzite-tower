# Allow build scripts to be referenced without being copied into the final image
FROM scratch AS ctx
COPY build_files /

# Base: bazzite KDE + proprietary NVIDIA driver. Chosen over the -open variant
# for more reliable suspend/resume and power management on this Optimus laptop —
# the open kernel modules still have known gaps there (NVIDIA's own docs flag
# power management; upstream hybrid-laptop suspend bugs remained open into 2026).
# Swap to ghcr.io/ublue-os/bazzite-nvidia-open:stable to use the open modules.
# Desktop variant — not deck-based.
# TEMPORARILY PINNED to the last FC43 *stable* base (kernel 6.17.7-ba29) to escape the
# 7.0.9-ogc3.2 corrected-MCE / RAS-CEC page-offline storm that began at the F43->F44
# kernel jump (2026-06-13). The `ba` kernel lineage avoids the `ogc` regression; the
# 6.19 `testing-43` build shares the `ogc` lineage and is NOT a safe target.
# Revert to :stable once a fixed ogc kernel ships. Base mirrored by mirror-base.yml so
# this digest survives upstream pruning until F43 EOL.
#   stable-43 == 6.17.7-ba29.fc43 (verified from ostree.linux label 2026-06-15)
FROM ghcr.io/ublue-os/bazzite-nvidia@sha256:829e0cbd5c33b66a51a7a890c8f671c30391cacfd1b8fa5deedd1e495c17ace9

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
