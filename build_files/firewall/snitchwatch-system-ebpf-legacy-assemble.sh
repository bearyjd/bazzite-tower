#!/usr/bin/env bash
# Containerfile stage ebpf-legacy-assembly: FROM ebpf-legacy-base, RUN
# --network=none, root. The legacy counterpart of the eBPF half of
# snitchwatch-system-assemble.sh: the two sibling compiles must be byte-identical,
# and the manifest is computed here from the pins, the kernel facts and hashes
# taken here. The handoffs are DATA (allowlist and hash checked, never executed).
# Output: NEW-OUT holds exactly the profile marker, the staged objects and the
# manifest; 95-firewall.sh installs the objects over the RPM's after extraction.
# Usage: HANDOFF NEW-OUT
set -euo pipefail
factory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bash "$factory/snitchwatch-system-mode.sh"
[[ "${SNITCHWATCH_BRIDGE:-legacy}" == legacy ]] || { echo 'ebpf legacy assembly: legacy profile only' >&2; exit 1; }
[[ $# -eq 2 && -d "$1" && "$2" == /?* && ! -e "$2" ]] || { echo 'ebpf legacy assembly: usage HANDOFF NEW-OUT' >&2; exit 1; }
[[ -e /run/.containerenv || -e /.dockerenv ]] && [[ ! -e /run/.toolboxenv && ! -e /run/host ]] || {
    echo 'native factory requires a fresh builder container' >&2; exit 1;
}
handoff=$1 out=$2
guard="$factory/snitchwatch-system-handoff.py"
ebpf_pins="$factory/snitchwatch-system-ebpf-pins.json"
python3 "$guard" probe-network offline
# The parent too: a writable /handoff would let anything rename the checked trees.
handoff_meta=$(stat -c '%u:%g:%a' "$handoff" || true)
IFS=: read -r h_uid h_gid h_mode <<< "$handoff_meta" || true
if ! [[ "$h_uid" == 0 && "$h_gid" == 0 && "$h_mode" =~ ^[0-7]{3}$ ]] || (( (8#$h_mode & 8#022) != 0 )); then
    echo "ebpf legacy assembly: $handoff must be root-owned and not group/other writable: ${handoff_meta:-unreadable}" >&2; exit 1
fi
python3 "$guard" check ebpf-kernel-facts "$handoff/ebpf-kernel"
python3 "$guard" check ebpf --pins "$ebpf_pins" "$handoff/ebpf"
python3 "$guard" check ebpf-rebuild --pins "$ebpf_pins" "$handoff/ebpf-rebuild"
python3 "$guard" compare-ebpf --pins "$ebpf_pins" "$handoff/ebpf" "$handoff/ebpf-rebuild" || {
    echo 'ebpf legacy assembly: the sibling eBPF rebuild is not byte-identical to the first eBPF build; refusing.' >&2; exit 1;
}
python3 -B "$factory/snitchwatch-system-ebpf.py" stage --pins "$ebpf_pins" --profile legacy \
    --kernel-facts "$handoff/ebpf-kernel/kernel-facts.json" "$handoff/ebpf" "$out"
install -m 0644 "$factory/snitchwatch-system-legacy-marker" "$out/usr/share/bazzite-tower/snitchwatch-bridge-profile"
