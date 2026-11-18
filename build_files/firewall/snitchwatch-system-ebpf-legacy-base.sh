#!/usr/bin/env bash
# Containerfile stage ebpf-legacy-base (network, root): the pinned Fedora builder
# plus python3 (absent from the bare image; every later legacy script and the
# handoff/ebpf helpers are python). Nothing else: the compiler stage and the
# assembly stage both descend from this one, so the assembly never has a compiler
# in its ancestry (same shape as snitchwatch-prep for the system profile).
set -euo pipefail
[[ "${SNITCHWATCH_BRIDGE:-legacy}" == legacy ]] || { echo 'ebpf legacy base: legacy profile only' >&2; exit 1; }
[[ -e /run/.containerenv || -e /.dockerenv ]] && [[ ! -e /run/.toolboxenv && ! -e /run/host ]] || {
    echo 'native factory requires a fresh builder container' >&2; exit 1;
}
timeout 240 dnf -y --setopt=install_weak_deps=False install python3
