#!/usr/bin/env bash
# Containerfile stages snitchwatch-ebpf-prep (system, FROM snitchwatch-prep) and
# ebpf-legacy-prep (legacy, FROM ebpf-legacy-base) (network, root).
# Installs the BPF toolchain from pinned, Fedora-signed Koji RPMs (clang21 and
# llvm21 21.1.8-10.fc45; clang 23 cannot build these modules) plus live `make`,
# so both offline compile stages see the same verified compiler. Only RPM
# scriptlets run here. A stage of its own rather than part of snitchwatch-prep,
# so the scanner, bridge and daemon ancestry and their shared RPM inventory do
# not gain a compiler. Legacy has no snitchwatch checkout: it also installs git
# and fetches the one pinned upstream OpenSnitch commit of its daemon RPM (v1.8.0)
# into $work/source; the offline compile re-hashes ebpf_prog against the pin.
# Output: the fixed work directory $1, nothing else.
# SNITCHWATCH_BRIDGE (system|legacy) selects the profile.
set -euo pipefail
factory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bash "$factory/snitchwatch-system-mode.sh"
profile=${SNITCHWATCH_BRIDGE:-legacy}
[[ "$profile" == system || "$profile" == legacy ]] || { echo 'ebpf prep: unknown profile' >&2; exit 1; }
[[ $# -eq 1 && "$1" == /?* && ! -e "$1" ]] || { echo 'ebpf prep: work must be a new absolute directory' >&2; exit 1; }
[[ -e /run/.containerenv || -e /.dockerenv ]] && [[ ! -e /run/.toolboxenv && ! -e /run/host ]] || {
    echo 'native factory requires a fresh builder container' >&2; exit 1;
}
work=$1
# Positive control: the same probe must fail in both --network=none compile stages.
python3 "$factory/snitchwatch-system-handoff.py" probe-network online
mkdir -p "$work"
# The pins digest anchor (EXPECTED_EBPF_PINS_SHA256) is enforced before any RPM is fetched.
python3 -B "$factory/snitchwatch-system-ebpf.py" check-pins "$factory/snitchwatch-system-ebpf-pins.json"
extras=(make)
if [[ "$profile" == legacy ]]; then
    # The toolchain helper downloads with curl; git-core is the compile stage's blob reader.
    command -v curl > /dev/null || timeout 240 dnf -y --setopt=install_weak_deps=False install curl
    extras=(make git-core)
fi
bash "$factory/snitchwatch-system-toolchain.sh" install "$factory/snitchwatch-system-ebpf-pins.json" "$work/toolchain" "${extras[@]}"
# The installed headers are re-checked against the verified files; the RPMs themselves are not needed later.
rm -r -- "$work/toolchain"
if [[ "$profile" == legacy ]]; then
    readarray -t pin < <(python3 -B "$factory/snitchwatch-system-ebpf.py" source-pin "$factory/snitchwatch-system-ebpf-pins.json" |
        python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["repository"]); print(d["commit"])')
    # Fetch by commit (verified by git's own object hash): not the tag, which could be moved.
    git init "$work/source"
    git -C "$work/source" remote add origin "${pin[0]}"
    git -C "$work/source" fetch --depth=1 origin "${pin[1]}"
    [[ "$(git -C "$work/source" rev-parse FETCH_HEAD)" == "${pin[1]}" ]] || { echo 'ebpf prep: fetched commit is not the pin' >&2; exit 1; }
fi
rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | LC_ALL=C sort > "$work/rpm-inventory.txt"
