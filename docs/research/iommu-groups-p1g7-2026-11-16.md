# IOMMU groups, ThinkPad P1 Gen 7: M0 report (2026-11-16, non-root run + sudo run)

Input to the Cloison plan (`docs/prp/cloison-kvm-compartments.md` section 7). Produced with
`scripts/cloison-hw-report.sh` (now in the private `bearyjd/cloison` repo), run **without root** on kernel `7.2.15-ogc1.1.fc45`, so the kernel-log section
(DMAR, interrupt remapping, iwlwifi faults) is EMPTY. This is a topology snapshot, not an acceptance result.
No device was bound, unbound or reset. Raw output is not committed (the cmdline carries LUKS UUIDs).

## Platform
- IOMMU active (`dmar0`, `dmar1`), `intel_iommu=on iommu=pt`, no `pcie_acs_override` (absent, good).
- `vfio_iommu_type1.allow_unsafe_interrupts=N`. Thunderbolt domain0: `iommu_dma_protection=1`, `security=user`.
- KSM off. Existing kargs include `vfio-pci`, `vfio_pci.disable_vga=1`, `kvmfr.static_size_mb=128`, `kvm.ignore_msrs=1`.

## Candidates (endpoints exclude PCI bridges)
| Device | BDF | Group | Endpoints in group | reset_method |
|---|---|---|---|---|
| Wi-Fi 7 BE200 (iwlwifi) | 0000:09:00.0 | 23 | 1 | flr bus |
| xHCI (Core Ultra 200) | 0000:00:0d.0 | 11 | 2 (with Thunderbolt DMA0 0000:00:0d.2) | unreadable (`?`) |
| xHCI (Meteor Lake-P USB 3.2) | 0000:00:14.0 | 12 | 2 (with Shared SRAM 0000:00:14.2, intel_pmc_ssram_telemetry) | unreadable (`?`) |
| NVIDIA AD106M (dGPU) | 0000:01:00.0 | 19 | 1 | flr bus |
| Intel Arc iGPU | 0000:00:02.0 | 0 | 1 | flr |

## Reading against the plan's decision tree (provisional)
- **BE200 (`sys-net`)**: single-endpoint group with `flr bus`, so steps 2 and 4 pass. Step 1 (interrupt remapping)
  and steps 5 to 7 (bind/unbind soak, suspend matrix, in-guest reset recovery) are NOT done.
- **xHCI 0d.0**: shares group 11 with the Thunderbolt NHI. Giving it to `sys-usb` takes USB4/Thunderbolt with it (plan D4).
- **xHCI 14.0**: shares group 12 with platform SRAM telemetry, which a VM should not own; whole-group passthrough
  needs a deliberate call (plan step 3). Neither xHCI exposes `reset_method`; under plan rev 1 (§7.2 step 4a, no boot-once mode) that rejects passthrough unless new evidence appears.

## Update: sudo run (owner, 2026-11-16 21:50 local), same topology, kernel log now present
Source: `sudo bash scripts/cloison-hw-report.sh` (in the private `bearyjd/cloison` repo) on kernel `7.2.15-ogc1.1.fc45` (booted 2026-11-13). Raw output not committed (cmdline carries LUKS UUIDs).
- **Decision-tree step 1 passes:** IOMMU enabled (`dmar0`, `dmar1`, queued invalidation), `DMAR-IR: Enabled IRQ remapping in x2apic mode`, no RMRR/ATSR table, no `pcie_acs_override`, no DMAR or iwlwifi fault lines in the boot-time log (21 matching lines, not truncated). `pci 0000:00:02.0: DMAR: Skip IOMMU disabling for graphics` is expected for i915.
- Thunderbolt domain0 `iommu_dma_protection=1` (firmware-reported kernel DMA protection), `security=user`.
- **xHCI 0000:00:14.0 (group 12)** serves the external USB-A style ports (usb3 ports 1, 3, 4, 6 and usb4 ports 1, 2 report `hotplug`; a film scanner on port 3) AND devices marked fixed: `8087:0036` (an Intel wireless-class device, almost certainly the internal Bluetooth: UNVERIFIED inference), `27c6:6594` Goodix fingerprint, `30c9:00cd` integrated camera. Passing it through to a `sys-usb` VM would remove host Bluetooth, fingerprint and camera, and it shares group 12 with Shared SRAM.
- **xHCI 0000:00:0d.0 (group 11)** serves two `hotplug` ports (usb2 ports 1 and 2; nothing attached at the time) and shares the group with the Thunderbolt NHI `0000:00:0d.2`: passthrough takes USB4/Thunderbolt tunnelling with it.
- Neither xHCI exposes a readable `reset_method` (`?`).

### Provisional reading (not decisions)
- D4: neither xHCI is a clean `sys-usb` candidate; the plan's USBGuard + redirect fallback matches the evidence.
- D3: the BE200 passes decision-tree steps 1, 2 and 4. Steps 5-7 (bind/unbind soak, suspend matrix, in-guest reset recovery) are NOT done and need explicit owner approval (they touch device bindings).
- D6: no effective-DMA-domain or invalidation-strictness evidence yet; nothing here supports changing `iommu=pt`.

## Still needed before any decision is recorded
1. (done 2026-11-16, see the update above)
2. Cross-check `lspci -nnk` for the two xHCI controllers and the `reset_method` gap.
3. The bind/unbind soak and suspend matrix, only after the owner approves touching device bindings.
