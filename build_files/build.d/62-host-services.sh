#!/usr/bin/env bash
set -euo pipefail

# ── Host services that were only ever enabled in /etc ─────────────────────────
# Both units ship in the Bazzite base (no layering needed), but their enablement
# lived only as symlinks in /etc — so a rebase or fresh install came up without
# them. Baking the enable here makes the image reproduce the machine's actual
# intended state. See docs/RUNBOOK.md "/etc drift vs the image".
systemctl enable tailscaled.service
systemctl enable waydroid-container.service

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
# libvirtd.service — deliberately MASKED by 60-libvirt-services.sh in favour of
#   the modular virt*.socket daemons. An enable symlink for it exists in /etc on
#   at least one host; it is inert (the mask wins) and should be deleted there,
#   not promoted here.
