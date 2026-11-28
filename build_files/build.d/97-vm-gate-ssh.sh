#!/usr/bin/env bash
set -euo pipefail

# ── VM-gate SSH (opt-in, never in :latest) ───────────────────────────────────
# Bazzite does not enable sshd by default, which made every interactive VM
# test (e.g. the snitchwatch-system VM gate) depend on typing/pasting into a
# GUI console -- see the `run-vm-ssh` recipe comment in the Justfile for why
# SSH over a qemu hostfwd is used instead of systemd-vmspawn --vsock.
#
# Off unless VM_GATE_SSH=1 is passed as a build-arg. Even then, sshd.socket
# being enabled is not by itself reachable: the disk still has to be built
# from disk_config/vm-test.toml (not disk.toml, which ships and is what the
# ThinkPad boots) to actually have a user/key to log in with.
if [[ "${VM_GATE_SSH:-0}" == "1" ]]; then
    systemctl enable sshd.socket
fi
