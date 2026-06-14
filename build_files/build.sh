#!/usr/bin/env bash
# build.sh — runs inside the container image build
set -euo pipefail

# ── QEMU / libvirt / KVM stack ────────────────────────────────────────────────
# config-network/config-nwfilter ship the default NAT network and nwfilter rules.
dnf install -y \
    edk2-ovmf \
    guestfs-tools \
    libvirt \
    libvirt-client \
    libvirt-daemon-config-network \
    libvirt-daemon-config-nwfilter \
    libvirt-daemon-kvm \
    qemu-kvm \
    spice-gtk3 \
    virt-install \
    virt-manager \
    virt-viewer

# ── DX-equivalent dev tooling (Fedora repos) ──────────────────────────────────
dnf install -y \
    android-tools \
    ccache \
    flatpak-builder \
    podman-machine \
    podman-tui \
    rclone \
    restic \
    zsh

# ── Docker CE ─────────────────────────────────────────────────────────────────
# Mirror of https://download.docker.com/linux/fedora/docker-ce.repo with every
# section disabled. We flip docker-ce-stable on for the install transaction only
# via --enablerepo, so the repo file is inert at runtime (no background updates,
# no surprise upgrades). Bazzite ships podman-docker which owns /usr/bin/docker;
# remove it first so docker-ce can land cleanly.
install -Dm644 /dev/stdin /etc/yum.repos.d/docker-ce.repo <<'EOF'
[docker-ce-stable]
name=Docker CE Stable - $basearch
baseurl=https://download.docker.com/linux/fedora/$releasever/$basearch/stable
enabled=0
gpgcheck=1
gpgkey=https://download.docker.com/linux/fedora/gpg

[docker-ce-stable-debuginfo]
name=Docker CE Stable - Debuginfo $basearch
baseurl=https://download.docker.com/linux/fedora/$releasever/debug-$basearch/stable
enabled=0
gpgcheck=1
gpgkey=https://download.docker.com/linux/fedora/gpg

[docker-ce-stable-source]
name=Docker CE Stable - Sources
baseurl=https://download.docker.com/linux/fedora/$releasever/source/stable
enabled=0
gpgcheck=1
gpgkey=https://download.docker.com/linux/fedora/gpg

[docker-ce-test]
name=Docker CE Test - $basearch
baseurl=https://download.docker.com/linux/fedora/$releasever/$basearch/test
enabled=0
gpgcheck=1
gpgkey=https://download.docker.com/linux/fedora/gpg

[docker-ce-test-debuginfo]
name=Docker CE Test - Debuginfo $basearch
baseurl=https://download.docker.com/linux/fedora/$releasever/debug-$basearch/test
enabled=0
gpgcheck=1
gpgkey=https://download.docker.com/linux/fedora/gpg

[docker-ce-test-source]
name=Docker CE Test - Sources
baseurl=https://download.docker.com/linux/fedora/$releasever/source/test
enabled=0
gpgcheck=1
gpgkey=https://download.docker.com/linux/fedora/gpg

[docker-ce-nightly]
name=Docker CE Nightly - $basearch
baseurl=https://download.docker.com/linux/fedora/$releasever/$basearch/nightly
enabled=0
gpgcheck=1
gpgkey=https://download.docker.com/linux/fedora/gpg

[docker-ce-nightly-debuginfo]
name=Docker CE Nightly - Debuginfo $basearch
baseurl=https://download.docker.com/linux/fedora/$releasever/debug-$basearch/nightly
enabled=0
gpgcheck=1
gpgkey=https://download.docker.com/linux/fedora/gpg

[docker-ce-nightly-source]
name=Docker CE Nightly - Sources
baseurl=https://download.docker.com/linux/fedora/$releasever/source/nightly
enabled=0
gpgcheck=1
gpgkey=https://download.docker.com/linux/fedora/gpg
EOF

dnf remove -y podman-docker || true

dnf install -y --enablerepo=docker-ce-stable \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin

