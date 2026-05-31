# bazzite-tower custom recipes — VM / libvirt management
#
# libvirt runs in modular mode on this image: per-driver daemons (virtqemud,
# virtnetworkd, ...) are socket-activated and enabled at boot, so the stack is
# normally already up. The monolithic libvirtd is masked. These recipes drive
# the modular daemons rather than libvirtd.

# Start the libvirt virtualization stack (modular daemons)
[group('Virtualization')]
vm-start:
    sudo systemctl start virtqemud.socket virtnetworkd.socket virtnodedevd.socket virtnwfilterd.socket virtstoraged.socket virtproxyd.socket

# Stop the libvirt virtualization stack (modular daemons)
[group('Virtualization')]
vm-stop:
    sudo systemctl stop virtqemud.socket virtnetworkd.socket virtnodedevd.socket virtnwfilterd.socket virtstoraged.socket virtproxyd.socket

# List all VMs known to the system libvirt instance
[group('Virtualization')]
vm-list:
    virsh -c qemu:///system list --all

# Show the status of all libvirt virtual networks
[group('Virtualization')]
vm-net-status:
    virsh -c qemu:///system net-list --all

# Add the current user to the kvm, libvirt, and docker groups
[group('Virtualization')]
fix-vm-groups:
    #!/usr/bin/bash
    set -euo pipefail
    groups=""
    for group in kvm libvirt docker; do
        if getent group "$group" >/dev/null; then
            groups="${groups:+$groups,}$group"
        fi
    done
    [ -n "$groups" ] || { echo "None of kvm/libvirt/docker exist on this system."; exit 1; }
    sudo usermod -aG "$groups" "$USER"
    echo "Added $USER to: $groups"
    echo "Log out and back in (or reboot) for the new group membership to take effect."

# Diagnose a missing or non-working Wi-Fi card (read-only; works with no network)
[group('Diagnostics')]
wifi-debug:
    @/usr/libexec/bazzite-tower-wifi-debug
