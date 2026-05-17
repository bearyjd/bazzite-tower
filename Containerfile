# Allow build scripts to be referenced without being copied into the final image
FROM scratch AS ctx
COPY build_files /

# Base: bazzite KDE + Nvidia open kernel modules (RTX 30/40 series recommended)
# Desktop variant — not deck-based, tracks F44+
FROM ghcr.io/ublue-os/bazzite-nvidia-open:stable

### MODIFICATIONS
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    --mount=type=cache,dst=/var/cache \
    --mount=type=cache,dst=/var/log \
    --mount=type=tmpfs,dst=/tmp \
    /ctx/build.sh

### LINTING
RUN bootc container lint
