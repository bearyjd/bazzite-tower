#!/usr/bin/env bash
set -euo pipefail
case "${SNITCHWATCH_BRIDGE:-legacy}" in
    legacy) ;;
    system)
        if [[ "${FIREWALL_DAEMON:-opensnitch}" != opensnitch ]]; then
            echo 'SNITCHWATCH_BRIDGE=system requires FIREWALL_DAEMON=opensnitch.' >&2
            exit 1
        fi
        ;;
    *) echo "Unknown SNITCHWATCH_BRIDGE='${SNITCHWATCH_BRIDGE}'." >&2; exit 1 ;;
esac
case "${FIREWALL_DAEMON:-opensnitch}" in
    opensnitch|portmaster) ;;
    *) echo "Unknown FIREWALL_DAEMON='${FIREWALL_DAEMON}'." >&2; exit 1 ;;
esac
