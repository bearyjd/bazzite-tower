#!/usr/bin/env bash
# Containerfile stage snitchwatch-system (assembly): FROM snitchwatch-prep,
# RUN --network=none, root. Runs no third-party code as root: its tools are
# this script's directory (COPY --from=ctx, a scratch stage) and the prep
# checkout (re-hashed against the pins first). The scanner, bridge and daemon
# stage outputs are DATA: allowlist-checked, hash-checked, never executed as
# root (the bridge binary runs only as uid 65534 inside stage.py).
# Usage: WORK HANDOFF NEW-OUT
set -euo pipefail
factory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bash "$factory/snitchwatch-system-mode.sh"
[[ "${SNITCHWATCH_BRIDGE:-legacy}" == system ]] || { echo 'native factory requires system mode' >&2; exit 1; }
[[ $# -eq 3 && -d "$1/source" && -d "$2" && "$3" == /?* && ! -e "$3" ]] || { echo 'assembly: usage WORK HANDOFF NEW-OUT' >&2; exit 1; }
[[ -e /run/.containerenv || -e /.dockerenv ]] && [[ ! -e /run/.toolboxenv && ! -e /run/host ]] || {
    echo 'native factory requires a fresh builder container' >&2; exit 1;
}
work=$1 handoff=$2 out=$3
pins="$factory/snitchwatch-system-pins.json"
guard="$factory/snitchwatch-system-handoff.py"
daemon_verifier="$factory/snitchwatch-system-daemon-verify.py"
python3 "$guard" probe-network offline
rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | LC_ALL=C sort | cmp - "$work/rpm-inventory.txt" || {
    echo 'assembly: RPM set differs from the snitchwatch-prep inventory; refusing.' >&2; exit 1;
}
# The prep checkout is the only upstream code used here; re-check it now.
cmp "$factory/snitchwatch-system-install-deps.sh" "$work/source/packaging/release/install-deps.sh"
python3 - "$pins" "$work/source" <<'PY'
import hashlib,json,pathlib,sys
p=json.load(open(sys.argv[1]));root=pathlib.Path(sys.argv[2])
for name,expected in p['sourceFiles'].items():
 if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:raise SystemExit('assembly: prep checkout differs from pin: '+name)
PY
# Every COPY --from result must be exactly its allowlist (no links, set-id, caps, extra files).
# The parent too: a writable /handoff would let anything rename the checked trees.
handoff_meta=$(stat -c '%u:%g:%a' "$handoff" || true)
IFS=: read -r h_uid h_gid h_mode <<< "$handoff_meta" || true
if ! [[ "$h_uid" == 0 && "$h_gid" == 0 && "$h_mode" =~ ^[0-7]{3}$ ]] || (( (8#$h_mode & 8#022) != 0 )); then
    echo "assembly: $handoff must be root-owned and not group/other writable (no special bits): ${handoff_meta:-unreadable}" >&2; exit 1
fi
python3 "$guard" check scanner "$handoff/scanner"
python3 "$guard" check bridge --pins "$pins" "$handoff/bridge"
python3 "$guard" check daemon --daemon-verifier "$daemon_verifier" "$handoff/daemon"
cmp "$daemon_verifier" "$handoff/daemon/usr/libexec/snitchwatch/verify-system-daemon.py" || {
    echo 'assembly: daemon handoff verifier differs from the tower copy; refusing.' >&2; exit 1;
}
python3 -B "$daemon_verifier" --stage-only --root "$handoff/daemon"
tarball="$handoff/bridge/$(python3 - "$pins" <<'PY'
import json,sys
print('snitchwatch-bridge-%s-x86_64.tar.gz' % json.load(open(sys.argv[1]))['bridgeVersion'])
PY
)"
version=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["bridgeVersion"])' "$pins")
# Without the ldd/run options this verify executes nothing (those ran in the
# bridge stage). The sha is self-computed from untrusted data: bridge integrity is M1.
mkdir -p "$work/assembly"
python3 -B "$work/source/packaging/release/bridge_artifact.py" verify --tarball "$tarball" \
    --expect-sha256 "$(sha256sum "$tarball" | awk '{print $1}')" --expect-version "$version" --extract-to "$work/assembly/verified"
python3 -B "$factory/snitchwatch-system-stage.py" \
    --source "$work/source" --artifact "$work/assembly/verified/$(basename "$tarball" .tar.gz)" --out "$out" \
    --scanner-artifact "$handoff/scanner" \
    --pins "$pins" --verifier "$factory/snitchwatch-system-verify.py" \
    --legacy-config /candidate/legacy-config.json --candidate-config /candidate/system-config.json \
    --build-log "$handoff/bridge/build.log" --os-update-rule "$factory/snitchwatch-system-os-update-rule.json" \
    --resolved-dns-rule "$factory/snitchwatch-system-resolved-dns-rule.json" \
    --repro-log "$handoff/bridge/repro.log" --rpm-inventory "$work/rpm-inventory.txt" --tarball "$tarball"
# Re-check after the uid 65534 runs inside stage.py, immediately before the merge reads it.
python3 "$guard" check daemon --daemon-verifier "$daemon_verifier" "$handoff/daemon"
python3 "$guard" merge "$handoff/daemon" "$out"
python3 -B "$daemon_verifier" --stage-only --root "$out"
python3 -B "$factory/snitchwatch-system-verify.py" --root "$out" --image-tree
