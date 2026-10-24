#!/usr/bin/env bash
# Static contract for the image-resident reporting-only diagnostic.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
script="${repo_root}/system_files/usr/libexec/bazzite-tower-health"

bash -n "${script}"
grep -qx 'set +e' "${script}"
grep -q 'systemctl is-enabled' "${script}"
grep -q 'systemctl is-active' "${script}"
grep -q 'fwupdmgr --version' "${script}"
grep -q 'mokutil --sb-state' "${script}"
if grep -Ev '^[[:space:]]*#' "${script}" | grep -Eq '(^|[[:space:]])sudo([[:space:]]|$)'; then
    echo "tower-health must not require sudo" >&2
    exit 1
fi
if grep -Ev '^[[:space:]]*#' "${script}" | grep -Eq 'systemctl[[:space:]]+(enable|disable|start|stop|restart|reload)'; then
    echo "tower-health must not change service state" >&2
    exit 1
fi
python3 "${repo_root}/tests/test-tower-health.py"
echo "tower-health reporting and targeted repair fixtures: pass"
