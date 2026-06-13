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

# ── SOF audio firmware: pin to the kernel's topology ABI ──────────────────────
# The 7.0 kernel this image ships carries a SOF driver at topology ABI 3.23, but
# current alsa-sof-firmware ships topologies built at ABI 3.29. A topology newer
# than the kernel's ABI can't be instantiated ("FW reported error: 9" / "failed
# widget list set up"); WirePlumber then re-links the dead sink ~10x/s until
# PipeWire sets the card profile to off — i.e. no audio at all. So pin the
# firmware to the newest build whose topology ABI is <= the kernel's, and verify
# it: the build fails loudly rather than shipping silent-but-broken audio.
#
# KERNEL_SOF_ABI_*: the kernel's SOF topology ABI. Bump these — and raise
# SOF_FW_PIN to a newer firmware — when a future kernel moves its SOF ABI past
# 3.23 (confirm on a booted host with `journalctl -k | grep "Kernel ABI"`). That
# is the single edit needed to lift this pin.
KERNEL_SOF_ABI_MAJ=3
KERNEL_SOF_ABI_MIN=23
# SOF_FW_PIN — NEEDS CONFIRMATION against the live Fedora repos. It must be the
# newest alsa-sof-firmware whose shipped .tplg ABI is <= 3.23. Inspect with:
#     dnf --showduplicates list alsa-sof-firmware
# and verify a candidate by extracting its sof-tplg and running
# /usr/libexec/bazzite-tower-sof-abi on it. The ABI gate below is the backstop:
# a wrong pin (ABI too new, or a version that doesn't exist) fails the build.
SOF_FW_PIN="2024.09.2"

# Expected case: the base ships a newer build, so downgrade. Fall back to a plain
# install for the (unlikely) case the base is already older/equal or absent.
dnf downgrade -y "alsa-sof-firmware-${SOF_FW_PIN}" \
    || dnf install -y "alsa-sof-firmware-${SOF_FW_PIN}"
# Hold it for the rest of this build so no later transaction pulls it forward.
# Best-effort: the plugin may be absent on the base, and the runtime is immutable
# anyway (no `dnf upgrade` ever runs on the laptop), so never fail the build on it
# — the install order above plus the ABI gate below are the real guarantees.
dnf versionlock add alsa-sof-firmware 2>/dev/null \
    || echo "note: dnf versionlock unavailable; relying on install order + ABI gate"

# Authoritative gate: the shipped topology ABI must not exceed the kernel's, or
# audio is silently broken on the laptop. Read it offline from the .tplg files.
sof_abi="$(/usr/libexec/bazzite-tower-sof-abi /usr/lib/firmware/intel/sof-tplg)" || {
    echo "ERROR: could not read the shipped SOF topology ABI" >&2
    exit 1
}
read -r sof_maj sof_min _sof_patch <<<"${sof_abi}"
if (( sof_maj > KERNEL_SOF_ABI_MAJ \
      || (sof_maj == KERNEL_SOF_ABI_MAJ && sof_min > KERNEL_SOF_ABI_MIN) )); then
    echo "ERROR: shipped SOF topology ABI ${sof_maj}.${sof_min} exceeds kernel ABI ${KERNEL_SOF_ABI_MAJ}.${KERNEL_SOF_ABI_MIN}" >&2
    echo "       lower SOF_FW_PIN to an older alsa-sof-firmware build (see comment above)." >&2
    exit 1
fi
echo "SOF topology ABI ${sof_maj}.${sof_min} <= kernel ABI ${KERNEL_SOF_ABI_MAJ}.${KERNEL_SOF_ABI_MIN} (firmware ${SOF_FW_PIN}) — ok"

# ── Cleanup ───────────────────────────────────────────────────────────────────
dnf clean all
