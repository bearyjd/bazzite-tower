<!-- Generated: 2026-06-08 | Files scanned: 8 | Token estimate: ~650 -->
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

Applied at install and on every upgrade.

- `00-iommu.toml` → `kargs = ["intel_iommu=on", "iommu=pt"]` — VFIO/PCI passthrough
- `10-i915-display.toml` → `kargs = ["i915.enable_dc=0", "i915.enable_psr=0", "i915.enable_psr2_sel_fetch=0"]` — disable Intel PSR/DC; fixes eDP PLL errors/flicker on the Meteor Lake panel
- `20-suspend.toml` → `kargs = ["mem_sleep_default=deep"]` — default to S3 deep suspend (silently falls back to s2idle if firmware lacks S3)
