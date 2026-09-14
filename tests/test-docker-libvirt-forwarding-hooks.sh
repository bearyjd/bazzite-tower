#!/usr/bin/env bash
# Static contracts for lifecycle hooks: they must be asynchronous and harmless.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
network_hook="${repo_root}/system_files/etc/libvirt/hooks/network"
dispatcher="${repo_root}/system_files/etc/NetworkManager/dispatcher.d/90-docker-libvirt-forwarding"

bash -n "${network_hook}" "${dispatcher}"
grep -Eq 'started\|stopped\|updated' "${network_hook}"
grep -qF '/usr/local/libexec/docker-libvirt-forwarding >/dev/null 2>&1 &' "${network_hook}"
grep -qx 'exit 0' "${network_hook}"
grep -Eq 'up\|down\|dhcp4-change\|dhcp6-change\|connectivity-change' "${dispatcher}"
grep -qF 'systemctl is-active --quiet docker.service' "${dispatcher}"
grep -qF '/usr/local/libexec/docker-libvirt-forwarding >/dev/null 2>&1 &' "${dispatcher}"
grep -qx 'exit 0' "${dispatcher}"
echo "docker/libvirt forwarding hook contracts: pass"
