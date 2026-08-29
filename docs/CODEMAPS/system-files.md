<!-- Generated: 2026-08-08 | Files scanned: 21 | Token estimate: ~1050 -->
# System Files (baked-in runtime surface)

`system_files/` is `COPY`ed verbatim to `/`. Paths below are image-absolute.

## systemd units (`/usr/lib/systemd/system/`)

| Unit | Type | Ordering / condition | Helper | Purpose |
|---|---|---|---|---|
| `bazzite-tower-firstboot.service` | oneshot, RemainAfterExit | `After=systemd-user-sessions`; `ConditionPathExists=!/var/lib/.bazzite-tower-groups-done` | `…/firstboot` | add first uid≥1000 user to kvm,libvirt,docker; retries each boot until a user exists, then drops the marker |
| `bazzite-tower-wifi-backend-guard.service` | oneshot, RemainAfterExit | `After=local-fs`; `Before=NetworkManager` | `…/wifi-backend-guard` | force wpa_supplicant if `wifi.backend=iwd` is selected but iwd isn't enabled |
| `bazzite-tower-power-tuning.service` | oneshot, RemainAfterExit | `After=basic.target` | `…/power-tuning` | set `platform_profile=balanced` + EPP=`balance_performance` on every core (was firmware low-power) |
| `i915-resume-fix-check.service` | oneshot | `After=systemd-journald.service`; triggered by its `.timer` | `…/i915-resume-fix-check` | pre-7.0 kernel: no-op; 7.0+: grep this boot's journal for the cx0 DPLL s2idle-resume regression signature, warn if found |
| `portmaster.service` | simple, disabled VM spike | `After=network-online`; conflicts with OpenSnitch/firewalld; `StateDirectory=portmaster`; `BindReadOnlyPaths=` pins config from `/usr`; `StartLimitBurst=3`; `ExecStopPost=` recovers netfilter rules | `…/portmaster/portmaster-core --bin-dir … --data-dir … --log-stdout` | direct, pinned Portmaster core test; never enabled in an image. Both dir flags are load-bearing: without `--bin-dir` the daemon falls back to a hardcoded `/usr/lib/portmaster` and its updater exits 2 mkdir'ing it on read-only `/usr` |

All `.service` units except the disabled `portmaster.service` VM spike are
enabled in build.sh.

## systemd timers (`/usr/lib/systemd/system/`)

| Timer | Schedule | Purpose |
|---|---|---|
| `i915-resume-fix-check.timer` | `OnBootSec=5min`, `OnUnitActiveSec=1d`, `Persistent=true` | periodic trigger for `i915-resume-fix-check.service` — the machine-checkable signal Containerfile's kernel-pin comment points at |

`WantedBy=timers.target`, enabled in build.sh (`systemctl enable i915-resume-fix-check.timer`).

## libexec helpers (`/usr/libexec/`)