# ── System users from packaged sysusers.d snippets ────────────────────────────
# A plain `dnf install` in a container build does NOT run systemd-sysusers the
# way an rpm-ostree compose does, so packages that declare their accounts via
# /usr/lib/sysusers.d/*.conf — notably qemu — never get those users created.
# Without the 'qemu' user, virtqemud aborts at startup ("Failed to parse user
# 'qemu'") and the socket-activated service crash-loops into start-limit-hit on
# every boot, so qemu:///system never comes up.
#
# Complication seen on this base: it ships orphan `qemu:` lines in /etc/shadow
# and /etc/gshadow with no matching /etc/passwd or /etc/group entry. sysusers
# writes passwd+group+shadow+gshadow as one transaction and aborts — rolling the
# whole thing back — when it hits the pre-existing gshadow line, so it silently
# creates nothing. Strip those orphans first (no-op if absent), then materialize.
sed -i '/^qemu:/d' /etc/shadow /etc/gshadow
systemd-sysusers
# Belt-and-suspenders: if no sysusers.d snippet shipped the qemu user, create it
# directly. libvirt resolves it by name at runtime, so a dynamic system uid (-r)
# is fine. Guarded so a future packaging change can't break the build.
getent group  qemu >/dev/null || groupadd -r qemu
getent passwd qemu >/dev/null || useradd  -r -g qemu -d / -s /sbin/nologin -c "qemu user" qemu

# ── docker-in-docker: load iptable_nat at boot ────────────────────────────────
install -Dm644 /dev/stdin /etc/modules-load.d/iptable_nat.conf <<'EOF'
iptable_nat
EOF

# ── libvirt: modular daemons + Docker service ─────────────────────────────────
# Bazzite/F44+ ships modular libvirt: one socket-activated daemon per driver
# (virtqemud, virtnetworkd, ...) instead of the monolithic libvirtd. Mask the
# legacy libvirtd.service so it can't race the modular daemons — that race is the
# root cause of the broken stock `ujust setup-virtualization`.
systemctl mask libvirtd.service

# Enable the per-driver sockets. Each primary .socket carries Also= directives
# that pull in its matching -ro and -admin sockets, so the primaries are enough.
systemctl enable virtqemud.socket
systemctl enable virtnetworkd.socket
systemctl enable virtnodedevd.socket
systemctl enable virtnwfilterd.socket
systemctl enable virtstoraged.socket

# virtproxyd serves the legacy /run/libvirt/libvirt-sock path that older tooling
# expects, forwarding to the modular daemons. It is the modular replacement for
# libvirtd.socket: the two declare Conflicts= on the same socket path, so we
# enable only this one (libvirtd.socket would be inert anyway — its service is
# masked).
systemctl enable virtproxyd.socket

# Docker daemon starts at boot (Docker CE is baked in alongside Podman).
systemctl enable docker.service

# ── libvirt default NAT network: autostart on boot ────────────────────────────
# libvirt-daemon-config-network ships the default NAT network definition. Mark it
# autostart by creating the symlink `virsh net-autostart` would — the daemons
# aren't running at build time, so virsh itself can't be used. Idempotent and
# guarded on the definition existing.
if [[ -f /etc/libvirt/qemu/networks/default.xml ]]; then
    install -d /etc/libvirt/qemu/networks/autostart
    ln -sfn ../default.xml /etc/libvirt/qemu/networks/autostart/default.xml
fi

# ── Polkit: wheel → qemu:///system access (immediate, no logout required) ─────
install -Dm644 /dev/stdin /etc/polkit-1/rules.d/50-libvirt-wheel.rules <<'EOF'
polkit.addRule(function(action, subject) {
    if ((action.id == "org.libvirt.unix.manage" ||
         action.id == "org.libvirt.unix.monitor") &&
        subject.isInGroup("wheel")) {
        return polkit.Result.YES;
    }
});
EOF

# ── First-boot oneshot: add the first regular user to the virt + docker groups ─
# Polkit covers libvirt access for wheel users, but raw /dev/kvm, the docker
# socket, and tools that check `groups` membership need real group entries. The
# unit and its helper ship in system_files/; the unit retries every boot until a
# regular user exists, then drops a marker so it stops running.
systemctl enable bazzite-tower-firstboot.service

