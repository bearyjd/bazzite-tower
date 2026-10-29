#!/usr/bin/env bash
# Containerfile stage snitchwatch-bridge: FROM snitchwatch-prep, network, root.
# The ~660 third-party crates of the bridge build run here and ONLY here. No
# other stage descends from this one; the assembly stage receives just the
# allowlisted tarball, its sidecar and the two logs ($2) and re-checks them.
# Everything in this stage, those outputs included, is untrusted (open: M1).
set -euo pipefail
factory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bash "$factory/snitchwatch-system-mode.sh"
[[ "${SNITCHWATCH_BRIDGE:-legacy}" == system ]] || { echo 'native factory requires system mode' >&2; exit 1; }
[[ $# -eq 2 && -d "$1/source" && "$2" == /?* && ! -e "$2" ]] || { echo 'bridge stage: usage WORK NEW-HANDOFF' >&2; exit 1; }
[[ -e /run/.containerenv || -e /.dockerenv ]] && [[ ! -e /run/.toolboxenv && ! -e /run/host ]] || {
    echo 'native factory requires a fresh builder container' >&2; exit 1;
}
work=$1
pins="$factory/snitchwatch-system-pins.json"
readarray -t pin < <(python3 - "$pins" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
for k in ('sourceCommit','bridgeVersion'):print(p[k])
PY
)
export SRC_DIR="$work/source" OUT_DIR="$work/dist" CARGO_HOME="$work/cargo" CARGO_TARGET_DIR="$work/target"
export SW_GIT_SHA="${pin[0]}" CARGO_BUILD_JOBS=4
unset ALLOW_DIRTY SW_RELEASE_TAG SW_INSTALL_DEPS
"$SRC_DIR/packaging/release/build-bridge.sh" 2>&1 | tee "$work/build.log"
mapfile -t tarballs < <(find "$OUT_DIR" -maxdepth 1 -type f -name '*.tar.gz')
[[ ${#tarballs[@]} == 1 ]] || { echo "bridge stage: expected one tarball in $OUT_DIR, found ${#tarballs[@]}" >&2; exit 1; }
first=${tarballs[0]}
"$SRC_DIR/packaging/release/repro-check.sh" "$first" 2>&1 | tee "$work/repro.log"
# All execution of the bridge binary (ldd, --version, --help) happens in this stage.
python3 -B "$SRC_DIR/packaging/release/bridge_artifact.py" verify --tarball "$first" \
    --expect-sha256 "$(sha256sum "$first" | awk '{print $1}')" --expect-version "${pin[1]}" --check-ldd --run-flags
# Drift detection only (this stage can forge it); assembly recomputes its own.
rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | LC_ALL=C sort | cmp - "$work/rpm-inventory.txt" || {
    echo 'bridge stage: RPM set differs from the snitchwatch-prep inventory; refusing.' >&2; exit 1;
}
install -m 0644 "$work/build.log" "$work/repro.log" "$OUT_DIR/"
python3 "$factory/snitchwatch-system-handoff.py" export bridge --pins "$pins" "$OUT_DIR" "$2"
rm -rf -- "$CARGO_TARGET_DIR"
