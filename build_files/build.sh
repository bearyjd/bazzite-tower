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
