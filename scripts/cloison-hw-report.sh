#!/usr/bin/env bash
# cloison-hw-report — read-only IOMMU/VFIO/USB topology report. Changes nothing.
# Usage: bash scripts/cloison-hw-report.sh > iommu-$(date +%F).txt   (sudo for kernel log lines)
set -uo pipefail
shopt -s nullglob

sec() { printf '\n== %s\n' "$1"; }
rd()  { cat "$1" 2>/dev/null || printf '?'; }

sec "kernel / cmdline / firmware"
uname -r
cat /proc/cmdline
rd /sys/class/dmi/id/product_name; echo; rd /sys/class/dmi/id/bios_version; echo
printf 'mem_sleep: %s\n' "$(rd /sys/power/mem_sleep)"

sec "ACS override (must be absent)"
grep -o 'pcie_acs_override=[^ ]*' /proc/cmdline || echo "absent (good)"

sec "IOMMU units"
units=(/sys/class/iommu/*)
if ((${#units[@]})); then printf '%s\n' "${units[@]##*/}"; else echo "NONE: IOMMU not active"; fi
printf 'vfio_iommu_type1.allow_unsafe_interrupts=%s\n' \
  "$(rd /sys/module/vfio_iommu_type1/parameters/allow_unsafe_interrupts)"

sec "kernel log: DMAR / IOMMU / interrupt remapping / iwlwifi faults (needs root)"
journalctl -k -b 0 --no-pager 2>/dev/null \
  | grep -E 'DMAR|IOMMU|[Ii]nterrupt remapping|iwlwifi.*(DMAR|fault|error)' | head -60 \
  || echo "(no access — rerun with sudo)"

sec "Thunderbolt / USB4 DMA protection"
for d in /sys/bus/thunderbolt/devices/domain*; do
  printf '%s iommu_dma_protection=%s security=%s\n' "${d##*/}" \
    "$(rd "$d/iommu_dma_protection")" "$(rd "$d/security")"
done

sec "KSM"
printf 'ksm run=%s pages_shared=%s\n' "$(rd /sys/kernel/mm/ksm/run)" "$(rd /sys/kernel/mm/ksm/pages_shared)"

sec "IOMMU groups (endpoint count excludes PCI bridges, class 0x0604xx)"
for g in $(printf '%s\n' /sys/kernel/iommu_groups/* | sed 's|.*/||' | sort -n); do
  [[ -n "$g" ]] || continue
  devs=(/sys/kernel/iommu_groups/"$g"/devices/*)
  endpoints=0
  for dev in "${devs[@]}"; do [[ "$(rd "$dev/class")" == 0x0604* ]] || endpoints=$((endpoints+1)); done
  printf -- '-- group %s (%d device(s), %d endpoint(s))\n' "$g" "${#devs[@]}" "$endpoints"
  for dev in "${devs[@]}"; do
    bdf=${dev##*/}
    drv="(none)"; [[ -e "$dev/driver" ]] && drv=$(basename "$(readlink -f "$dev/driver")")
    printf '   %s class=%s driver=%s reset_method=%s d3cold_allowed=%s\n      %s\n' \
      "$bdf" "$(rd "$dev/class")" "$drv" "$(rd "$dev/reset_method")" "$(rd "$dev/d3cold_allowed")" \
      "$(lspci -nns "$bdf" 2>/dev/null | cut -d' ' -f2-)"
  done
done

sec "USB topology per xHCI (connect_type: hotplug=external port, hardwired=internal)"
for pci in /sys/bus/pci/devices/*; do
  [[ "$(rd "$pci/class")" == 0x0c0330 ]] || continue
  real=$(readlink -f "$pci")
  printf -- '-- xHCI %s (iommu_group %s)\n' "${pci##*/}" "$(basename "$(readlink -f "$pci/iommu_group")")"
  for port in "$real"/usb*/*-0:1.0/usb*-port*; do
    printf '   port %-14s connect_type=%s\n' "${port##*/}" "$(rd "$port/connect_type")"
  done
  for u in /sys/bus/usb/devices/*; do
    [[ -f "$u/idVendor" ]] || continue
    case "$(readlink -f "$u")" in "$real"/*) ;; *) continue ;; esac
    ifcls=$(for i in "$u"/*:*; do rd "$i/bInterfaceClass"; printf ' '; done)
    printf '   dev %-8s %s:%s removable=%s devclass=%s ifclasses=[%s] "%s"\n' "${u##*/}" \
      "$(rd "$u/idVendor")" "$(rd "$u/idProduct")" "$(rd "$u/removable")" \
      "$(rd "$u/bDeviceClass")" "${ifcls% }" "$(rd "$u/product")"
  done
done

sec "Candidates (network 0x0280xx, xHCI 0x0c0330, USB4/TB NHI 0x0c0340, display 0x03xxxx)"
for pci in /sys/bus/pci/devices/*; do
  c=$(rd "$pci/class")
  case "$c" in 0x0280*|0x0c0330|0x0c0340|0x03*) ;; *) continue ;; esac
  g=$(basename "$(readlink -f "$pci/iommu_group" 2>/dev/null)")
  n=0; for d in /sys/kernel/iommu_groups/"$g"/devices/*; do [[ "$(rd "$d/class")" == 0x0604* ]] || n=$((n+1)); done
  printf '%s class=%s group=%s endpoints_in_group=%s reset_method=%s  %s\n' "${pci##*/}" "$c" "$g" "$n" \
    "$(rd "$pci/reset_method")" "$(lspci -s "${pci##*/}" 2>/dev/null | cut -d' ' -f2-)"
done
