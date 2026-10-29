#!/usr/bin/env bash
# Containerfile stage snitchwatch-prep (network, root). Only Fedora/Koji RPM
# scriptlets, git and `cargo fetch` (which runs no build script) execute here.
# The scanner, bridge, daemon and assembly stages all descend from this stage
# alone, so no third-party build code has ever run in their filesystem's
# ancestry (H1). Output: the fixed work directory $1, nothing else.
set -euo pipefail
factory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bash "$factory/snitchwatch-system-mode.sh"
[[ "${SNITCHWATCH_BRIDGE:-legacy}" == system ]] || { echo 'native factory requires system mode' >&2; exit 1; }
[[ $# -eq 1 && "$1" == /?* && ! -e "$1" ]] || { echo 'prep: work must be a new absolute directory' >&2; exit 1; }
[[ -e /run/.containerenv || -e /.dockerenv ]] && [[ ! -e /run/.toolboxenv && ! -e /run/host ]] || {
    echo 'native factory requires a fresh builder container' >&2; exit 1;
}
# Exact copy of the source commit's sole dependency list, checked again after
# cloning. No mutable installers, host package transaction or user unit install.
bash "$factory/snitchwatch-system-install-deps.sh"
work=$1
mkdir -p "$work"
# The unchanged upstream dependency list omits the RPM helper downloader.
command -v curl > /dev/null || timeout 240 dnf -y --setopt=install_weak_deps=False install curl
bash "$factory/snitchwatch-system-toolchain.sh" install "$factory/snitchwatch-system-pins.json" "$work/toolchain"
# Fingerprint the pinned rust/cargo/rust-std-static files now; the scanner stage
# re-hashes them. Nothing but RPM scriptlets has run, and no later stage that
# compiles third-party code is an ancestor of the scanner stage.
python3 "$factory/snitchwatch-system-scanner-build.py" --record-toolchain "$work/toolchain.sha256" \
    --pins "$factory/snitchwatch-system-pins.json"
# Only fake authorization/private-bus tests run in the scanner stage.
command -v dbus-run-session > /dev/null || timeout 240 dnf -y --setopt=install_weak_deps=False install dbus-daemon
# Positive control: the same probe must fail in the --network=none stages.
python3 "$factory/snitchwatch-system-handoff.py" probe-network online
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
cmp "$factory/snitchwatch-system-install-deps.sh" "$work/source/packaging/release/install-deps.sh"
# Hash-check every pinned input before running any of them (the version helper included).
python3 - "$factory/snitchwatch-system-pins.json" "$work/source" <<'PY'
import hashlib,json,pathlib,sys
p=json.load(open(sys.argv[1]));root=pathlib.Path(sys.argv[2])
for name,expected in p['sourceFiles'].items():
 if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:raise SystemExit('Pinned source input mismatch: '+name)
PY
[[ "$(python3 -B "$work/source/packaging/release/bridge_artifact.py" version --repo "$work/source")" == "${pin[5]}" ]] || {
    echo 'Pinned source crate version differs from required bridgeVersion; reconcile source before building.' >&2; exit 1;
}
# The bridge build's own fetch command, so the offline scanner stage sees the
# same .crate set it used to borrow from the bridge build. Downloads only.
CARGO_HOME="$work/cargo" cargo fetch --locked --target x86_64-unknown-linux-gnu --manifest-path "$work/source/Cargo.toml"
rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | LC_ALL=C sort > "$work/rpm-inventory.txt"
