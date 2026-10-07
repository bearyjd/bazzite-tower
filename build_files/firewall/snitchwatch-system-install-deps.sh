#!/usr/bin/env bash
# The ONE dependency list for the bridge release builder container
# (SW_BUILDER_IMAGE in pins.env). build-bridge.sh (SW_INSTALL_DEPS=1),
# release.yml and the justfile recipes all call this script instead of
# repeating the list — add new build/verify tools here and nowhere else.
#
#   build:   git gcc rust cargo protobuf-compiler
#   pack:    python3 (bridge_artifact.py), tar gzip
#   verify:  binutils (readelf/objdump), glibc-common (ldd), coreutils
#            (sha256sum/install/timeout), diffutils (cmp), findutils
#   install: systemd (systemd-analyze verify --user, systemctl --global)
#   buildinfo: rpm (NEVRAs for the MANIFEST: the toolchain RPMs by name —
#            incl. glibc-devel, libgcc, llvm-libs, zlib-ng-compat, which come
#            in as dependencies — plus the full `rpm -qa`, because the image
#            digest pins only the base layer and dnf pulls live updates)
#
# It installs into the root filesystem, so it refuses to run outside a
# container.
set -euo pipefail

if [[ ! -e /run/.containerenv && ! -e /.dockerenv ]]; then
    echo "install-deps.sh: refusing to run outside a container (no /run/.containerenv" \
        "or /.dockerenv); run it inside the pinned builder image from pins.env" >&2
    exit 1
fi
if [[ -e /run/.toolboxenv || -e /run/host ]]; then
    echo "install-deps.sh: refusing to run in a toolbox/distrobox (a long-lived container" \
        "sharing your home); use a fresh container from the pinned builder image" >&2
    exit 1
fi

dnf -y --setopt=install_weak_deps=False install \
    git gcc rust cargo protobuf-compiler python3 binutils systemd tar gzip \
    findutils diffutils coreutils glibc-common rpm
