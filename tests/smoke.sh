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
check "i915 display kargs.d fragment present" test -f /usr/lib/bootc/kargs.d/10-i915-display.toml
check "suspend kargs.d fragment present" test -f /usr/lib/bootc/kargs.d/20-suspend.toml
check "vfio/kvm kargs.d fragment present" test -f /usr/lib/bootc/kargs.d/30-vfio-kvm.toml
check "nvme kargs.d fragment present" test -f /usr/lib/bootc/kargs.d/40-nvme.toml
check_enabled "bazzite-tower-firstboot.service"
check "firstboot helper is executable" test -x /usr/libexec/bazzite-tower-firstboot

echo "== Storage health (SMART) =="
check "smartctl present"      command -v smartctl
check_enabled "smartd.service"
check "smartd.conf present"   test -f /etc/smartmontools/smartd.conf

echo "== CPU power tuning =="
check "thermald present"                command -v thermald
check_enabled "thermald.service"
check "power-tuning helper executable"  test -x /usr/libexec/bazzite-tower-power-tuning
check_enabled "bazzite-tower-power-tuning.service"

echo "== RAS / MCE =="
# rasdaemon replaces mcelog for MCE collection/decoding; mcelog is masked because
# its cache-error-trigger tried to offline a CPU on this Meteor Lake box.
check "rasdaemon present (ras-mc-ctl)" command -v ras-mc-ctl
check_enabled "rasdaemon.service"
check_masked  "mcelog.service"
check "microcode_ctl present" rpm -q microcode_ctl

echo "== Audio (SOF bypass) =="
# SOF/DSP is bypassed via snd_intel_dspcfg.dsp_driver=1 (legacy HDA): the kernel's
# SOF ABI (3.23) can't load stock firmware's ABI-3.29 topology, and no ABI-≤3.23
# alsa-sof-firmware exists in the repos to downgrade to. Assert the bypass karg and
# the WirePlumber backoff seatbelt (defense-in-depth if SOF is ever re-enabled).
check "SOF bypass kargs.d fragment present" test -f /usr/lib/bootc/kargs.d/25-audio-sof-bypass.toml
check "SOF bypass forces legacy HDA"        grep -q 'snd_intel_dspcfg.dsp_driver=1' /usr/lib/bootc/kargs.d/25-audio-sof-bypass.toml
check "WirePlumber SOF backoff drop-in present" test -f /usr/share/wireplumber/wireplumber.conf.d/90-tower-sof-backoff.conf

echo "== Defaults (swappiness / indexer) =="
check "swappiness sysctl present" test -f /usr/lib/sysctl.d/99-tower-swappiness.conf
check "swappiness set to 10"      grep -qE '^vm\.swappiness[[:space:]]*=[[:space:]]*10$' /usr/lib/sysctl.d/99-tower-swappiness.conf
check "baloo exclude config present" test -f /etc/xdg/baloofilerc

echo "== GPU module blacklist =="
# No AMD GPU exists on this hardware; amdgpu/amdxcp are blacklisted as a lean-boot
# optimization. xe is intentionally left loaded.
check "unused-GPU blacklist present" test -f /usr/lib/modprobe.d/blacklist-unused-gpu.conf
check "amdgpu blacklisted" grep -qx 'blacklist amdgpu' /usr/lib/modprobe.d/blacklist-unused-gpu.conf

echo "== Docker CE =="
check "docker present"     command -v docker
check "containerd present" command -v containerd
# The 'docker' group must be baked into the image: docker.socket resolves it at
# early boot, and if it's only created late at runtime the socket fails every boot.
check "docker group exists (getent group docker)" getent group docker
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
