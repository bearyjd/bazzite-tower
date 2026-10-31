#!/usr/bin/env bash
# Containerfile stage snitchwatch-scanner: FROM snitchwatch-prep, RUN --network=none, root;
# every third-party step runs as uid 65534 via snitchwatch-system-unpriv.py (M1 step 3).
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
# Root, no third-party code: git archive of the pinned commit, pin checks, the
# Cargo.lock-allowlisted cargo home and the toolchain digest, into new root-owned dirs.
CARGO_HOME="$work/cargo" python3 -B "$factory/snitchwatch-system-scanner-build.py" --phase inputs --source "$work/source" --inputs "$work/scanner-inputs" --cargo-home "$work/scanner-cargo" --pins "$factory/snitchwatch-system-pins.json" --toolchain-digest "$work/toolchain.sha256"
# M1 step 3: the same drop as the bridge stages (no caps, no groups, no_new_privs,
# verified before exec). Root checks that the checkout and the export are
# root-owned and not writable by it, that it owns nothing, and hands it only the
# scratch dirs (scanner-cargo, scanner, home); the selfcheck proves the drop and
# that deliberate writes to root-owned tooling and inputs fail (one JSON line).
python3 -B "$factory/snitchwatch-system-unpriv.py" prepare "$work" scanner
python3 -B "$factory/snitchwatch-system-unpriv.py" run "$work" -- python3 -B "$factory/snitchwatch-system-unpriv.py" selfcheck "$work" scanner
# Both builds and their comparison, cargo test, the private-bus test, ldd, cargo
# metadata/tree and the license texts, all as uid 65534 (which can therefore forge them).
CARGO_BUILD_JOBS=4 python3 -B "$factory/snitchwatch-system-unpriv.py" run "$work" -- python3 -B "$factory/snitchwatch-system-scanner-build.py" --phase build --inputs "$work/scanner-inputs" --cargo-home "$work/scanner-cargo" --out "$work/scanner" --pins "$factory/snitchwatch-system-pins.json" --rpm-inventory "$work/rpm-inventory.txt"
# Root after the drop (every uid 65534 process is gone): copy only the allowlist out.
python3 "$factory/snitchwatch-system-handoff.py" export scanner "$work/scanner" "$2"
# Keep the stage layer small; only $2 crosses the stage boundary.
rm -rf -- "$work/scanner" "$work/scanner-cargo" "$work/scanner-inputs"
