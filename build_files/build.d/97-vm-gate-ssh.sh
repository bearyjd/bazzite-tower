#!/usr/bin/env bash
set -euo pipefail

# ── VM-gate SSH (opt-in, never in :latest) ───────────────────────────────────
# Bazzite does not enable sshd by default, which made every interactive VM
# test (the Portmaster spike gate among them) depend on typing/pasting into a
# GUI console -- see docs/research/portmaster-bootc-spike.md's "VM-gate SSH"
# section for the friction that motivated this and why the alternatives
# (systemd-vmspawn --vsock + systemd-ssh-proxy) didn't pan out.
#
# Off unless VM_GATE_SSH=1 is passed as a build-arg. Even then, sshd.socket
# being enabled is not by itself reachable: the disk still has to be built
# from disk_config/vm-test.toml (not disk.toml, which ships and is what the
# ThinkPad boots) to actually have a user/key to log in with.
if [[ "${VM_GATE_SSH:-0}" == "1" ]]; then
    systemctl enable sshd.socket
fi