# ── Wi-Fi backend guard: don't let a stale wifi.backend=iwd strand Wi-Fi ───────
# /etc persists across a bootc rebase, so a `wifi.backend=iwd` file from a
# previous image can outlive its enabled iwd service — NetworkManager then points
# at a supplicant that never runs and every Wi-Fi device reports `unavailable`.
# This unit runs before NetworkManager each boot and forces the default
# wpa_supplicant backend whenever iwd is selected but not enabled (and backs off
# again the moment iwd is properly enabled). Ships in system_files/.
systemctl enable bazzite-tower-wifi-backend-guard.service

# ── Storage health monitoring (SMART) ─────────────────────────────────────────
# smartd runs scheduled self-tests and watches SMART health for both NVMe drives,
# logging warnings to the journal (no MTA on this image). Config ships in
# system_files/etc/smartmontools/smartd.conf.
dnf install -y smartmontools
systemctl enable smartd.service

# ── RAS / MCE handling ────────────────────────────────────────────────────────
# This box logs ~115 corrected CPU cache-error MCEs per boot on Meteor Lake. EDAC
# igen6 ECC counters read 0/0, so these are CPU cache, not DRAM. Two changes:
#   1. rasdaemon — the modern RAS collector. It records and decodes MCEs into a
#      local store queryable with `ras-mc-ctl --summary`/`--errors`.
#   2. mask mcelog — redundant with rasdaemon, and on this box its
#      cache-error-trigger tried to *offline a CPU* when it hit the corrected-error
#      threshold (it failed only because the trigger script was buggy). Masking
#      removes that footgun for good.
dnf install -y rasdaemon
systemctl enable rasdaemon.service
systemctl mask mcelog.service

# Latest Intel microcode. Meteor Lake has shipped corrected-cache-error microcode
# updates; this box was seen on revision 0x28. `dnf install` upgrades microcode_ctl
# to the newest in the Fedora repos when the base is older (no-op if already
# current). Note early-load takes effect once the initramfs is regenerated (on a
# base bump); verify the running revision per docs/RUNBOOK.md.
dnf install -y microcode_ctl

# ── SOF audio firmware: auto-resolve to the kernel's topology ABI ─────────────
# The 7.0 kernel this image ships carries a SOF driver at topology ABI 3.23, but
# current alsa-sof-firmware ships topologies built at ABI 3.29. A topology newer
# than the kernel's ABI can't be instantiated ("FW reported error: 9" / "failed
# widget list set up"); WirePlumber then re-links the dead sink ~10x/s until
# PipeWire sets the card profile to off — i.e. no audio at all.
#
# Rather than hard-code a pin that drifts, resolve it at build time: walk the
# available alsa-sof-firmware builds newest->oldest and pick the newest whose
# *actual shipped topology ABI* is <= the kernel's. "Verify, don't guess." The
# installed-ABI gate at the end is the backstop.
#
# KERNEL_SOF_ABI_*: the kernel's SOF topology ABI (journal: "... Kernel ABI 3:23:1").
# Bumping these is the ONLY edit needed when a future kernel advances its SOF ABI —
# the resolver then auto-selects a newer firmware. Confirm on a booted host with
# `journalctl -k | grep "Kernel ABI"`.
KERNEL_SOF_ABI_MAJ=3
KERNEL_SOF_ABI_MIN=23

# Tooling to inspect a candidate's .tplg without installing it: cpio to unpack a
# downloaded rpm, plus the `dnf download` plugin. That plugin's virtual provide is
# named differently on dnf5 (this base) vs dnf4, so try the known forms in order
# and succeed on the first that resolves. (repoquery is built into dnf5 and pulled
# in by dnf-plugins-core on dnf4.)
dnf install -y cpio
dnf install -y "dnf5-command(download)" \
    || dnf install -y "dnf-command(download)" \
    || dnf install -y dnf5-plugins \
    || dnf install -y dnf-plugins-core

