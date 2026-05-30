#!/usr/bin/env bash
# build.sh — runs inside the container image build
set -euo pipefail

# ── QEMU / libvirt / KVM stack ────────────────────────────────────────────────
dnf install -y \
    qemu-kvm \
    libvirt \
    libvirt-daemon-kvm \
    libvirt-client \
    virt-manager \
    virt-install \
    virt-viewer \
    edk2-ovmf \
    guestfs-tools \
    spice-gtk3

# ── DX-equivalent dev tooling (Fedora repos) ──────────────────────────────────
dnf install -y \
    android-tools \
    flatpak-builder \
    restic \
    rclone \
    zsh \
    ccache \
    podman-machine \
    podman-tui

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

# ── docker-in-docker: load iptable_nat at boot ────────────────────────────────
install -Dm644 /dev/stdin /etc/modules-load.d/docker.conf <<'EOF'
iptable_nat
EOF

# ── Fix libvirt service setup ─────────────────────────────────────────────────
# Mask the legacy monolithic daemon — it conflicts with the modern modular ones
# (this is the root cause of the broken ujust setup-virtualization)
systemctl mask libvirtd.service

# Enable modern modular libvirt daemons — virt works on first boot, no manual steps
systemctl enable virtqemud.socket
systemctl enable virtqemud-ro.socket
systemctl enable virtqemud-admin.socket
systemctl enable virtnetworkd.socket
systemctl enable virtstoraged.socket
systemctl enable virtnodedevd.socket

# Legacy libvirtd.socket: enabled so tools probing the legacy socket path see it.
# libvirtd.service stays masked so the modular daemons own the runtime socket;
# libvirtd.socket's shipped Conflicts= keeps it from racing virtqemud.socket.
systemctl enable libvirtd.socket

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

# ── First-boot oneshot: add the first regular user to libvirt and kvm groups ──
# Polkit covers libvirt access for wheel users, but raw /dev/kvm and tools that
# check `groups` membership need real group entries. The unit retries every
# boot until a regular user exists, then writes a marker so it stops running.
install -Dm755 /dev/stdin /usr/libexec/bazzite-tower-add-user-to-virt-groups <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
marker=/var/lib/bazzite-tower/virt-groups-applied
user=$(awk -F: '$3>=1000 && $3<65534 {print $1; exit}' /etc/passwd || true)
[[ -z "$user" ]] && exit 0
/usr/sbin/usermod -aG libvirt,kvm "$user"
install -d /var/lib/bazzite-tower
touch "$marker"
EOF

install -Dm644 /dev/stdin /usr/lib/systemd/system/bazzite-tower-add-user-to-virt-groups.service <<'EOF'
[Unit]
Description=Add first regular user to libvirt and kvm groups
ConditionPathExists=!/var/lib/bazzite-tower/virt-groups-applied

[Service]
Type=oneshot
ExecStart=/usr/libexec/bazzite-tower-add-user-to-virt-groups
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl enable bazzite-tower-add-user-to-virt-groups.service

# ── Cleanup ───────────────────────────────────────────────────────────────────
dnf clean all
