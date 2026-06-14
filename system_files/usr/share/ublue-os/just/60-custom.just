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

# Install/update the Looking Glass client on demand.
#
# The kvmfr host module is already baked in (base-provided; /dev/kvmfr0). The
# CLIENT is deliberately NOT baked: there is no official package, and it is
# version-coupled to the Looking Glass *host application* running in your Windows
# guest — you must run matching B-versions. This installs it into a Fedora
# distrobox (so the immutable host image stays clean) from the pgaskin COPR and
# exports the binary to ~/.local/bin. Re-run any time to update/match your guest.
[group('Virtualization')]
install-looking-glass-client:
    #!/usr/bin/bash
    set -euo pipefail
    box="looking-glass"
    image="registry.fedoraproject.org/fedora:latest"
    if ! distrobox list --no-color 2>/dev/null | grep -qw "$box"; then
        echo "Creating distrobox '$box'…"
        distrobox create --name "$box" --image "$image" --yes
    fi
    distrobox enter --name "$box" -- bash -lc '
        set -euo pipefail
        sudo dnf -y install dnf-plugins-core
        sudo dnf -y copr enable pgaskin/looking-glass-client
        sudo dnf -y install looking-glass-client
        distrobox-export --bin /usr/bin/looking-glass-client --export-path "$HOME/.local/bin"
    '
    echo
    echo "looking-glass-client installed/updated (exported to ~/.local/bin)."
    echo "Ensure ~/.local/bin is on PATH, then run: looking-glass-client"
    echo "Match its B-version to the Looking Glass host app in your Windows VM."

# Diagnose a missing or non-working Wi-Fi card (read-only; works with no network)
[group('Diagnostics')]
wifi-debug:
    @/usr/libexec/bazzite-tower-wifi-debug
