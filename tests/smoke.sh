#!/usr/bin/env bash
# tests/smoke.sh — offline assertions that bazzite-tower's customizations
# survived the build, run against the built image with no VM:
#
#   podman run --rm -i <image> bash -s < tests/smoke.sh
#
# Every check encodes an *intent* from build_files/build.sh or system_files/, so
# the build going green but an upstream change quietly undoing one of our changes
# (a renamed qemu user, a vanished virt*.socket, a disabled guard) fails loudly
# here instead of silently on the laptop. Runs all checks and reports every
# failure, not just the first.
#
# Everything here must be answerable from the image filesystem alone — no running
# systemd, no network. `systemctl is-enabled`/`is-active`-at-runtime behaviour is
# left to the boot test; here we only read on-disk enablement (the symlinks
# `systemctl enable` wrote at build time), which is readable offline.
set -uo pipefail

fail=0
pass() { printf '  ok   %s\n' "$1"; }
bad()  { printf '  FAIL %s\n' "$1"; fail=1; }

# check "<description>" <command...>  — passes if the command exits 0.
check() {
    local desc="$1"; shift
    if "$@" >/dev/null 2>&1; then pass "${desc}"; else bad "${desc}"; fi
}

# enablement state of a unit, read from disk (no running systemd needed).
unit_state() { systemctl is-enabled "$1" 2>/dev/null || true; }

check_enabled() {
    local unit="$1" state
    state="$(unit_state "${unit}")"
    case "${state}" in
        enabled|enabled-runtime|static|alias|indirect|generated) pass "enabled: ${unit} (${state})" ;;
        *) bad "enabled: ${unit} (got '${state:-missing}')" ;;
    esac
}

check_masked() {
    local unit="$1" state
    state="$(unit_state "${unit}")"
    if [[ "${state}" == "masked" || "${state}" == "masked-runtime" ]]; then
        pass "masked: ${unit}"
    else
        bad "masked: ${unit} (got '${state:-missing}')"
    fi
}

echo "== QEMU / libvirt =="
# The sysusers / orphan-shadow dance in build.sh must leave a resolvable qemu
# user+group, or virtqemud aborts at startup ("Failed to parse user 'qemu'").
check "qemu user resolvable (getent passwd qemu)" getent passwd qemu
check "qemu group resolvable (getent group qemu)" getent group qemu
check "id qemu succeeds"                          id qemu
# Modular libvirt: each per-driver socket we enabled in build.sh.
for s in virtqemud virtnetworkd virtnodedevd virtnwfilterd virtstoraged virtproxyd; do
    check_enabled "${s}.socket"
done
# Legacy monolithic daemon must stay masked so it can't race the modular ones.
check_masked "libvirtd.service"
# Default NAT network marked autostart (the symlink build.sh creates by hand).
check "default network autostart symlink" test -L /etc/libvirt/qemu/networks/autostart/default.xml
# Polkit rule granting wheel access to qemu:///system.
check "libvirt-wheel polkit rule present" test -f /etc/polkit-1/rules.d/50-libvirt-wheel.rules
# The tooling we install must actually be on PATH.
check "qemu-system-x86_64 present" command -v qemu-system-x86_64
check "virsh present"              command -v virsh
check "virt-install present"       command -v virt-install

echo "== Wi-Fi backend guard =="
check_enabled "bazzite-tower-wifi-backend-guard.service"
check "guard helper is executable" test -x /usr/libexec/bazzite-tower-wifi-backend-guard
# The guard falls back to the wpa_supplicant backend, so it must exist.
check "wpa_supplicant present" command -v wpa_supplicant

echo "== Boot args / first-boot =="
check "IOMMU kargs.d fragment present" test -f /usr/lib/bootc/kargs.d/00-iommu.toml
check_enabled "bazzite-tower-firstboot.service"
check "firstboot helper is executable" test -x /usr/libexec/bazzite-tower-firstboot

echo "== Docker CE =="
check "docker present"     command -v docker
check "containerd present" command -v containerd
# Docker daemon set to start at boot.
check_enabled "docker.service"
# iptable_nat is loaded at boot for docker-in-docker.
check "iptable_nat modules-load.d present" test -f /etc/modules-load.d/iptable_nat.conf

echo
if [[ "${fail}" -ne 0 ]]; then
    echo "SMOKE TESTS FAILED"
    exit 1
fi
echo "All smoke checks passed."
