#!/usr/bin/env bash
# Containerfile stage snitchwatch-bridge: FROM snitchwatch-prep, RUN --network=none, root.
# The ~660 third-party crates of the bridge build run here and ONLY here. No
# other stage descends from this one; the assembly stage receives just the
# allowlisted tarball, its sidecar and the two logs ($2) and re-checks them.
# Offline (M1 step 1): the crates come only from prep's `cargo fetch --locked`,
# so build scripts cannot download a payload or send data out. Everything in
# this stage, those outputs included, is still untrusted and its evidence is
# still self-attesting (sha, logs, repro-check are all made here; open: M1).
set -euo pipefail
factory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bash "$factory/snitchwatch-system-mode.sh"
[[ "${SNITCHWATCH_BRIDGE:-legacy}" == system ]] || { echo 'native factory requires system mode' >&2; exit 1; }
[[ $# -eq 2 && -d "$1/source" && "$2" == /?* && ! -e "$2" ]] || { echo 'bridge stage: usage WORK NEW-HANDOFF' >&2; exit 1; }
[[ -e /run/.containerenv || -e /.dockerenv ]] && [[ ! -e /run/.toolboxenv && ! -e /run/host ]] || {
    echo 'native factory requires a fresh builder container' >&2; exit 1;
}
work=$1
python3 "$factory/snitchwatch-system-handoff.py" probe-network offline
pins="$factory/snitchwatch-system-pins.json"
readarray -t pin < <(python3 - "$pins" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
for k in ('sourceCommit','bridgeVersion'):print(p[k])
PY
)
# Allowlisted CARGO_HOME for the build: only .crate files whose sha256 equals
# the pinned Cargo.lock, plus the index. No config, bin or unpacked src from
# prep. Hardening against a stale or edited cache, not isolation (root here).
python3 -B - "$factory/snitchwatch-system-scanner-build.py" "$work/cargo" "$work/bridge-cargo" "$work/source/Cargo.lock" <<'PY' || {
import importlib.util,pathlib,sys
spec=importlib.util.spec_from_file_location('scanner_build',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.make_fresh_cargo_home(pathlib.Path(sys.argv[2]),pathlib.Path(sys.argv[3]),pathlib.Path(sys.argv[4]).read_text())
PY
    echo 'bridge stage: prep cargo cache failed the Cargo.lock checksum allowlist; refusing.' >&2; exit 1;
}
# Exported (not a one-shot prefix) so repro-check.sh's nested build-bridge.sh is offline too.
export SRC_DIR="$work/source" OUT_DIR="$work/dist" CARGO_HOME="$work/bridge-cargo" CARGO_TARGET_DIR="$work/target" CARGO_NET_OFFLINE=true
export SW_GIT_SHA="${pin[0]}" CARGO_BUILD_JOBS=4
unset ALLOW_DIRTY SW_RELEASE_TAG SW_INSTALL_DEPS
# The same fetch build-bridge.sh repeats for its license file, now offline: a
# failure here means prep's cache is incomplete, not that a build script needed network.
cargo fetch --locked --offline --target x86_64-unknown-linux-gnu --manifest-path "$SRC_DIR/Cargo.toml" || {
    echo 'bridge stage: offline cargo fetch preflight failed (prep cache incomplete, or cargo fetch refuses offline); refusing.' >&2; exit 1;
}
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
rm -rf -- "$CARGO_TARGET_DIR" "$work/bridge-cargo"
