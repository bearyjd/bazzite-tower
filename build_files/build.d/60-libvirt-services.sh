#!/usr/bin/env bash
set -euo pipefail

# ── libvirt: modular daemons ──────────────────────────────────────────────────
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

# Docker CE is present but its daemon and socket remain a host-local opt-in.
# Explicitly remove a package/base preset if one exists.
systemctl disable docker.service docker.socket || true

# ── libvirt default NAT network: autostart on boot ────────────────────────────
# libvirt-daemon-config-network ships the default NAT network definition. Mark it
# autostart by creating the symlink `virsh net-autostart` would — the daemons
# aren't running at build time, so virsh itself can't be used. Idempotent and
# guarded on the definition existing.
if [[ -f /etc/libvirt/qemu/networks/default.xml ]]; then
    install -d /etc/libvirt/qemu/networks/autostart
    ln -sfn ../default.xml /etc/libvirt/qemu/networks/autostart/default.xml
fi

# ── Polkit: libvirt manage requires authentication ────────────────────────────
# Deliberately nothing is written here. system_files/usr/share/polkit-1/rules.d/
# 49-bazzite-tower-libvirt-manage.rules makes org.libvirt.unix.manage (read-write
# qemu:///system) ask for an admin password; org.libvirt.unix.monitor stays at its
# passwordless default. A former /etc rule granted wheel manage+monitor = YES.

# ── First-boot oneshot: add the first regular user to virtualisation groups ────
# Raw /dev/kvm and tools that check `groups` membership need real kvm/libvirt
# entries (libvirt manage itself now prompts for a password, see above). Docker access is an
# explicit opt-in because its group is root-equivalent. The unit and its helper
# ship in system_files/; the unit retries every boot until a regular user exists,
# then drops a marker so it stops running.
systemctl enable bazzite-tower-firstboot.service

# ── Docker/libvirt NAT forwarding: path-activated reconciliation ──────────────
# Re-run the forwarding helper whenever libvirt starts/stops a network. This is
# a systemd path unit, not a libvirt network hook: libvirt's confined
# virtnetworkd_t cannot exec hooks under enforcing SELinux, and a failing hook
# aborts every network start.
systemctl enable docker-libvirt-forwarding.path
# Self-heal: reconcile periodically in case an edge event was missed or the
# path unit stopped watching.
systemctl enable docker-libvirt-forwarding.timer
