#!/usr/bin/env bash
# Opt-in host integration check. Requires root, active libvirt default NAT, and
# permission to restart Docker; it is intentionally not a required nested-CI test.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "run with sudo (this restarts docker.service)" >&2
    exit 77
fi
bridge=$(virsh net-dumpxml default | python3 -c '
import sys
import xml.etree.ElementTree as ET
root = ET.fromstring(sys.stdin.read())
forward = root.find("forward")
bridge = root.find("bridge")
if forward is None or forward.get("mode") != "nat" or bridge is None or not bridge.get("name"):
    raise SystemExit(1)
print(bridge.get("name"))
' 2>/dev/null || true)
if [[ -z "${bridge}" ]]; then
    echo "SKIP: active libvirt default NAT network is unavailable"
    exit 0
fi
address=$(ip -4 -o addr show dev "${bridge}" scope global | awk '$3 == "inet" { print $4; exit }')
cidr=$(python3 -c 'import ipaddress, sys; print(ipaddress.ip_interface(sys.argv[1]).network)' "${address}" 2>/dev/null || true)
uplink=$(ip -o route show default | awk '{ for (i = 1; i <= NF; i++) if ($i == "dev") { print $(i + 1); exit } }')
if [[ -z "${cidr}" || -z "${uplink}" ]]; then
    echo "SKIP: default bridge IPv4 CIDR or default-route interface unavailable"
    exit 0
fi

systemctl restart docker.service
comment_prefix=bazzite-tower-libvirt-forwarding
comment="${comment_prefix}:${bridge}:${cidr}:${uplink}"
iptables -w -C DOCKER-USER -i "${bridge}" -o "${uplink}" -s "${cidr}" \
    -m conntrack --ctstate NEW,ESTABLISHED,RELATED \
    -m comment --comment "${comment}" -j ACCEPT
iptables -w -C DOCKER-USER -i "${uplink}" -o "${bridge}" -d "${cidr}" \
    -m conntrack --ctstate RELATED,ESTABLISHED \
    -m comment --comment "${comment}" -j ACCEPT

probe_image=${DOCKER_FORWARDING_PROBE_IMAGE:-busybox:1.36.1}
docker run --rm --network bridge "${probe_image}" sh -ec '
    gateway=$(ip route | awk "/^default/ { print \$3; exit }")
    test -n "${gateway}"
    ping -c 1 -W 2 "${gateway}"
'
echo "docker/libvirt forwarding integration: pass (${bridge} via ${uplink})"