# 0 if "<maj> <min> <patch>" is <= the kernel's SOF ABI.
sof_abi_le_kernel() {
    local maj min
    read -r maj min _ <<<"$1"
    (( maj < KERNEL_SOF_ABI_MAJ || (maj == KERNEL_SOF_ABI_MAJ && min <= KERNEL_SOF_ABI_MIN) ))
}

# Echo the newest alsa-sof-firmware version-release whose topology ABI <= kernel.
resolve_sof_firmware() {
    local evr workdir abi tplg evrs
    evrs="$(dnf -q repoquery --available --queryformat '%{version}-%{release}\n' \
            alsa-sof-firmware 2>/dev/null | sort -Vr | uniq || true)"
    [[ -n "${evrs}" ]] || { echo "ERROR: no alsa-sof-firmware builds in the repos" >&2; return 1; }
    for evr in ${evrs}; do
        workdir="$(mktemp -d)"
        if dnf -q download --destdir="${workdir}" "alsa-sof-firmware-${evr}" >/dev/null 2>&1; then
            ( cd "${workdir}" && rpm2cpio ./*.rpm | cpio -idm --quiet '*/sof-tplg/*' 2>/dev/null )
            tplg="${workdir}/usr/lib/firmware/intel/sof-tplg"
            if abi="$(/usr/libexec/bazzite-tower-sof-abi "${tplg}" 2>/dev/null)" \
               && sof_abi_le_kernel "${abi}"; then
                echo "candidate ${evr}: topology ABI ${abi// /.} <= ${KERNEL_SOF_ABI_MAJ}.${KERNEL_SOF_ABI_MIN} — selected" >&2
                echo "${evr}"
                rm -rf "${workdir}"
                return 0
            fi
            echo "candidate ${evr}: topology ABI ${abi:-?} too new — skipping" >&2
        else
            echo "candidate ${evr}: download failed — skipping" >&2
        fi
        rm -rf "${workdir}"
    done
    echo "ERROR: no alsa-sof-firmware build has topology ABI <= ${KERNEL_SOF_ABI_MAJ}.${KERNEL_SOF_ABI_MIN}" >&2
    return 1
}

echo "Resolving alsa-sof-firmware to SOF topology ABI <= ${KERNEL_SOF_ABI_MAJ}.${KERNEL_SOF_ABI_MIN} ..."
SOF_FW_EVR="$(resolve_sof_firmware)"
echo "Selected alsa-sof-firmware-${SOF_FW_EVR}"

# Install the resolved build (downgrade from the base's newer one) and hold it.
dnf downgrade -y "alsa-sof-firmware-${SOF_FW_EVR}" \
    || dnf install -y "alsa-sof-firmware-${SOF_FW_EVR}"
# Hold it so no later transaction pulls it forward. Best-effort: the runtime is
# immutable (no `dnf upgrade` runs on the laptop), so never fail the build on a
# missing plugin — install order + the gate below are the real guarantees.
dnf versionlock add alsa-sof-firmware 2>/dev/null \
    || echo "note: dnf versionlock unavailable; relying on install order + ABI gate"

# Authoritative gate: assert the *installed* topology ABI, in case install
# resolution did something unexpected. Audio is silently broken if this is wrong.
sof_abi="$(/usr/libexec/bazzite-tower-sof-abi /usr/lib/firmware/intel/sof-tplg)" || {
    echo "ERROR: could not read the installed SOF topology ABI" >&2
    exit 1
}
if ! sof_abi_le_kernel "${sof_abi}"; then
    echo "ERROR: installed SOF topology ABI ${sof_abi// /.} exceeds kernel ABI ${KERNEL_SOF_ABI_MAJ}.${KERNEL_SOF_ABI_MIN}" >&2
    exit 1
fi
echo "SOF topology ABI ${sof_abi// /.} <= kernel ABI ${KERNEL_SOF_ABI_MAJ}.${KERNEL_SOF_ABI_MIN} (alsa-sof-firmware-${SOF_FW_EVR}) — ok"

# ── Cleanup ───────────────────────────────────────────────────────────────────
dnf clean all
