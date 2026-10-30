#!/usr/bin/env bash
# Containerfile stage snitchwatch-bridge-rebuild: FROM snitchwatch-prep, RUN --network=none, root.
# M1 step 2: a sibling of snitchwatch-bridge that repeats the same offline
# build (the same lines as snitchwatch-system-bridge-build.sh from the probe to
# the one-tarball check, at the same paths, from the same prep layer) in a
# separate container that never sees the first stage. Only its tarball crosses
# to the assembly stage, which compares it byte for byte with the first one.
# The same third-party crates run here as root, so this stage is as untrusted
# as the first; it is an ancestor of nothing, executes nothing it built and
# writes no evidence. A deterministic hostile crate builds the same bytes here.
set -euo pipefail
factory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bash "$factory/snitchwatch-system-mode.sh"
[[ "${SNITCHWATCH_BRIDGE:-legacy}" == system ]] || { echo 'native factory requires system mode' >&2; exit 1; }
[[ $# -eq 2 && -d "$1/source" && "$2" == /?* && ! -e "$2" ]] || { echo 'bridge rebuild stage: usage WORK NEW-HANDOFF' >&2; exit 1; }
[[ -e /run/.containerenv || -e /.dockerenv ]] && [[ ! -e /run/.toolboxenv && ! -e /run/host ]] || {
    echo 'native factory requires a fresh builder container' >&2; exit 1;
}
work=$1
# From here to the one-tarball check: identical to snitchwatch-system-bridge-build.sh
# (tests/test-snitchwatch-image-stages.py compares the two). Byte equality needs it.
python3 "$factory/snitchwatch-system-handoff.py" probe-network offline
pins="$factory/snitchwatch-system-pins.json"
readarray -t pin < <(python3 - "$pins" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
for k in ('sourceCommit','bridgeVersion'):print(p[k])
PY
)
python3 -B - "$factory/snitchwatch-system-scanner-build.py" "$work/cargo" "$work/bridge-cargo" "$work/source/Cargo.lock" <<'PY' || {
import importlib.util,pathlib,sys
spec=importlib.util.spec_from_file_location('scanner_build',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.make_fresh_cargo_home(pathlib.Path(sys.argv[2]),pathlib.Path(sys.argv[3]),pathlib.Path(sys.argv[4]).read_text())
PY
    echo 'bridge stage: prep cargo cache failed the Cargo.lock checksum allowlist; refusing.' >&2; exit 1;
}
export SRC_DIR="$work/source" OUT_DIR="$work/dist" CARGO_HOME="$work/bridge-cargo" CARGO_TARGET_DIR="$work/target" CARGO_NET_OFFLINE=true
export SW_GIT_SHA="${pin[0]}" CARGO_BUILD_JOBS=4
unset ALLOW_DIRTY SW_RELEASE_TAG SW_INSTALL_DEPS
cargo fetch --locked --offline --target x86_64-unknown-linux-gnu --manifest-path "$SRC_DIR/Cargo.toml" || {
    echo 'bridge stage: offline cargo fetch preflight failed (prep cache incomplete, or cargo fetch refuses offline); refusing.' >&2; exit 1;
}
"$SRC_DIR/packaging/release/build-bridge.sh" 2>&1 | tee "$work/build.log"
mapfile -t tarballs < <(find "$OUT_DIR" -maxdepth 1 -type f -name '*.tar.gz')
[[ ${#tarballs[@]} == 1 ]] || { echo "bridge stage: expected one tarball in $OUT_DIR, found ${#tarballs[@]}" >&2; exit 1; }
# End of the identical block. No repro-check, verify or run of the binary here:
# the assembly stage's comparison is the evidence, not anything this stage says.
rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | LC_ALL=C sort | cmp - "$work/rpm-inventory.txt" || {
    echo 'bridge rebuild stage: RPM set differs from the snitchwatch-prep inventory; refusing.' >&2; exit 1;
}
python3 "$factory/snitchwatch-system-handoff.py" export bridge-rebuild --pins "$pins" "$OUT_DIR" "$2"
rm -rf -- "$CARGO_TARGET_DIR" "$work/bridge-cargo"
