# bazzite-tower custom recipes — VM / libvirt management
#
# libvirt runs in modular mode on this image: per-driver daemons (virtqemud,
# virtnetworkd, ...) are socket-activated and enabled at boot, so the stack is
# normally already up. The monolithic libvirtd is masked. These recipes drive
# the modular daemons rather than libvirtd.

# Start the libvirt virtualization stack (modular daemons)
[group('Virtualization')]
vm-start:
    sudo systemctl start virtqemud.socket virtnetworkd.socket virtstoraged.socket virtnodedevd.socket

# Stop the libvirt virtualization stack (modular daemons)
[group('Virtualization')]
vm-stop:
    sudo systemctl stop virtqemud.socket virtnetworkd.socket virtstoraged.socket virtnodedevd.socket

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
    sudo usermod -aG "$groups" "$USER"
    echo "Added $USER to: $groups"
    echo "Log out and back in (or reboot) for the new group membership to take effect."
