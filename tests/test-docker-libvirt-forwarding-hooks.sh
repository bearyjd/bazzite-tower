#!/usr/bin/env bash
# Static contracts for the Docker/libvirt forwarding triggers: a systemd path
# unit (not a libvirt hook, which virtnetworkd_t cannot exec under SELinux),
# the NetworkManager dispatcher, and the Docker drop-in.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
sys="${repo_root}/system_files"
path_unit="${sys}/usr/lib/systemd/system/docker-libvirt-forwarding.path"
service="${sys}/usr/lib/systemd/system/docker-libvirt-forwarding.service"
dispatcher="${sys}/etc/NetworkManager/dispatcher.d/90-docker-libvirt-forwarding"
dropin="${sys}/etc/systemd/system/docker.service.d/libvirt-forwarding.conf"
services_script="${repo_root}/build_files/build.d/60-libvirt-services.sh"

bash -n "${dispatcher}"
[[ ! -e "${sys}/etc/libvirt/hooks/network" ]]
grep -qx 'PathChanged=/run/libvirt/network' "${path_unit}"
grep -qx 'Unit=docker-libvirt-forwarding.service' "${path_unit}"
grep -qx 'WantedBy=multi-user.target' "${path_unit}"
grep -qx 'Type=oneshot' "${service}"
grep -qx 'StartLimitIntervalSec=0' "${service}"
grep -qx 'ExecCondition=/usr/bin/systemctl is-active --quiet docker.service' "${service}"
grep -qx 'ExecStart=/usr/local/libexec/docker-libvirt-forwarding' "${service}"
grep -qx 'systemctl enable docker-libvirt-forwarding.path' "${services_script}"
grep -qx 'ExecStartPost=-/usr/local/libexec/docker-libvirt-forwarding' "${dropin}"
grep -Eq 'up\|down\|dhcp4-change\|dhcp6-change\|connectivity-change\|vpn-up\|vpn-down\|reapply' "${dispatcher}"
grep -qF 'systemctl is-active --quiet docker.service' "${dispatcher}"
grep -qF '/usr/local/libexec/docker-libvirt-forwarding >/dev/null 2>&1 &' "${dispatcher}"
grep -qx 'exit 0' "${dispatcher}"
echo "docker/libvirt forwarding trigger contracts: pass"
