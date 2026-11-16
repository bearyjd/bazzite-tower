# IOMMU groups, ThinkPad P1 Gen 7: preliminary M0 report (2026-11-16)

Input to the Cloison plan (`docs/prp/cloison-kvm-compartments.md` section 7). Produced with
`scripts/cloison-hw-report.sh`, run **without root** on kernel `7.2.15-ogc1.1.fc45`, so the kernel-log section
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

## Still needed before any decision is recorded
1. `sudo bash scripts/cloison-hw-report.sh > iommu-$(date +%F).txt` on the host: kernel-log lines, step 1.
2. Cross-check `lspci -nnk` for the two xHCI controllers and the `reset_method` gap.
3. The bind/unbind soak and suspend matrix, only after the owner approves touching device bindings.
