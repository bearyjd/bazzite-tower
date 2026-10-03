#!/usr/bin/env bash
# Static contracts for the Docker/libvirt forwarding triggers: a systemd path
# unit (not a libvirt hook, which virtnetworkd_t cannot exec under SELinux),
# a self-heal timer, the NetworkManager dispatcher, and the Docker drop-in.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
sys="${repo_root}/system_files"
path_unit="${sys}/usr/lib/systemd/system/docker-libvirt-forwarding.path"
service="${sys}/usr/lib/systemd/system/docker-libvirt-forwarding.service"
timer="${sys}/usr/lib/systemd/system/docker-libvirt-forwarding.timer"
dispatcher="${sys}/etc/NetworkManager/dispatcher.d/90-docker-libvirt-forwarding"
dropin="${sys}/etc/systemd/system/docker.service.d/libvirt-forwarding.conf"
services_script="${repo_root}/build_files/build.d/60-libvirt-services.sh"
helper_path=/usr/local/libexec/docker-libvirt-forwarding

# expect <file> <grep-mode> <pattern> <message>: fail with a distinct message.
expect() {
    local file="$1" mode="$2" pattern="$3" msg="$4"
    grep "${mode}" -- "${pattern}" "${file}" >/dev/null 2>&1 || { echo "FAIL: ${msg}" >&2; exit 1; }
}
# expect_count <file> <exact-line> <n> <message>
expect_count() {
    local n
    n=$(grep -cxF -- "$2" "$1" || true)
    [[ "${n}" -eq "$3" ]] || { echo "FAIL: $4 (found ${n}, want $3)" >&2; exit 1; }
}

bash -n "${dispatcher}" || { echo "FAIL: dispatcher has a syntax error" >&2; exit 1; }
[[ ! -e "${sys}/etc/libvirt/hooks/network" ]] || { echo "FAIL: libvirt network hook must not ship" >&2; exit 1; }

expect "${path_unit}" -qx 'PathChanged=/run/libvirt/network' "path unit must watch /run/libvirt/network"
expect "${path_unit}" -qx 'Unit=docker-libvirt-forwarding.service' "path unit must activate the service"
expect "${path_unit}" -qx 'TriggerLimitIntervalSec=10s' "path unit needs TriggerLimitIntervalSec=10s"
expect "${path_unit}" -qx 'TriggerLimitBurst=1000' "path unit needs TriggerLimitBurst=1000"
expect "${path_unit}" -qx 'WantedBy=multi-user.target' "path unit must be WantedBy multi-user.target"

expect "${service}" -qx 'Type=oneshot' "service must be Type=oneshot"
expect "${service}" -qx 'StartLimitIntervalSec=0' "service must disable the start rate limit"
expect "${service}" -qx 'ExecCondition=/usr/bin/systemctl is-active --quiet docker.service' "service must be conditioned on docker.service"
expect "${service}" -qx 'ExecCondition=/usr/bin/systemctl is-active --quiet virtnetworkd.service' "service must be conditioned on virtnetworkd.service"
expect "${service}" -qx 'ExecStartPre=/usr/bin/sleep 1' "service must settle with ExecStartPre sleep 1"
expect "${service}" -qx "ExecStart=${helper_path}" "service ExecStart must be the helper"
expect "${service}" -qx 'ExecStartPost=/usr/bin/sleep 2' "service needs ExecStartPost sleep 2"
expect "${service}" -qx "ExecStartPost=${helper_path}" "service needs a second-pass helper ExecStartPost"
expect "${service}" -qx 'UnsetEnvironment=VIRSH_BIN IP_BIN IPTABLES_BIN PYTHON_BIN DOCKER_LIBVIRT_FORWARDING_LOCK' "service must UnsetEnvironment the test overrides"

expect "${timer}" -qx 'OnBootSec=2min' "timer needs OnBootSec=2min"
expect "${timer}" -qx 'OnUnitInactiveSec=10min' "timer needs OnUnitInactiveSec=10min"
expect "${timer}" -qx 'Unit=docker-libvirt-forwarding.service' "timer must activate the service"
expect "${timer}" -qx 'WantedBy=timers.target' "timer must be WantedBy timers.target"

expect_count "${services_script}" 'systemctl enable docker-libvirt-forwarding.path' 1 "60-libvirt-services.sh must enable the path unit"
expect_count "${services_script}" 'systemctl enable docker-libvirt-forwarding.timer' 1 "60-libvirt-services.sh must enable the timer"

expect "${dropin}" -qx "ExecStartPost=-${helper_path}" "Docker drop-in must still run the helper"
expect "${dispatcher}" -Eq 'up\|down\|dhcp4-change\|dhcp6-change\|connectivity-change\|vpn-up\|vpn-down\|reapply' "dispatcher must cover route/VPN/reapply events"
expect "${dispatcher}" -qF 'systemctl is-active --quiet docker.service' "dispatcher must check docker.service"
expect "${dispatcher}" -qF "${helper_path} >/dev/null 2>&1 &" "dispatcher must run the helper asynchronously"
expect "${dispatcher}" -qx 'exit 0' "dispatcher must always exit 0"
echo "docker/libvirt forwarding trigger contracts: pass"
