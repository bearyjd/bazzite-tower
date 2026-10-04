#!/usr/bin/env bash
# Static safety contract for the image-resident OpenSnitch readiness preflight.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
script="${repo_root}/system_files/usr/libexec/bazzite-tower-opensnitch-readiness"

bash -n "${script}"
grep -qx 'set -euo pipefail' "${script}"
grep -Fqx 'config=/etc/opensnitchd/default-config.json' "${script}"
grep -Fqx 'bridge_address=127.0.0.1:50051' "${script}"
grep -Fq 'systemctl is-active --quiet opensnitch.service' "${script}"
grep -Fqx 'bridge_unit=snitchwatch-bridge.service' "${script}"
grep -Fqx "bridge_executable=\"\${HOME}/.local/bin/snitchwatch-bridge-cli\"" "${script}"
grep -Fqx "bridge_manifest=\"\${HOME}/.local/share/snitchwatch/bridge.sha256\"" "${script}"
grep -Fq "bridge_enabled=\$(systemctl --user is-enabled \"\${bridge_unit}\" 2>/dev/null || true)" "${script}"
grep -Fq "[[ \"\${bridge_enabled}\" == \"enabled\" ]]" "${script}"
grep -Fq "systemctl --user is-active --quiet \"\${bridge_unit}\"" "${script}"
grep -Fq "systemctl --user show --property=MainPID --value \"\${bridge_unit}\"" "${script}"
grep -Fq "sha256sum -- \"\${bridge_executable}\"" "${script}"
grep -Fq "bridge executable hash does not match \${bridge_manifest}" "${script}"
grep -Fq "readlink -f -- \"/proc/\${bridge_pid}/exe\"" "${script}"
grep -Fq '.Server.Address == "127.0.0.1:50051"' "${script}"
grep -Fq '.DefaultAction == "allow"' "${script}"
grep -Fq "ss -H -ltnp 'sport = :50051'" "${script}"
grep -Fq "index(\$0, \"pid=\" pid \",\")" "${script}"
if grep -Ev '^[[:space:]]*#' "${script}" | grep -Eq 'systemctl[[:space:]]+(enable|disable|start|stop|restart|reload)'; then
    echo "opensnitch readiness must not change service state" >&2
    exit 1
fi
if grep -Ev '^[[:space:]]*#' "${script}" | grep -Eq '(^|[[:space:]])sudo([[:space:]]|$)'; then
    echo "opensnitch readiness must not require sudo" >&2
    exit 1
fi
if grep -q 'SNITCHWATCH_BRIDGE_' "${script}"; then
    echo "opensnitch readiness must not accept a bridge environment override" >&2
    exit 1
fi
echo "opensnitch readiness is read-only and fail-open: pass"
