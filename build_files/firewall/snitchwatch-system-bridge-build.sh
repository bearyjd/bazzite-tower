#!/usr/bin/env bash
# Containerfile stage snitchwatch-bridge: FROM snitchwatch-prep, RUN --network=none, root;
# every third-party step runs as uid 65534 via snitchwatch-system-unpriv.py (M1 step 3).
# The ~660 third-party crates of the bridge build run here and ONLY here. No
# other stage descends from this one; the assembly stage receives just the
# allowlisted tarball, its sidecar and the two logs ($2) and re-checks them.
# Offline (M1 step 1): the crates come only from prep's `cargo fetch --locked`,
# so build scripts cannot reach the network (no second-stage download, no
# exfiltration over the network; the tarball itself still leaves). Everything in
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
# prep. Made by root, then handed to uid 65534 (cargo must write it).
python3 -B - "$factory/snitchwatch-system-scanner-build.py" "$work/cargo" "$work/bridge-cargo" "$work/source/Cargo.lock" <<'PY' || {
import importlib.util,pathlib,sys
spec=importlib.util.spec_from_file_location('scanner_build',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.make_fresh_cargo_home(pathlib.Path(sys.argv[2]),pathlib.Path(sys.argv[3]),pathlib.Path(sys.argv[4]).read_text())
PY
    echo 'bridge stage: prep cargo cache failed the Cargo.lock checksum allowlist; refusing.' >&2; exit 1;
}
# M1 step 3: everything that runs third-party code runs as uid 65534 through the
# drop helper (no caps, no groups, no_new_privs, verified before exec). Root first
# checks that the source is root-owned and not writable by it, that it owns nothing,
# and hands it only the scratch dirs; the selfcheck then proves the drop and that
# deliberate writes to root-owned tooling fail (printed as one JSON line).
python3 -B "$factory/snitchwatch-system-unpriv.py" prepare "$work"
python3 -B "$factory/snitchwatch-system-unpriv.py" run "$work" -- python3 -B "$factory/snitchwatch-system-unpriv.py" selfcheck "$work"
# Exported (not a one-shot prefix) so repro-check.sh's nested build-bridge.sh is offline too.
export SRC_DIR="$work/source" OUT_DIR="$work/dist" CARGO_HOME="$work/bridge-cargo" CARGO_TARGET_DIR="$work/target" CARGO_NET_OFFLINE=true
export SW_GIT_SHA="${pin[0]}" CARGO_BUILD_JOBS=4
unset ALLOW_DIRTY SW_RELEASE_TAG SW_INSTALL_DEPS
# The same fetch build-bridge.sh repeats for its license file, now offline: a
# failure here means prep's cache is incomplete, not that a build script needed network.
python3 -B "$factory/snitchwatch-system-unpriv.py" run "$work" -- bash "$factory/snitchwatch-system-bridge-unpriv.sh" fetch || {
    echo 'bridge stage: offline cargo fetch preflight failed (prep cache incomplete, or cargo fetch refuses offline); refusing.' >&2; exit 1;
}
python3 -B "$factory/snitchwatch-system-unpriv.py" run "$work" -- bash "$factory/snitchwatch-system-bridge-unpriv.sh" build 2>&1 | tee "$work/build.log"
mapfile -t tarballs < <(find "$OUT_DIR" -maxdepth 1 -type f -name '*.tar.gz')
[[ ${#tarballs[@]} == 1 ]] || { echo "bridge stage: expected one tarball in $OUT_DIR, found ${#tarballs[@]}" >&2; exit 1; }
first=${tarballs[0]}
python3 -B "$factory/snitchwatch-system-unpriv.py" run "$work" -- bash "$factory/snitchwatch-system-bridge-unpriv.sh" repro "$first" 2>&1 | tee "$work/repro.log"
# All execution of the bridge binary (ldd, --version, --help) happens in this stage, as uid 65534.
python3 -B "$factory/snitchwatch-system-unpriv.py" run "$work" -- bash "$factory/snitchwatch-system-bridge-unpriv.sh" verify "$first" "${pin[1]}"
# Drift detection only (this stage can forge it); assembly recomputes its own.
rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | LC_ALL=C sort | cmp - "$work/rpm-inventory.txt" || {
    echo 'bridge stage: RPM set differs from the snitchwatch-prep inventory; refusing.' >&2; exit 1;
}
install -m 0644 "$work/build.log" "$work/repro.log" "$OUT_DIR/"
python3 "$factory/snitchwatch-system-handoff.py" export bridge --pins "$pins" "$OUT_DIR" "$2"
rm -rf -- "$CARGO_TARGET_DIR" "$work/bridge-cargo"
