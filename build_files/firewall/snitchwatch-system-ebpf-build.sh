#!/usr/bin/env bash
# Containerfile stages snitchwatch-ebpf and snitchwatch-ebpf-rebuild: FROM
# snitchwatch-ebpf-prep, RUN --network=none, root. Both stages run this one
# script with the same work paths (byte equality needs identical paths: the
# objects keep BTF strings that name the sources and headers), differing only
# in the handoff KIND; each is an ancestor of nothing. Inputs are data: the
# kernel header handoff from ebpf-kernel (allowlist + tarball digest checked)
# and ebpf_prog extracted as raw blobs from the prep checkout of the daemon's
# own OpenSnitch commit, re-hashed against the pin. The assembly stage refuses
# unless the two stages' objects are byte-identical.
# Usage: WORK KERNEL-HANDOFF NEW-HANDOFF ebpf|ebpf-rebuild
set -euo pipefail
factory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bash "$factory/snitchwatch-system-mode.sh"
[[ "${SNITCHWATCH_BRIDGE:-legacy}" == system ]] || { echo 'native factory requires system mode' >&2; exit 1; }
[[ $# -eq 4 && -d "$1" && -d "$2" && "$3" == /?* && ! -e "$3" ]] && [[ "$4" == ebpf || "$4" == ebpf-rebuild ]] || {
    echo 'ebpf stage: usage WORK KERNEL-HANDOFF NEW-HANDOFF ebpf|ebpf-rebuild' >&2; exit 1;
}
[[ -e /run/.containerenv || -e /.dockerenv ]] && [[ ! -e /run/.toolboxenv && ! -e /run/host ]] || {
    echo 'native factory requires a fresh builder container' >&2; exit 1;
}
work=$1 kernel=$2 dest=$3 kind=$4
python3 "$factory/snitchwatch-system-handoff.py" probe-network offline
rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | LC_ALL=C sort | cmp - "$work/rpm-inventory.txt" || {
    echo 'ebpf stage: RPM set differs from the snitchwatch-ebpf-prep inventory; refusing.' >&2; exit 1;
}
ebpf_pins="$factory/snitchwatch-system-ebpf-pins.json"
python3 "$factory/snitchwatch-system-handoff.py" check ebpf-kernel "$kernel"
python3 -B "$factory/snitchwatch-system-ebpf.py" build --pins "$ebpf_pins" \
    --daemon-pins "$factory/snitchwatch-system-daemon-pins.json" --native-pins "$factory/snitchwatch-system-pins.json" \
    --profile system --repo /work/native/source/vendor/opensnitch --kernel "$kernel" --work "$work/compile" --out "$work/out" \
    --rpm-inventory "$work/rpm-inventory.txt"
python3 "$factory/snitchwatch-system-handoff.py" export "$kind" --pins "$ebpf_pins" "$work/out" "$dest"
