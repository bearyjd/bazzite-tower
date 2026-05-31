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
    #!/usr/bin/bash
    # Collects everything needed to root-cause "the Wi-Fi card isn't even there":
    # PCI/USB enumeration, driver load, firmware crashes/DMAR faults, rfkill
    # blocks, NetworkManager state, and the live kernel cmdline (IOMMU kargs).
    # Read-only and safe to run offline. Pipe to a file to share: `ujust wifi-debug > wifi.txt`
    set +e
    echo "== rfkill (soft/hard radio blocks) =="
    rfkill list 2>/dev/null || echo "  rfkill unavailable"
    echo
    echo "== PCI network controllers (driver in 'Kernel driver in use') =="
    lspci -nnk | grep -iA3 -E 'network|wireless|wi-?fi|802\.11' || echo "  no PCI network controller matched — card not enumerated (BIOS/hardware?)"
    echo
    echo "== USB wireless devices =="
    lsusb 2>/dev/null | grep -iE 'wireless|wi-?fi|802\.11|wlan' || echo "  none"
    echo
    echo "== iwlwifi / 80211 kernel messages (look for DMAR faults, ucode errors) =="
    sudo dmesg 2>/dev/null | grep -iE 'iwlwifi|iwlmvm|cfg80211|mac80211|firmware|DMAR' | tail -n 40 || echo "  none"
    echo
    echo "== loaded wireless modules =="
    lsmod | grep -iE 'iwlwifi|iwlmvm|cfg80211|mac80211' || echo "  no wireless modules loaded"
    echo
    echo "== NetworkManager devices =="
    nmcli -f DEVICE,TYPE,STATE device 2>/dev/null || echo "  nmcli unavailable"
    echo
    echo "== NetworkManager radio =="
    nmcli radio 2>/dev/null || echo "  nmcli unavailable"
    echo
    echo "== iwlwifi firmware shipped in image =="
    ls /usr/lib/firmware/iwlwifi-* 2>/dev/null | head || echo "  no iwlwifi-* firmware files found"
    echo
    echo "== live kernel cmdline (note intel_iommu / iommu=pt) =="
    cat /proc/cmdline
