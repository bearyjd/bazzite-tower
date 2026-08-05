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
# Looking Glass: kvmfr host module is base-provided; the version-coupled client is
# installed on demand via this ujust recipe (not baked). Assert the recipe shipped.
check "looking-glass-client ujust recipe present" \
    grep -q 'install-looking-glass-client' /usr/share/ublue-os/just/60-custom.just

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

echo "== i915 resume-regression watcher =="
# Machine-checkable signal for the comment in Containerfile pinning the base to
# 6.19.x-ogc: the watcher must exist and be enabled so a future kernel bump
# past 7.0 gets flagged instead of silently trusted.
check_enabled "i915-resume-fix-check.timer"
check "i915-resume-fix-check helper is executable" test -x /usr/libexec/i915-resume-fix-check

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
check "journald size cap present" test -f /usr/lib/systemd/journald.conf.d/90-tower-journal-cap.conf
check "journald cap is 500M" grep -qE '^SystemMaxUse=500M$' /usr/lib/systemd/journald.conf.d/90-tower-journal-cap.conf

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

echo "== OpenSnitch (application firewall) =="
firewall_daemon="$(cat /usr/share/bazzite-tower/firewall-daemon 2>/dev/null || true)"
case "${firewall_daemon}" in
opensnitch)
# Extracted (not rpm/dnf-installed — see build.sh) from a pinned, sha256-verified
# upstream release RPM (not Fedora/RPM Fusion). Not in the rpm database by design,
# so check the binary directly rather than `rpm -q`. Daemon only; default policy
# fails open without a GUI.
check "opensnitch binary present" test -x /usr/bin/opensnitchd
check_enabled "opensnitch.service"
# `test -x` only proves the file exists — it cannot catch a missing shared
# library, and libnetfilter_queue.so.1 (a hard DT_NEEDED of opensnitchd) is NOT
# in the base image, so the extraction must install it explicitly. Without this
# check a missing lib ships green and the daemon fails to exec on every boot.
# ldd must BOTH exit 0 (a missing binary makes it fail, and its "No such file"
# message contains no "not found" for the grep to catch) AND report no
# unresolved library — checking only the grep passes when the binary is absent.
check "opensnitch dynamic libs all resolve" \
    bash -c 'ldd /usr/bin/opensnitchd >/dev/null 2>&1 && ! ldd /usr/bin/opensnitchd 2>&1 | grep -q "not found"'
# The real proof: the binary execs (every library resolved, loader satisfied) and
# is the version build.sh pinned. Replacing `rpm -q opensnitch` with `test -x`
# for the rpm2cpio extraction dropped the only assertion on the installed
# version; this restores it. Deliberately duplicates the pin in build.sh — a
# version bump must update both, and smoke.sh exists to encode that intent.
check "opensnitchd execs and reports pinned version 1.8.0" \
    bash -c '/usr/bin/opensnitchd -version 2>/dev/null | grep -qx "1.8.0"'
check "opensnitch default-config present" test -f /etc/opensnitchd/default-config.json
# Pristine image-intent copy, for diffing against a locally-edited /etc.
check "opensnitch staged config present" \
    test -f /usr/share/bazzite-tower/opensnitchd-default-config.json
# The one assertion that cannot pass by coincidence. The RPM extraction writes
# its own default-config.json to this path and build.sh installs ours over it —
# but DefaultAction=allow below matches the RPM's shipped default, so a silently
# skipped install would still satisfy that value check. Only a byte comparison
# proves the overwrite actually happened.
check "staged config actually overwrote the RPM's" \
    cmp -s /usr/share/bazzite-tower/opensnitchd-default-config.json \
        /etc/opensnitchd/default-config.json
# Structural validity: the value checks below read individual keys, so a trailing
# comma or unbalanced brace would leave them passing while opensnitchd fails to
# parse the file at startup.
check "opensnitch config is valid JSON" \
    jq -e . /etc/opensnitchd/default-config.json
# Values parsed, not grepped — string matching couples these to the file's
# whitespace and cannot distinguish a key from a substring elsewhere.
check "opensnitch DefaultAction is allow (fail-open headless)" \
    jq -e '.DefaultAction == "allow"' /etc/opensnitchd/default-config.json
# eBPF module fails to load on this image's kernel (snitchwatch#6) — "proc" is
# mandatory here. Flipping this back to "ebpf" needs a newer opensnitch release.
check "opensnitch ProcMonitorMethod is proc (eBPF broken on this kernel)" \
    jq -e '.ProcMonitorMethod == "proc"' /etc/opensnitchd/default-config.json
# Points at the Snitchwatch bridge's gRPC listener, not opensnitch-ui's socket.
check "opensnitch Server.Address is the Snitchwatch bridge" \
    jq -e '.Server.Address == "127.0.0.1:50051"' /etc/opensnitchd/default-config.json
# The GUI is Snitchwatch; upstream's opensnitch-ui conflicts with it.
check "opensnitch-ui NOT installed (conflicts with Snitchwatch)" \
    bash -c '! test -e /usr/bin/opensnitch-ui'
check_masked "portmaster.service"
;;
portmaster)
echo "== Portmaster (disabled VM spike) =="
check "portmaster binary present" test -x /usr/libexec/portmaster/portmaster-core
check "portmaster reports pinned version 2.2.1" \
    bash -c '/usr/libexec/portmaster/portmaster-core version 2>/dev/null | grep -q "Portmaster 2.2.1"'
check "portmaster seed helper executable" test -x /usr/libexec/bazzite-tower-portmaster-seed
check "portmaster seed defaults valid JSON" jq -e . /usr/share/bazzite-tower/portmaster-config.default.json
check "portmaster automatic binary updates disabled in image defaults" \
    jq -e '.core.automaticUpdates == false' /usr/share/bazzite-tower/portmaster-config.default.json
check "portmaster automatic intel updates disabled in image defaults" \
    jq -e '.core.automaticIntelUpdates == false' /usr/share/bazzite-tower/portmaster-config.default.json
# shellcheck disable=SC2016 # The inner shell, not this script, expands $().
check "portmaster is disabled by default" \
    bash -c '[[ "$(systemctl is-enabled portmaster.service 2>/dev/null)" == "disabled" ]]'
check_masked "opensnitch.service"
check "opensnitch binary absent from portmaster spike" bash -c '! test -e /usr/bin/opensnitchd'
;;
*)
bad "known firewall selector (got '${firewall_daemon:-missing}')"
;;
esac

echo "== Cockpit (web management) =="
# The compose can retain Cockpit Machines' files while omitting its RPM database
# record, so test the UI manifest the image actually serves rather than rpm -q.
check "Cockpit Machines UI assets present" test -f /usr/share/cockpit/machines/manifest.json
check_enabled "cockpit.socket"

echo
if [[ "${fail}" -ne 0 ]]; then
    echo "SMOKE TESTS FAILED"
    exit 1
fi
echo "All smoke checks passed."
# Explicit, mirroring the failure path above. Falling off the end means the same
# 0 to CI, but `just smoke` is invoked here through `distrobox-host-exec podman`,
# and that wrapper does not return when bash reaches EOF without an explicit
# exit -- so the *passing* run appears to hang while every failing run returns
# instantly. tests/boot-check.sh already ends this way.
exit 0
