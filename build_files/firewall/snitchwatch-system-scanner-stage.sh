#!/usr/bin/env bash
# Containerfile stage snitchwatch-scanner: FROM snitchwatch-prep, RUN --network=none.
# Builds the root-privileged scanner in a filesystem where the bridge's ~660
# third-party build scripts never ran (H1). Hands over only the allowlisted
# binaries and evidence ($2); never the source export, cargo home or targets.
set -euo pipefail
factory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bash "$factory/snitchwatch-system-mode.sh"
[[ "${SNITCHWATCH_BRIDGE:-legacy}" == system ]] || { echo 'native factory requires system mode' >&2; exit 1; }
[[ $# -eq 2 && -d "$1/source" && "$2" == /?* && ! -e "$2" ]] || { echo 'scanner stage: usage WORK NEW-HANDOFF' >&2; exit 1; }
[[ -e /run/.containerenv || -e /.dockerenv ]] && [[ ! -e /run/.toolboxenv && ! -e /run/host ]] || {
    echo 'native factory requires a fresh builder container' >&2; exit 1;
}
work=$1
python3 "$factory/snitchwatch-system-handoff.py" probe-network offline
rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | LC_ALL=C sort | cmp - "$work/rpm-inventory.txt" || {
    echo 'scanner stage: RPM set differs from the snitchwatch-prep inventory; refusing.' >&2; exit 1;
}
CARGO_HOME="$work/cargo" CARGO_BUILD_JOBS=4 python3 "$factory/snitchwatch-system-scanner-build.py" \
    --source "$work/source" --out "$work/scanner" --pins "$factory/snitchwatch-system-pins.json" \
    --rpm-inventory "$work/rpm-inventory.txt" --toolchain-digest "$work/toolchain.sha256"
python3 "$factory/snitchwatch-system-handoff.py" export scanner "$work/scanner" "$2"
# Keep the stage layer small; only $2 crosses the stage boundary.
rm -rf -- "$work/scanner"
