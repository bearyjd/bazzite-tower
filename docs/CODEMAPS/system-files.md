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
| `docker-libvirt-forwarding.path` | path, `WantedBy=multi-user.target` | `PathChanged=/run/libvirt/network` (any change in libvirt's network state dir); `TriggerLimitBurst=1000`/10s | activates the `.service` | libvirt-side reconciliation trigger |
| `docker-libvirt-forwarding.service` | oneshot | `ExecCondition` docker.service active and virtnetworkd.service active/activating/reloading; `StartLimitIntervalSec=0`; sleep 1, helper, sleep 2, helper (second pass catches merged events); `UnsetEnvironment` of test overrides | `/usr/local/libexec/docker-libvirt-forwarding` | reconcile DOCKER-USER NAT rules |
| `docker-libvirt-forwarding.timer` | timer, `WantedBy=timers.target` | `OnBootSec=2min`, `OnUnitInactiveSec=10min` | the `.service` | self-heal if an event was missed |
| `portmaster.service` | simple, disabled VM spike | `After=network-online`; conflicts with OpenSnitch/firewalld; `StateDirectory=portmaster`; `BindReadOnlyPaths=` pins config from `/usr`; `StartLimitBurst=3`; `ExecStopPost=` recovers netfilter rules | `…/portmaster/portmaster-core --bin-dir … --data-dir … --log-stdout` | direct, pinned Portmaster core test; never enabled in an image. Both dir flags are load-bearing: without `--bin-dir` the daemon falls back to a hardcoded `/usr/lib/portmaster` and its updater exits 2 mkdir'ing it on read-only `/usr` |

Docker, Cockpit, and Waydroid are explicitly disabled in the image; Tailscale
is enabled without a node identity. Other listed services are enabled in
build.sh except the disabled `portmaster.service` VM spike.

The image-owned `/usr/lib/systemd/system/logrotate.service.d/20-boot-log-label.conf`
adds only `ExecStartPre=bazzite-tower-boot-log-label`; it preserves the vendor
logrotate command, timer and AC-power condition and enables no extra service.
There is no kvmfr relabel or local SELinux mapping removal.

## systemd-sleep hooks (`/usr/lib/systemd/system-sleep/`)

- `bazzite-tower-bluetooth-resume-guard` — `systemctl restart bluetooth.service` on `post` resume only. No unit/enable step: any executable in this directory is auto-invoked by `systemd-suspend(-then-hibernate)/-hibernate/-hybrid-sleep.service`. Mitigates a previously-paired Bluetooth device (reported: a mouse) not reconnecting after suspend — bluetoothd stays "active" across the cycle with nothing logged, and there's no BE200-style firmware NMI/reset signature for hci0 the way there is for Wi-Fi (below), so it's the adapter's HCI state, not firmware, that doesn't recover cleanly. Same s2idle-resume-quality class of bug as i915 display and the BE200 Wi-Fi radio on this hardware. Health-checks via `busctl get-property ... Powered` (not `bluetoothctl show`, which can SIGABRT — coredump — if queried before D-Bus is fully up) for up to 30s (a documented self-recovery took 23s; escalating sooner would unbind a perfectly healthy adapter). If still not healthy, unbind/rebinds the BE200 Bluetooth USB function (`8087:0036`, resolving the driver symlink's real target *before* unbinding — the kernel removes that symlink on unbind, so reading it again afterwards to rebind silently fails) to force a closer equivalent of a full reboot's reinitialization, then restarts the daemon again. `etc/systemd/system/bluetooth.service.d/10-fast-timeout.conf` bounds each restart to 10s start/10s stop, since this script can restart the service twice and systemd-sleep blocks the whole system's resume until it exits

## libexec helpers (`/usr/libexec/`)

- `bazzite-tower-firstboot` — first regular user → `usermod -aG` only existing groups
- `bazzite-tower-wifi-backend-guard` — NM iwd-backend guard, idempotent
- `bazzite-tower-wifi-debug` — read-only Wi-Fi diagnostics (offline)
- `bazzite-tower-health` — bounded reporting-only booted/staged image identity (selected rpm-ostree fields, OSTree checksum distinguished from container digest), loaded/installed versions, failed services, MCE/HardwareCorrupted evidence, effective SELinux labels and exact kvmfr local-mapping presence. `--diagnostics` adds limited current-boot/resume/RAS excerpts; missing or denied probes are UNKNOWN. No sudo, network, config dumps or state changes. Runtime fixtures: `tests/test-tower-health.py`, invoked by `tests/test-tower-health.sh`
- `bazzite-tower-boot-log-label` — fixed `/var/log/boot.log` restorecon preflight for logrotate only; existing regular root-owned single-link target, no symlinks/recursion/creation; current installed policy supplies the context
- `bazzite-tower-opensnitch-readiness` — read-only selected-profile dispatcher; legacy retains the fixed release hash/user-service/TCP listener check. System dispatch requires an explicit root invocation of `bazzite-tower-snitchwatch-readiness` for provenance, service hardening, socket/token DAC and the shipped allow/proc policy; deny passes only as a confirmed `snitchwatch-deny` opt-in, other deny overrides fail readiness. Neither proves GUI decisions
- `bazzite-tower-opensnitch-ebpf-check` — read-only regular-file/ELF64 relocatable BPF header check for all three pinned RPM objects; build and smoke packaging gate, no kernel-load claim
- `bazzite-tower-snitchwatch-resources` — finite reporting-only Python sampler; default daemon/bridge MainPIDs plus optional GUI PIDs, interval CPU core percentages, RSS/threads, PID/starttime changes and missing/permission statuses; no traffic, command-line or environment reads. Offline fixtures: `tests/test-snitchwatch-resources.py`; owner A/B procedure in RUNBOOK
- `bazzite-tower-snitchwatch-migrate` — explicit `check|apply|rollback` transaction; detects legacy conflicts and drift, records baseline state and supports restoration without changing fail-open policy
- `bazzite-tower-snitchwatch-deny` — root `status [--json]|on [--revert-after N]|confirm|off|revert-if-pending [--boot]|publish-status`; system profile only, opt-in time-boxed `DefaultAction=deny` (single-key edit, exact backup in `/var/lib/bazzite-tower/snitchwatch-deny/`, transient revert timer, marker `/etc/bazzite-tower/snitchwatch-deny-optin`). `bazzite-tower-snitchwatch-deny-guard.service` (enabled by `build.d/96-snitchwatch-deny-guard.sh`) reverts a pending trial before `opensnitch.service` (an `opensnitch.service.d` drop-in makes it `Requires=`/`After=` the guard); common.py readiness accepts deny only with a confirmed marker. Image still ships allow. After each change it publishes `/run/snitchwatch/deny-status.json` (status schema 1 + `updatedAt`,`bootId`; GUI reads via the existing `:ro` mount) and `bazzite-tower-snitchwatch-deny-status.service` (non-fatal oneshot, not a firewall dependency) regenerates it at boot. Tests: `tests/test-snitchwatch-deny.py`
- `docker-libvirt-forwarding` — Docker `ExecStartPost` helper: takes a bounded-wait `/run` lock, discovers only active libvirt XML NAT bridges, derives each live IPv4 network and default uplink, and idempotently adds tagged `NEW,ESTABLISHED,RELATED` / `RELATED,ESTABLISHED` `DOCKER-USER` pairs; removes only stale rules bearing its own prefix and fails if Docker did not create that chain
- `bazzite-tower-power-tuning` — write platform_profile + per-CPU EPP; skips absent/read-only knobs
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
`/usr/lib/tmpfiles.d/snitchwatch.conf`, the daemon drop-in
`/usr/lib/systemd/system/opensnitch.service.d/snitchwatch-system-bridge.conf`
(r15; replaces the tower's former `20-system-bridge.conf`) and three shipped
opensnitchd rules: upstream's `/etc/opensnitchd/rules/000-snitchwatch-bridge-fetch.json`
(bridge HTTPS blocklist fetch; pinned in build-pins) and the tower's always-on
`/etc/opensnitchd/rules/000-snitchwatch-tower-os-update.json` (r15, "always update os": allow
`/usr/bin/skopeo`/`/usr/bin/rpm-ostree` as uid 0 or 65534 to TCP 443,
`precedence: false`; source `build_files/firewall/snitchwatch-system-os-update-rule.json`,
digest pinned in the verifier) and its companion
`/etc/opensnitchd/rules/000-snitchwatch-tower-resolved-dns.json` (allow
`/usr/lib/systemd/systemd-resolved` as a system uid 0–999 to port 53/853,
UDP/TCP; source `snitchwatch-system-resolved-dns-rule.json`, digest pinned).
They are the only `/etc` files the manifest
verifier reads, so a local edit or delete fails verification. System profile only. Accounts are named `snitchwatch`
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

The r16.2 image also stages the two scanner executables, session D-Bus
activation, polkit action and private-state tmpfiles definition from the native
factory. No scanner service or timer is installed. Readiness permits exactly
`org.snitchwatch.Scanner=talk` only when the verified image's expected Flatpak
finishArgs grant that name; owning the name, suffixes and wildcards still refuse.
The immutable scanner source/build/license inventory is described in
`image-build.md`; runtime scanner acceptance remains owner-operated.

Opt-in endpoint events (Vigie): `/usr/share/snitchwatch/endpoint-events/` holds the
four pinned upstream files (`.conf` template, `.nft`, `.service`, `-nft.service`),
root:root 0644, in no unit path and enabled nowhere; packages come from
`build.d/64-endpoint-events-packages.sh` with all their units disabled. No listener
on 514/6514 and no daemon `Loggers` entry by default; see `docs/RUNBOOK.md`.

Readiness (and migrate/deny) run helpers only by the absolute paths in `COMMANDS` in `bazzite-tower-snitchwatch-common.py` (`/usr/bin/{flatpak,systemctl,ss,busctl,getent,python3,systemd-run}`) with a fixed env (`PATH=/usr/sbin:/usr/bin`, `LC_ALL=C`); the caller's PATH/`LD_*`/`PYTHON*` are never used, and a missing helper refuses with `NOT READY: required command missing`. Unparseable Flatpak metadata (e.g. duplicate `sockets=`) also refuses cleanly.

Readiness retains the historical exact `wayland;fallback-x11` socket profile.
The serialized `x11;wayland;fallback-x11` form is accepted only when the host
`flatpak --version` reports one of Flatpak 1.18.1 to 1.18.4, whose
upstream socket evaluator (diffed across those tags; unchanged in behaviour)
makes X11 conditional on `!has-wayland` with fallback-X11. Later versions need
a fresh review before the range is widened.
Unknown, ambiguous or failed version detection refuses that exception; other
socket sets and all existing network, device, filesystem, persistence, IPC
environment, bus and override checks retain their refusal rules. The actual
installed branch metadata fixture and behavioral cases live in
`tests/fixtures/r13-installed-branch-metadata.json` and
`tests/test-snitchwatch-system.py`. These offline checks do not establish actual
Wayland/X11 sandbox authority or runtime/release acceptance.

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
- `/usr/share/bazzite-tower/opensnitchd-default-config.json` → Snitchwatch-tuned opensnitchd config. **Staged, not live**: `95-firewall.sh` `install`s it over `/etc/opensnitchd/default-config.json` *after* the OpenSnitch RPM extraction (which writes that path itself), so it can't live at the real path here. Doubles as the pristine image-intent copy to diff a 3-way-merged `/etc` against. Deltas from the RPM default: `Server.Address` `127.0.0.1:50051` in legacy or `unix:opensnitchd.sock` in the opt-in system candidate, `ProcMonitorMethod` `proc` (tested baseline; packaged eBPF objects need owner runtime validation), `DefaultAction` `allow` (permanent default in both profiles; deny enforcement requires explicit operator opt-in). System candidate `opensnitchd-system-bridge-config.json` retains allow/proc; migration retains allow and readiness rejects local deny drift

## Diagnostic collection preset

`/usr/lib/systemd/system-preset/00-bazzite-tower-diagnostics.preset` disables
sysstat.service, sysstat-collect/summary/rotate.timer and trace-cmd.service for
fresh-image preset processing (first matching preset wins; `/etc` owner policy
can override). Build stage70 also explicitly disables these units and sets the
packaged sysstat.sleep hook0644; vendor bytes remain intact but suspend annotations
stay off even after a timer opt-in. No tracing or GPU tests are automatically run.
