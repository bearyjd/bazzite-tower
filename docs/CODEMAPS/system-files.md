<!-- Generated: 2026-08-08 | Files scanned: 21 | Token estimate: ~1050 -->
<!-- Targeted update: 2026-10-05 | OpenSnitch readiness and system-bridge boundary; not a full regeneration -->
# System Files (baked-in runtime surface)

`system_files/` is `COPY`ed verbatim to `/`. Paths below are image-absolute.

## systemd units (`/usr/lib/systemd/system/`)

| Unit | Type | Ordering / condition | Helper | Purpose |
|---|---|---|---|---|
| `bazzite-tower-firstboot.service` | oneshot, RemainAfterExit | `After=systemd-user-sessions`; `ConditionPathExists=!/var/lib/.bazzite-tower-groups-done` | `…/firstboot` | add first uid≥1000 user to kvm,libvirt only; retries each boot until a user exists, then drops the marker |
| `bazzite-tower-wifi-backend-guard.service` | oneshot, RemainAfterExit | `After=local-fs`; `Before=NetworkManager` | `…/wifi-backend-guard` | force wpa_supplicant if `wifi.backend=iwd` is selected but iwd isn't enabled |
| `bazzite-tower-power-tuning.service` | oneshot, RemainAfterExit | `After=basic.target` | `…/power-tuning` | set `platform_profile=balanced` + EPP=`balance_performance` on every core (was firmware low-power) |
| `i915-resume-fix-check.service` | oneshot | `After=systemd-journald.service`; triggered by its `.timer` | `…/i915-resume-fix-check` | pre-7.0 kernel: no-op; 7.0+: grep this boot's journal for the cx0 DPLL s2idle-resume regression signature, warn if found |
| `docker-libvirt-forwarding.path` | path, `WantedBy=multi-user.target` | `PathChanged=/run/libvirt/network` (any change in libvirt's network state dir); `TriggerLimitBurst=1000`/10s | activates the `.service` | libvirt-side reconciliation trigger |
| `docker-libvirt-forwarding.service` | oneshot | `ExecCondition` docker.service active and virtnetworkd.service active/activating/reloading; `StartLimitIntervalSec=0`; sleep 1, helper, sleep 2, helper (second pass catches merged events); `UnsetEnvironment` of test overrides | `/usr/local/libexec/docker-libvirt-forwarding` | reconcile DOCKER-USER NAT rules |
| `docker-libvirt-forwarding.timer` | timer, `WantedBy=timers.target` | `OnBootSec=2min`, `OnUnitInactiveSec=10min` | the `.service` | self-heal if an event was missed |
| `portmaster.service` | simple, disabled VM spike | `After=network-online`; conflicts with OpenSnitch/firewalld; `StateDirectory=portmaster`; `BindReadOnlyPaths=` pins config from `/usr`; `StartLimitBurst=3`; `ExecStopPost=` recovers netfilter rules | `…/portmaster/portmaster-core --bin-dir … --data-dir … --log-stdout` | direct, pinned Portmaster core test; never enabled in an image. Both dir flags are load-bearing: without `--bin-dir` the daemon falls back to a hardcoded `/usr/lib/portmaster` and its updater exits 2 mkdir'ing it on read-only `/usr` |

Docker, Cockpit, and Waydroid are explicitly disabled in the image; Tailscale
is enabled without a node identity. Other listed services are enabled in
build.sh except the disabled `portmaster.service` VM spike.

## systemd timers (`/usr/lib/systemd/system/`)

| Timer | Schedule | Purpose |
|---|---|---|
| `i915-resume-fix-check.timer` | `OnBootSec=5min`, `OnUnitActiveSec=1d`, `Persistent=true` | periodic trigger for `i915-resume-fix-check.service` — the machine-checkable signal Containerfile's kernel-pin comment points at |

`WantedBy=timers.target`, enabled in build.sh (`systemctl enable i915-resume-fix-check.timer`).

## systemd-sleep hooks (`/usr/lib/systemd/system-sleep/`)

- `bazzite-tower-bluetooth-resume-guard` — `systemctl restart bluetooth.service` on `post` resume only. No unit/enable step: any executable in this directory is auto-invoked by `systemd-suspend(-then-hibernate)/-hibernate/-hybrid-sleep.service`. Mitigates a previously-paired Bluetooth device (reported: a mouse) not reconnecting after suspend — bluetoothd stays "active" across the cycle with nothing logged, and there's no BE200-style firmware NMI/reset signature for hci0 the way there is for Wi-Fi (below), so it's the adapter's HCI state, not firmware, that doesn't recover cleanly. Same s2idle-resume-quality class of bug as i915 display and the BE200 Wi-Fi radio on this hardware. Health-checks via `busctl get-property ... Powered` (not `bluetoothctl show`, which can SIGABRT — coredump — if queried before D-Bus is fully up) for up to 30s (a documented self-recovery took 23s; escalating sooner would unbind a perfectly healthy adapter). If still not healthy, unbind/rebinds the BE200 Bluetooth USB function (`8087:0036`, resolving the driver symlink's real target *before* unbinding — the kernel removes that symlink on unbind, so reading it again afterwards to rebind silently fails) to force a closer equivalent of a full reboot's reinitialization, then restarts the daemon again. `etc/systemd/system/bluetooth.service.d/10-fast-timeout.conf` bounds each restart to 10s start/10s stop, since this script can restart the service twice and systemd-sleep blocks the whole system's resume until it exits

## libexec helpers (`/usr/libexec/`)

- `bazzite-tower-firstboot` — first regular user → `usermod -aG` only existing groups
- `bazzite-tower-wifi-backend-guard` — NM iwd-backend guard, idempotent
- `bazzite-tower-wifi-debug` — read-only Wi-Fi diagnostics (offline)
- `bazzite-tower-health` — reporting-only optional-service, monitoring, firmware, and security-tool summary (no sudo or state changes)
- `bazzite-tower-opensnitch-readiness` — read-only selected-profile dispatcher; legacy retains the fixed release hash/user-service/TCP listener check. System dispatch requires an explicit root invocation of `bazzite-tower-snitchwatch-readiness` for provenance, service hardening and socket/token DAC. Neither proves GUI decisions
- `bazzite-tower-snitchwatch-migrate` — explicit `check|apply|rollback` transaction; detects legacy conflicts and drift, records baseline state and supports restoration without changing fail-open policy
- `docker-libvirt-forwarding` — Docker `ExecStartPost` helper: takes a bounded-wait `/run` lock, discovers only active libvirt XML NAT bridges, derives each live IPv4 network and default uplink, and idempotently adds tagged `NEW,ESTABLISHED,RELATED` / `RELATED,ESTABLISHED` `DOCKER-USER` pairs; removes only stale rules bearing its own prefix and fails if Docker did not create that chain
- `bazzite-tower-power-tuning` — write platform_profile + per-CPU EPP; skips absent/read-only knobs
- `i915-resume-fix-check` — kernel-version-gated check for the Meteor Lake cx0 DPLL s2idle-resume regression signature in the current boot's journal
(The former `bazzite-tower-portmaster-seed` helper is gone. Portmaster's config is no longer copied into `/var`: the unit `BindReadOnlyPaths=`-mounts `/usr/share/bazzite-tower/portmaster-config.default.json` over `/var/lib/portmaster/config.json`, so the update pin is image-managed and reverts with a rollback. systemd creates the mount destination itself, and a missing source fails the unit before `ExecStart` — fail closed.)

## Firewall selector (`build.d/95-firewall.sh`, `FIREWALL_DAEMON` build-arg)

Default `opensnitch` uses the pinned v1.8.0 RPM extraction, enables the daemon
and masks Portmaster. The independent `SNITCHWATCH_BRIDGE` marker lives at
`/usr/share/bazzite-tower/snitchwatch-bridge-profile`: `legacy` selects the
existing TCP/user-service contract; `system` requires OpenSnitch and selects
an opt-in native system deployment.

The system installer stages release-owned assets instead of duplicating them
under `system_files/`: `/usr/bin/snitchwatch-bridge-cli`,
`snitchwatch-system-bridge.service`, `snitchwatch-system-bridge-grpc.socket`,
`snitchwatch-system-bridge-gui.socket`, `/usr/lib/sysusers.d/snitchwatch.conf`,
`/usr/lib/tmpfiles.d/snitchwatch.conf` and the one shipped opensnitchd rule
`/etc/opensnitchd/rules/000-snitchwatch-bridge-fetch.json` (bridge HTTPS
blocklist fetch; pinned, and the only `/etc` file the manifest verifier reads,
so a local edit or delete fails verification). Accounts are named `snitchwatch`
and `snitchwatch-ui`; numeric IDs are allocated by sysusers. The protected IPC
and auth directories use `/run/snitchwatch` and `/run/snitchwatch-auth`; no GUI
user membership is baked in. Licensing lives under
`/usr/share/licenses/snitchwatch-bridge`; installed artifact/overlay provenance
lives under `/usr/share/snitchwatch`. A separate
`system-daemon-manifest.json` records the source-built OpenSnitch 1.8.0 repair;
`/usr/libexec/snitchwatch/verify-system-daemon.py` validates its source, patch,
module/protocol/toolchain/license inventory and installed `/usr/bin/opensnitchd`.
The retained candidate executable and patch live under
`/usr/share/snitchwatch/daemon`. Readiness also verifies the live daemon PID
uses the reviewed executable and root NFQUEUE account.

The daemon drop-in selects `WorkingDirectory=/run/snitchwatch` and requires
the gRPC socket. Its image-intent configuration selects
`unix:opensnitchd.sock`, `proc` and `allow`. Readiness verifies the selected
profile and migration preserves a recorded legacy baseline. The GUI remains
an explicitly selected per-user system Flatpak profile. See
[validation and migration gates](../research/snitchwatch-system-bridge.md).

`FIREWALL_DAEMON=portmaster` remains a disabled, isolated VM spike using
`just build-portmaster-spike`; it cannot select the system bridge.


## ujust recipes (`/usr/share/ublue-os/just/60-custom.just`)

- **Virtualization**: `vm-start`, `vm-stop`, `vm-list`, `vm-net-status`, `fix-vm-groups`, `install-looking-glass-client` (installs the version-coupled LG client into a Fedora distrobox from the pgaskin COPR → `~/.local/bin`; kvmfr module is base-provided)
- **Diagnostics**: `wifi-debug`, `tower-health`, `opensnitch-readiness` (selected-profile fail-open preflight)
- **Host opt-ins**: `enable-docker` (root-equivalent group warning), `enable-cockpit` (loopback-only socket; prints a separate Tailscale Serve command), `enable-waydroid` (does not initialise Android)

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
- `/etc/systemd/system/cockpit.socket.d/10-loopback.conf` → clears Cockpit's wildcard listener and binds the opt-in socket only to `127.0.0.1` and `::1`
- `/etc/systemd/system/docker.service.d/libvirt-forwarding.conf` → non-fatally runs `/usr/local/libexec/docker-libvirt-forwarding` after Docker creates `DOCKER-USER`; it does not alter Docker's FORWARD policy, Docker-managed chains, or nftables tables
- `docker-libvirt-forwarding.{path,service,timer}` (table above) replace a libvirt network hook, which SELinux `virtnetworkd_t` cannot exec
- `/etc/NetworkManager/dispatcher.d/90-docker-libvirt-forwarding` → asynchronously requests reconciliation on NetworkManager route, VPN, or reapply events; always returns success and invokes the helper only while Docker is active
- `/usr/lib/bootc/install/50-signature-policy.toml` → asks bootc disk installation to enforce the image's container signature policy; it is image-owned rather than a disk-layout setting
- `/etc/xdg/baloofilerc` → seed indexer `exclude filters` with build/cache trees (.gradle, target, language caches)
- `/usr/libexec/bazzite-tower-stall-detect` + `/usr/lib/systemd/system/bazzite-tower-stall-detect.service` → samples CLOCK_MONOTONIC and records D-state workers on a stall. Catches freezes no kernel watchdog reports (soft lockup needs a spinning CPU; hung_task needs 120s). Built for the i915 GuC TLB invalidation timeout, drm/i915 #14469. Logs to the journal; query with `ujust freeze-report`
- `/usr/share/bazzite-tower/opensnitchd-default-config.json` → Snitchwatch-tuned opensnitchd config. **Staged, not live**: `95-firewall.sh` `install`s it over `/etc/opensnitchd/default-config.json` *after* the OpenSnitch RPM extraction (which writes that path itself), so it can't live at the real path here. Doubles as the pristine image-intent copy to diff a 3-way-merged `/etc` against. Deltas from the RPM default: `Server.Address` `127.0.0.1:50051` in legacy or `unix:opensnitchd.sock` in the opt-in system candidate, `ProcMonitorMethod` `proc` (bundled eBPF won't load on 6.19/7.x), `DefaultAction` `allow` (fail open during rollout)
