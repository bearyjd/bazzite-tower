#!/usr/bin/env bash
# build.sh — runs inside the container image build
set -euo pipefail

# ── QEMU / libvirt / KVM stack ────────────────────────────────────────────────
dnf install -y \
    qemu-kvm \
    libvirt \
    libvirt-daemon-kvm \
    libvirt-client \
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

# ── Cleanup ───────────────────────────────────────────────────────────────────
dnf clean all
