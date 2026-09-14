#!/usr/bin/env bash
set -euo pipefail

# ── Tailscale daemon (network identity remains unconfigured) ──────────────────
# The daemon itself has no baked node identity, auth key, serve/funnel setup, or
# public listener. Keep it available for an operator's later `tailscale up`, but
# leave every externally reachable management surface opt-in.
systemctl enable tailscaled.service

# Waydroid's Android runtime is potentially large and pulls initial state; make
# activation an explicit per-host choice even if an upstream preset changes.
systemctl disable waydroid-container.service || true

# ── Deliberately NOT enabled here ────────────────────────────────────────────
# sshd.service — Bazzite ships sshd off, and 97-vm-gate-ssh.sh keeps it that way
#   for :latest (sshd.socket only under VM_GATE_SSH=1). Enabling it image-wide
#   would change the security posture of every build. It stays a local /etc
#   setting on the one host that wants it; `ujust enable-ssh` re-applies it
#   after a rebase.
#
# plugin_loader.service — Decky Loader. Its unit runs as root with
#   ExecStart=/home/user/homebrew/services/PluginLoader, a path inside one user's
#   home. Host-specific by construction, and owned by Decky's own installer.
#   Never bake it into a shared image.
#
# docker.service/docker.socket, cockpit.socket, waydroid-container.service — all
#   are deliberately disabled. Their respective `ujust enable-*` recipes make
#   the host-local opt-in explicit. Cockpit's shipped socket drop-in limits it
#   to loopback even after it is enabled.
#
# libvirtd.service — deliberately MASKED by 60-libvirt-services.sh in favour of
#   the modular virt*.socket daemons. An enable symlink for it exists in /etc on
#   at least one host; it is inert (the mask wins) and should be deleted there,
#   not promoted here.