- `bazzite-tower-firstboot` — first regular user → `usermod -aG` only existing groups
- `bazzite-tower-wifi-backend-guard` — NM iwd-backend guard, idempotent
- `bazzite-tower-wifi-debug` — read-only Wi-Fi diagnostics (offline)
- `bazzite-tower-power-tuning` — write platform_profile + per-CPU EPP; skips absent/read-only knobs
- `i915-resume-fix-check` — kernel-version-gated check for the Meteor Lake cx0 DPLL s2idle-resume regression signature in the current boot's journal
(The former `bazzite-tower-portmaster-seed` helper is gone. Portmaster's config is no longer copied into `/var`: the unit `BindReadOnlyPaths=`-mounts `/usr/share/bazzite-tower/portmaster-config.default.json` over `/var/lib/portmaster/config.json`, so the update pin is image-managed and reverts with a rollback. systemd creates the mount destination itself, and a missing source fails the unit before `ExecStart` — fail closed.)

## Firewall selector (`build.d/95-firewall.sh`, `FIREWALL_DAEMON` build-arg)

Default `opensnitch`: pinned v1.8.0 RPM extraction (not repo-installed — see the
script for the systemd-live-at-%post hazard it works around), `opensnitch.service`
enabled, config staged at
`/usr/share/bazzite-tower/opensnitchd-default-config.json` (see below).
`portmaster.service` is symlinked to `/dev/null` (masked) in this default build.

`FIREWALL_DAEMON=portmaster` (never a default/published tag — build with
`just build-portmaster-spike`, validate in a VM first): OpenSnitch masked
instead, `portmaster.service` enabled — see the systemd units table above and
`docs/research/portmaster-bootc-spike.md`.

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

- `/etc/dnf/dnf.conf` (appended `exclude=` line, guarded on `[main]` being present) → written by `build.d/05-pin-kde-packages.sh` (build-time only, not from `system_files/`); excludes the KDE Plasma/KWin family so this build's own dnf transactions can't skew `kwin` ahead of `kscreenlocker`. Not `/etc/dnf/dnf.conf.d/` — this base runs dnf5, which has no such directory (real dnf5 drop-in dir is `/etc/dnf/libdnf5.conf.d/`); see `dependencies.md` and the "Correction" note in `docs/research/kwin-screenlocker-abi-2026-08-08/`
- `/usr/lib/modprobe.d/blacklist-unused-gpu.conf` → blacklist `amdgpu`, `amdxcp` (no AMD silicon; `xe` left loaded)
- `/usr/lib/modprobe.d/iwlwifi-be200-stability.conf` → `iwlmld power_scheme=1` (CAM) + `iwlwifi disable_11be=1 power_save=0 uapsd_disable=1` — BE200 firmware asserts `NMI_INTERRUPT_UNKNOWN` and the driver hard-resets the chip, freezing the desktop for 5-15s on this wifi-only box. `iwlmld` has its own power scheme that `iwlwifi.power_save` does not cover; see RUNBOOK "Wi-Fi: BE200 firmware asserts"
- `/usr/lib/modprobe.d/btusb-no-autosuspend.conf` → `options btusb enable_autosuspend=0` — autosuspend drops the Intel BT link on this ThinkPad
- `/usr/lib/udev/rules.d/` → device rules promoted from /etc drift 2026-08-29: `70-kvmfr.rules` (Looking Glass `/dev/kvmfr0`, GROUP=qemu), `99-smartcard.rules` (uaccess; was world-writable `MODE="0666"`), `99-i2c-designware.rules` (pin `power/control=on`, the controller misses wakeups under runtime PM), `70-xreal-xr.rules` + `70-viture-xr.rules` (XR glasses), `70-plustek-scanner.rules` (SPICE USB redirection needs user access)
- `/usr/lib/sysctl.d/99-tower-swappiness.conf` → `vm.swappiness=10` (zram was filling with RAM free)
- `/usr/lib/systemd/journald.conf.d/90-tower-journal-cap.conf` → `SystemMaxUse=4G` + `MaxRetentionSec=1month` (default cap ~10% of fs)
- `/usr/share/wireplumber/wireplumber.conf.d/90-tower-sof-backoff.conf` → shorten SOF node idle/error suspend window (defense-in-depth; dormant while SOF is bypassed)
- `/etc/smartmontools/smartd.conf` → monitor `/dev/nvme0`+`/dev/nvme1` (health, media errors, weekly long test, temp); logs to journal
- `/etc/xdg/baloofilerc` → seed indexer `exclude filters` with build/cache trees (.gradle, target, language caches)
- `/usr/libexec/bazzite-tower-stall-detect` + `/usr/lib/systemd/system/bazzite-tower-stall-detect.service` → samples CLOCK_MONOTONIC and records D-state workers on a stall. Catches freezes no kernel watchdog reports (soft lockup needs a spinning CPU; hung_task needs 120s). Built for the i915 GuC TLB invalidation timeout, drm/i915 #14469. Logs to the journal; query with `ujust freeze-report`
- `/usr/share/bazzite-tower/opensnitchd-default-config.json` → Snitchwatch-tuned opensnitchd config. **Staged, not live**: build.sh `install`s it over `/etc/opensnitchd/default-config.json` *after* the OpenSnitch RPM extraction (which writes that path itself), so it can't live at the real path here. Doubles as the pristine image-intent copy to diff a 3-way-merged `/etc` against. Deltas from the RPM default: `Server.Address` `127.0.0.1:50051` (Snitchwatch bridge), `ProcMonitorMethod` `proc` (bundled eBPF won't load on 6.19/7.x), `DefaultAction` `allow` (fail open during rollout)
