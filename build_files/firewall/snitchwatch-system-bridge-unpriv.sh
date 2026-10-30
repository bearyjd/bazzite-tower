#!/usr/bin/env bash
# The third-party half of both sibling bridge stages (M1 step 3). It runs ONLY as
# `python3 -B "$factory/snitchwatch-system-unpriv.py" run "$work" -- bash <this> MODE`,
# i.e. as uid 65534 with no capabilities, no groups and no_new_privs, after the
# root-owned stage script has prepared the scratch dirs (bridge-cargo, dist,
# target, home under WORK). Every crate build script, the upstream build and
# repro-check, and every run of the built binary happen here; none of them can
# write the source checkout, the toolchain, PATH tools, /etc or /factory.
# Environment: the stage script's exports (SRC_DIR OUT_DIR CARGO_HOME
# CARGO_TARGET_DIR CARGO_NET_OFFLINE SW_GIT_SHA CARGO_BUILD_JOBS), PATH and HOME
# set by the drop helper.
set -euo pipefail
(( EUID != 0 )) || { echo 'bridge unprivileged step: refusing to run as root' >&2; exit 1; }
case "${1:-}" in
    fetch)
        [[ $# -eq 1 ]] || { echo 'bridge unprivileged step: usage fetch (no arguments)' >&2; exit 1; }
        # The same fetch build-bridge.sh repeats for its license file, offline.
        cargo fetch --locked --offline --target x86_64-unknown-linux-gnu --manifest-path "$SRC_DIR/Cargo.toml"
        ;;
    build)
        [[ $# -eq 1 ]] || { echo 'bridge unprivileged step: usage build (no arguments)' >&2; exit 1; }
        "$SRC_DIR/packaging/release/build-bridge.sh"
        ;;
    repro)
        [[ $# -eq 2 ]] || { echo 'bridge unprivileged step: usage repro TARBALL' >&2; exit 1; }
        "$SRC_DIR/packaging/release/repro-check.sh" "$2"
        ;;
    verify)
        # Runs the built binary (ldd, --version, --help); the digest is this stage's own.
        [[ $# -eq 3 ]] || { echo 'bridge unprivileged step: usage verify TARBALL VERSION' >&2; exit 1; }
        python3 -B "$SRC_DIR/packaging/release/bridge_artifact.py" verify --tarball "$2" \
            --expect-sha256 "$(sha256sum "$2" | awk '{print $1}')" --expect-version "$3" --check-ldd --run-flags
        ;;
    *)
        echo 'bridge unprivileged step: usage fetch | build | repro TARBALL | verify TARBALL VERSION' >&2; exit 1
        ;;
esac
