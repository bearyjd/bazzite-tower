#!/usr/bin/env bash
# Containerfile stages snitchwatch-ebpf[-rebuild] (system) and ebpf-legacy[-rebuild]
# (legacy): FROM the profile's ebpf prep stage, RUN --network=none, root. Both
# stages of a profile run this one script with the same work paths (byte equality
# needs identical paths: the objects keep BTF strings that name the sources and
# headers), differing only in the handoff KIND; each is an ancestor of nothing.
# Inputs are data: the kernel header handoff from ebpf-kernel (allowlist + tarball
# digest checked) and ebpf_prog extracted as raw blobs from the prep checkout of
# the loading daemon's own OpenSnitch commit (system: the snitchwatch submodule;
# legacy: the fetched v1.8.0 commit), re-hashed against the pin. The assembly stage
# refuses unless the two stages' objects are byte-identical.
# SNITCHWATCH_BRIDGE (system|legacy) selects the profile.
# Usage: WORK KERNEL-HANDOFF NEW-HANDOFF ebpf|ebpf-rebuild
set -euo pipefail
factory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bash "$factory/snitchwatch-system-mode.sh"
profile=${SNITCHWATCH_BRIDGE:-legacy}
[[ "$profile" == system || "$profile" == legacy ]] || { echo 'ebpf stage: unknown profile' >&2; exit 1; }
[[ $# -eq 4 && -d "$1" && -d "$2" && "$3" == /?* && ! -e "$3" ]] && [[ "$4" == ebpf || "$4" == ebpf-rebuild ]] || {
    echo 'ebpf stage: usage WORK KERNEL-HANDOFF NEW-HANDOFF ebpf|ebpf-rebuild' >&2; exit 1;
}
[[ -e /run/.containerenv || -e /.dockerenv ]] && [[ ! -e /run/.toolboxenv && ! -e /run/host ]] || {
    echo 'native factory requires a fresh builder container' >&2; exit 1;
}
work=$1 kernel=$2 dest=$3 kind=$4
python3 "$factory/snitchwatch-system-handoff.py" probe-network offline
rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | LC_ALL=C sort | cmp - "$work/rpm-inventory.txt" || {
    echo 'ebpf stage: RPM set differs from the ebpf prep inventory; refusing.' >&2; exit 1;
}
ebpf_pins="$factory/snitchwatch-system-ebpf-pins.json"
python3 "$factory/snitchwatch-system-handoff.py" check ebpf-kernel "$kernel"
if [[ "$profile" == system ]]; then
    repo=/work/native/source/vendor/opensnitch
    pairing=(--daemon-pins "$factory/snitchwatch-system-daemon-pins.json" --native-pins "$factory/snitchwatch-system-pins.json")
else
    repo="$work/source"
    pairing=()
fi
python3 -B "$factory/snitchwatch-system-ebpf.py" build --pins "$ebpf_pins" "${pairing[@]}" \
    --profile "$profile" --repo "$repo" --kernel "$kernel" --work "$work/compile" --out "$work/out" \
    --rpm-inventory "$work/rpm-inventory.txt"
python3 "$factory/snitchwatch-system-handoff.py" export "$kind" --pins "$ebpf_pins" "$work/out" "$dest"
