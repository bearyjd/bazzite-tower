#!/usr/bin/env bash
# Fresh pinned builder only; output is an image overlay, never a live install.
set -euo pipefail
factory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bash "$factory/snitchwatch-system-mode.sh"
[[ "${SNITCHWATCH_BRIDGE:-legacy}" == system ]] || { echo 'native factory requires system mode' >&2; exit 1; }
[[ $# -eq 1 && "$1" != / && ! -e "$1" ]] || { echo 'output must be a new isolated directory' >&2; exit 1; }
[[ -e /run/.containerenv || -e /.dockerenv ]] && [[ ! -e /run/.toolboxenv && ! -e /run/host ]] || {
    echo 'native factory requires a fresh builder container' >&2; exit 1;
}
# Exact copy of the source commit's sole dependency list, checked again after
# cloning. No mutable installers, host package transaction or user unit install.
bash "$factory/snitchwatch-system-install-deps.sh"
mkdir -p /work
work=$(mktemp -d /work/snitchwatch-native.XXXXXX)
readarray -t pin < <(python3 - "$factory/snitchwatch-system-pins.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
for k in ('sourceRepository','sourceCommit','submoduleCommit','rustVersion','protocVersion','bridgeVersion'):print(p[k])
PY
)
[[ "$(rustc --version)" == "rustc ${pin[3]} "* && "$(protoc --version)" == "libprotoc ${pin[4]}" ]] || {
    rustc --version; protoc --version; echo 'Live builder toolchain drifted from reviewed pins; refusing.' >&2; exit 1;
}
git init "$work/source"
git -C "$work/source" remote add origin "${pin[0]}"
git -C "$work/source" fetch --depth=1 origin "${pin[1]}"
git -C "$work/source" checkout --detach FETCH_HEAD
git -C "$work/source" submodule update --init --depth=1 vendor/opensnitch
[[ "$(git -C "$work/source" rev-parse HEAD)" == "${pin[1]}" && "$(git -C "$work/source/vendor/opensnitch" rev-parse HEAD)" == "${pin[2]}" ]]
[[ "$(python3 "$work/source/packaging/release/bridge_artifact.py" version --repo "$work/source")" == "${pin[5]}" ]] || {
    echo 'Pinned source crate version differs from required bridgeVersion; reconcile source before building.' >&2; exit 1;
}
cmp "$factory/snitchwatch-system-install-deps.sh" "$work/source/packaging/release/install-deps.sh"
python3 - "$factory/snitchwatch-system-pins.json" "$work/source" <<'PY'
import hashlib,json,pathlib,sys
p=json.load(open(sys.argv[1]));root=pathlib.Path(sys.argv[2])
for name,expected in p['sourceFiles'].items():
 if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:raise SystemExit('Pinned source input mismatch: '+name)
PY
export SRC_DIR="$work/source" OUT_DIR="$work/dist" CARGO_HOME="$work/cargo" CARGO_TARGET_DIR="$work/target"
export SW_GIT_SHA="${pin[1]}" CARGO_BUILD_JOBS=4
unset ALLOW_DIRTY SW_RELEASE_TAG SW_INSTALL_DEPS
"$SRC_DIR/packaging/release/build-bridge.sh" 2>&1 | tee "$work/build.log"
mapfile -t tarballs < <(find "$OUT_DIR" -maxdepth 1 -type f -name '*.tar.gz')
[[ ${#tarballs[@]} == 1 ]]
first=${tarballs[0]}
"$SRC_DIR/packaging/release/repro-check.sh" "$first" 2>&1 | tee "$work/repro.log"
rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | LC_ALL=C sort > "$work/rpm-inventory.txt"
artifact_sha=$(sha256sum "$first" | awk '{print $1}')
python3 "$SRC_DIR/packaging/release/bridge_artifact.py" verify --tarball "$first" \
    --expect-sha256 "$artifact_sha" --expect-version "${pin[5]}" --check-ldd --run-flags --extract-to "$work/verified"
python3 "$factory/snitchwatch-system-stage.py" \
    --source "$SRC_DIR" --artifact "$work/verified/$(basename "$first" .tar.gz)" --out "$1" \
    --pins "$factory/snitchwatch-system-pins.json" --verifier "$factory/snitchwatch-system-verify.py" \
    --legacy-config /candidate/legacy-config.json --candidate-config /candidate/system-config.json \
    --candidate-dropin /candidate/20-system-bridge.conf --build-log "$work/build.log" \
    --repro-log "$work/repro.log" --rpm-inventory "$work/rpm-inventory.txt" --tarball "$first"
# Preserve source/targets/logs in the isolated build layer for audit. Only $1
# is copied into the final image, so no compiler, user unit or checkout ships.
