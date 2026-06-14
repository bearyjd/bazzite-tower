<!-- Generated: 2026-06-14 | Files scanned: 19 | Token estimate: ~900 -->
# System Files (baked-in runtime surface)

`system_files/` is `COPY`ed verbatim to `/`. Paths below are image-absolute.

## systemd units (`/usr/lib/systemd/system/`)

| Unit | Type | Ordering / condition | Helper | Purpose |
|---|---|---|---|---|
| `bazzite-tower-firstboot.service` | oneshot, RemainAfterExit | `After=systemd-user-sessions`; `ConditionPathExists=!/var/lib/.bazzite-tower-groups-done` | `…/firstboot` | add first uid≥1000 user to kvm,libvirt,docker; retries each boot until a user exists, then drops the marker |
| `bazzite-tower-wifi-backend-guard.service` | oneshot, RemainAfterExit | `After=local-fs`; `Before=NetworkManager` | `…/wifi-backend-guard` | force wpa_supplicant if `wifi.backend=iwd` is selected but iwd isn't enabled |
| `bazzite-tower-power-tuning.service` | oneshot, RemainAfterExit | `After=basic.target` | `…/power-tuning` | set `platform_profile=balanced` + EPP=`balance_performance` on every core (was firmware low-power) |

All `WantedBy=multi-user.target`, enabled in build.sh.

## libexec helpers (`/usr/libexec/`)

- `bazzite-tower-firstboot` — first regular user → `usermod -aG` only existing groups
- `bazzite-tower-wifi-backend-guard` — NM iwd-backend guard, idempotent
- `bazzite-tower-wifi-debug` — read-only Wi-Fi diagnostics (offline)
- `bazzite-tower-power-tuning` — write platform_profile + per-CPU EPP; skips absent/read-only knobs

## ujust recipes (`/usr/share/ublue-os/just/60-custom.just`)

- **Virtualization**: `vm-start`, `vm-stop`, `vm-list`, `vm-net-status`, `fix-vm-groups`, `install-looking-glass-client` (installs the version-coupled LG client into a Fedora distrobox from the pgaskin COPR → `~/.local/bin`; kvmfr module is base-provided)
- **Diagnostics**: `wifi-debug`

## bootc kargs (`/usr/lib/bootc/kargs.d/`, applied at install + every upgrade)

- `00-iommu.toml` → `intel_iommu=on iommu=pt` — VFIO/PCI passthrough
- `10-i915-display.toml` → `i915.enable_dc=0 i915.enable_psr=0 i915.enable_psr2_sel_fetch=0` — eDP PSR/DC stability on the MTL panel
- `20-suspend.toml` → `mem_sleep_default=s2idle` — MTL has no working S3
- `25-audio-sof-bypass.toml` → `snd_intel_dspcfg.dsp_driver=1` — force legacy HDA; kernel SOF ABI 3.23 can't load firmware's ABI-3.29 topology (no repo downgrade). Speakers (TAS2781 via ALC287 HDA side-codec)/HP/HDMI work; loses DMIC array
- `30-vfio-kvm.toml` → `kvmfr.static_size_mb=128 vfio_pci.disable_vga=1 kvm.ignore_msrs=1 kvm.report_ignored_msrs=0` — codified passthrough tuning (additive; not base defaults)
- `40-nvme.toml` → `nvme_core.default_ps_max_latency_us=0` — Samsung 990 EVO Plus APST-idle workaround

## Other drop-ins

- `/usr/lib/modprobe.d/blacklist-unused-gpu.conf` → blacklist `amdgpu`, `amdxcp` (no AMD silicon; `xe` left loaded)
- `/usr/lib/sysctl.d/99-tower-swappiness.conf` → `vm.swappiness=10` (zram was filling with RAM free)
- `/usr/lib/systemd/journald.conf.d/90-tower-journal-cap.conf` → `SystemMaxUse=500M` (default ~10% of fs)
- `/usr/share/wireplumber/wireplumber.conf.d/90-tower-sof-backoff.conf` → shorten SOF node idle/error suspend window (defense-in-depth; dormant while SOF is bypassed)
- `/etc/smartmontools/smartd.conf` → monitor `/dev/nvme0`+`/dev/nvme1` (health, media errors, weekly long test, temp); logs to journal
- `/etc/xdg/baloofilerc` → seed indexer `exclude filters` with build/cache trees (.gradle, target, language caches)
