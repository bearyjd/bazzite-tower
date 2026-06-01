<!-- Generated: 2026-05-31 | Files scanned: 6 | Token estimate: ~600 -->
# System Files (baked-in runtime surface)

`system_files/` is `COPY`ed verbatim to `/`. Paths below are image-absolute.

## systemd units (`/usr/lib/systemd/system/`)

| Unit | Type | Ordering / condition | Helper | Purpose |
|---|---|---|---|---|
| `bazzite-tower-firstboot.service` | oneshot, RemainAfterExit | `After=systemd-user-sessions`; `ConditionPathExists=!/var/lib/.bazzite-tower-groups-done` | `…/firstboot` | add first uid≥1000 user to kvm,libvirt,docker; retries each boot until a user exists, then drops the marker |
| `bazzite-tower-wifi-backend-guard.service` | oneshot, RemainAfterExit | `After=local-fs`; `Before=NetworkManager` | `…/wifi-backend-guard` | if `wifi.backend=iwd` selected but iwd not enabled, write `zzz-…guard.conf` forcing wpa_supplicant; remove it once iwd is enabled |

Both `WantedBy=multi-user.target`, enabled in build.sh.

## libexec helpers (`/usr/libexec/`)

- `bazzite-tower-firstboot` (38L) — awk first regular user; `usermod -aG` only groups that exist
- `bazzite-tower-wifi-backend-guard` (53L) — grep NM conf.d for iwd backend; `systemctl is-enabled iwd`; write/remove override idempotently
- `bazzite-tower-wifi-debug` (68L) — read-only Wi-Fi diagnostics (rfkill, lspci, iwlwifi/DMAR dmesg, modules, NetworkManager, firmware, kargs); works offline

## ujust recipes (`/usr/share/ublue-os/just/60-custom.just`)

- group **Virtualization**: `vm-start`, `vm-stop`, `vm-list`, `vm-net-status`, `fix-vm-groups`
- group **Diagnostics**: `wifi-debug` → `/usr/libexec/bazzite-tower-wifi-debug`

## bootc kargs (`/usr/lib/bootc/kargs.d/`)

- `00-iommu.toml` → `kargs = ["intel_iommu=on", "iommu=pt"]` — VFIO/PCI passthrough; applied at install and on every upgrade
