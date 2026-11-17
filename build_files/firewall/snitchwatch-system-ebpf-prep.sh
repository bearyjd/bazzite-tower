#!/usr/bin/env bash
# Containerfile stage snitchwatch-ebpf-prep (network, root): FROM snitchwatch-prep.
# Installs the BPF toolchain from pinned, Fedora-signed Koji RPMs (clang21 and
# llvm21 21.1.8-10.fc45; clang 23 cannot build these modules) plus live `make`,
# so both offline compile stages see the same verified compiler. Only RPM
# scriptlets run here. A stage of its own rather than part of snitchwatch-prep,
# so the scanner, bridge and daemon ancestry and their shared RPM inventory do
# not gain a compiler. Output: the fixed work directory $1, nothing else.
set -euo pipefail
factory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bash "$factory/snitchwatch-system-mode.sh"
[[ "${SNITCHWATCH_BRIDGE:-legacy}" == system ]] || { echo 'native factory requires system mode' >&2; exit 1; }
[[ $# -eq 1 && "$1" == /?* && ! -e "$1" ]] || { echo 'ebpf prep: work must be a new absolute directory' >&2; exit 1; }
[[ -e /run/.containerenv || -e /.dockerenv ]] && [[ ! -e /run/.toolboxenv && ! -e /run/host ]] || {
    echo 'native factory requires a fresh builder container' >&2; exit 1;
}
work=$1
# Positive control: the same probe must fail in both --network=none compile stages.
python3 "$factory/snitchwatch-system-handoff.py" probe-network online
mkdir -p "$work"
bash "$factory/snitchwatch-system-toolchain.sh" install "$factory/snitchwatch-system-ebpf-pins.json" "$work/toolchain" make
# The installed headers are re-checked against the verified files; the RPMs themselves are not needed later.
rm -r -- "$work/toolchain"
rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | LC_ALL=C sort > "$work/rpm-inventory.txt"
