# Runbook

Operational procedures for the `bazzite-tower` image. There is no server to deploy —
the "deployment" is a bootc image the target machine rebases onto. Day-2 operations
are install, update, **rollback**, health-check, triage, and the CI publish pipeline.

Image: `ghcr.io/bearyjd/bazzite-tower:latest` · signed with `cosign.pub`.

## Install / switch

```bash
sudo ./scripts/install-signature-policy.sh
sudo bootc switch --enforce-container-sigpolicy ghcr.io/bearyjd/bazzite-tower:latest
sudo systemctl reboot
```

From any bootc host (Bazzite, Bluefin, Aurora, Silverblue, Fedora Atomic), run
the bootstrap helper from a trusted checkout before the first switch. It makes
the global default reject, installs this repository's more-specific sigstore
rule, and preserves unrelated explicit host rules. An empty Docker-transport
compatibility fallback still permits unrelated Docker/Podman pulls; it cannot
override this repository's specific rule. An image cannot securely enforce a
policy that is first introduced by that same image. See
[README "Installing"](../README.md#installing).

## Update

Bazzite applies image updates in the background. Force one immediately with:

```bash
sudo bootc upgrade
sudo systemctl reboot
```

`:latest` rebuilds weekly (Sun 06:00 UTC) and on every push to `main`.

## Rollback (do this first when a boot goes bad)

```bash
sudo bootc rollback        # stage the previous deployment
sudo systemctl reboot
```

Or pick the previous entry in the GRUB boot menu at power-on. To **freeze** on a
known-good build instead of tracking `:latest`, switch to a date-stamped tag:

```bash
sudo bootc switch --enforce-container-sigpolicy ghcr.io/bearyjd/bazzite-tower:latest.YYYYMMDD
```

Tag scheme (`latest`, `latest.YYYYMMDD`, `YYYYMMDD`, `<short-sha>`):
[README "Tags"](../README.md#tags). A second, opt-in `:latest-kernel` variant
also exists, tracking upstream `bazzite-nvidia-open:stable`'s current kernel.
The Meteor Lake cx0 DPLL s2idle-resume regression that once made this variant
risky is now fixed upstream (mainline 7.2, commit `062499cc4813b5a3`) and
`:latest` itself moved onto a kernel carrying the fix on 2026-08-28 — see
`Containerfile`'s header comment and
`docs/research/i915-bug-report/UPSTREAM-FIX-STATUS-2026-08-28.md` for the full
chain of reasoning. `:latest-kernel` is still worth checking against that doc
before switching, since it floats independently and could in principle regress
again on a future kernel bump.

## Health checks

| Check | Command |
|---|---|
| Current/rollback deployment, signature | `bootc status` |
| Suspend mode actually in effect | `cat /sys/power/mem_sleep` (bracketed entry is active; expect `[s2idle]`) |
| Audio: SOF bypass active | `journalctl -k -b 0 \| grep -i dsp_driver` and `grep -c "FW reported error: 9"` should be 0; `cat /proc/asound/cards` shows the HDA card |
| CPU MCE / RAS summary | `sudo ras-mc-ctl --summary` · `sudo ras-mc-ctl --errors` |
| Running CPU microcode revision | `grep -m1 microcode /proc/cpuinfo` (and `journalctl -k \| grep -i microcode`) |
| Tracked kernel args applied (no dupes) | `cat /proc/cmdline` — expect each tracked karg exactly once (IOMMU, `kvmfr.static_size_mb=128`, `vfio_pci.disable_vga=1`, `kvm.ignore_msrs=1`, `nvme_core.default_ps_max_latency_us=0`) |
| NVMe SMART health / self-tests | `sudo smartctl -H /dev/nvme0` · `/dev/nvme1`; smartd warnings + scheduled tests: `journalctl -u smartd` |
| Swappiness in effect | `sysctl vm.swappiness` (expect `10`) |
| Indexer excludes applied | `balooctl6 config show excludeFilters` (expect `.gradle`, build/cache dirs) |
| CPU power baseline | `cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference` (expect `balance_performance`); `cat /sys/firmware/acpi/platform_profile` (expect `balanced`); `systemctl is-active thermald` |
| Virt stack up | `systemctl is-active virtqemud.socket` · `virsh -c qemu:///system list --all` |
| Read-only image/host summary | `ujust tower-health` — booted/staged identity, running versions, failed services, RAS evidence and SELinux labels; `ujust tower-health --diagnostics` adds bounded current-boot/resume excerpts |
| OpenSnitch legacy bridge readiness | `ujust opensnitch-readiness` — read-only check of the active daemon, fail-open config and verified user bridge at `127.0.0.1:50051`; see the OpenSnitch section below |
| Cockpit web management (opt-in) | `ujust enable-cockpit`; it binds only to loopback. Review Tailnet ACLs, then separately run `tailscale serve --https=443 http://127.0.0.1:9090`; do not expose :9090 directly without explicit firewall policy. |
| Docker/libvirt NAT forwarding (opt-in Docker) | `just test-docker-libvirt-forwarding` (from a repository checkout) — restarts Docker, checks its `DOCKER-USER` NAT-bridge/uplink pair rules, then probes Docker bridge networking. Rules are only for active libvirt XML `forward mode='nat'` bridges; no broad source-RFC1918 or bridge-wildcard policy is added. A post-start reconciliation failure is non-fatal to Docker and later libvirt/NetworkManager events retry it. |
| Looking Glass client | kvmfr host module is baked (`ls /dev/kvmfr0`); install the version-coupled client on demand with `ujust install-looking-glass-client`, then `looking-glass-client` (match its B-version to the Windows host app) |
| Default NAT network | `ujust vm-net-status` |
| Wi-Fi diagnostics (offline) | `ujust wifi-debug` |

**Docker/libvirt forwarding on libvirt network changes (manual, real hardware).**
Reconciliation is triggered by the systemd path unit `docker-libvirt-forwarding.path`,
not a libvirt network hook: libvirt's confined `virtnetworkd_t` cannot exec hooks
under enforcing SELinux, and a failing hook aborts every network start. The path
unit fires on *any* change in libvirt's network state directory
(`/run/libvirt/network`), not only start/stop; `docker-libvirt-forwarding.timer`
also re-runs it every ~10 minutes as a self-heal. Stale rules for a stopped network
are harmless and are pruned at the next run. To verify, with Docker running:

1. `systemctl is-active docker-libvirt-forwarding.path` (expect `active`) and
   `sudo ls /etc/libvirt/hooks/` — a leftover or locally modified `network` or
   `network.disabled` hook survives the `/etc` 3-way merge and would keep breaking
   network start; delete it.
2. `sudo virsh net-list --all`; pick a NAT network `<name>`.
3. `sudo virsh net-start <name>` (or `net-destroy` then `net-start` if active), wait ~4s,
   then `sudo iptables -S DOCKER-USER | grep bazzite-tower-libvirt-forwarding:` should
   show the pair for its bridge.
4. `sudo virsh net-destroy <name>`, wait ~4s; those rules should be gone.
5. `sudo ausearch -m avc -ts recent | grep -i virt` — expect nothing about the helper.

Logs: `journalctl -u docker-libvirt-forwarding.service -b`. An existing machine may
have a leftover `/etc/libvirt/hooks/network.disabled` from the earlier workaround.

Known limits:
- The default lock is the helper script file itself, so any local user who can open it
  can hold the lock (equivalent to the old `/run` lock file).
- The service is not sandboxed; hardening directives need real-host testing
  (`NoNewPrivileges` may break SELinux transitions).
- The service shows `failed` when offline (the helper exits 1 with no default route).

**Optional diagnostic packages (r16.1 source changes; not yet built or installed).**
All four requested Fedora packages are added to the image recipe: `bpftool`,
`sysstat`, `igt-gpu-tools`, and `trace-cmd`. Package availability is confirmed in
Fedora 44 metadata; installation, dependency resolution on this image and actual
GPU/BPF/tracing operation remain unverified until the new image is built and
accepted. These tools are not invoked by `tower-health` or CI GPU tests. Automatic
activation defaults are described below.

After installation, owner-invoked examples are `sudo bpftool prog show` (inspect
loaded BPF programs), `iostat -xz 1 10` or `pidstat -u -r 1 10` (finite samples),
`sudo timeout 10s intel_gpu_top` (Intel GPU activity), and `trace-cmd list -e`
(list trace events). Tool availability does not establish kernel support or grant
permission. GPU test runners/register writes and trace recordings require a
separately chosen owner procedure; none runs automatically.

Fresh images disable `sysstat.service`, `sysstat-collect.timer`,
`sysstat-summary.timer`, `sysstat-rotate.timer`, and `trace-cmd.service`, with an
early vendor disable preset and explicit build-time disable. Existing owner
`/etc` enablement can survive a bootc update; inspect with `systemctl is-enabled`
and `systemctl is-active` before assuming collection is off on an existing host.
The trace-cmd udev rule only reloads its already-active service and is retained.

Fedora's `sysstat.sleep` otherwise writes a data record unconditionally on suspend
and resume, regardless of the disabled services. The build retains its contents
but deliberately sets mode `0644`, so systemd-sleep skips it. This expected RPM
mode override also leaves sleep annotations disabled if the owner later opts in
to sysstat timers; manual `sar`, `iostat`, and `pidstat` still work. To explicitly
opt in to periodic collection after accepting the image, the owner can enable
`sysstat.service` plus its collect/summary/rotate timers. To turn it off again,
disable and stop all four units. The image does neither on a running host.

**One-shot sweep:** [`scripts/tower-diagnostic.sh`](../scripts/tower-diagnostic.sh)
runs all of the above (SOF/ABI, MCE/RAS, i915 resume, thermals, SMART, rpm-ostree)
in one pass. Run with `sudo` for the root-only checks:
`sudo ./scripts/tower-diagnostic.sh`.

**Display glitch that leaves no log trace:**
[`scripts/i915-drm-debug-capture.sh`](../scripts/i915-drm-debug-capture.sh) raises
`drm.debug` at runtime (no reboot) and extracts the journal around the moment a
glitch was *seen*. Some display symptoms — the 2026-09-07 flicker being the worked
example — produce zero i915/nvidia errors at default verbosity, so this is the only
way to see the pipeline at the moment it happens:

```bash
sudo ./scripts/i915-drm-debug-capture.sh on          # default mask 0x104 (KMS+DP)
# ...use the machine; note the wall-clock time when you SEE the glitch...
sudo ./scripts/i915-drm-debug-capture.sh grab 23:26  # writes a +/-60s capture
sudo ./scripts/i915-drm-debug-capture.sh off         # always turn it back off
```

Costs ~17 MB of journal per day at the default mask (measured), against the 4G cap
in `90-tower-journal-cap.conf` — safe to leave on for days while waiting to catch
one. Do **not** add the VBL bit (`0x20`): the panel runs at 165 Hz and would emit
~165 lines/second. See
[docs/research/nvidia-modeset-head-flicker-2026-09-07.md](./research/nvidia-modeset-head-flicker-2026-09-07.md).

CI mirrors these: `tests/smoke.sh` (offline, the gate) and `tests/boot-check.sh`
(runtime). See [docs/CODEMAPS/ci-cd.md](./CODEMAPS/ci-cd.md).

## Intel CSME / ME firmware

Carries the PCODE fix for the i915 GuC stall (see the Wi-Fi/GuC sections above).

```bash
cat /sys/class/mei/mei0/fw_ver     # want >= 18.1.18.2644
fwupdmgr get-updates               # ME appears as "Intel Management Engine"
```

Known-bad: `18.0.5.2141` (factory), `18.0.15.2515` (insufficient).
Known-good: `18.1.18.2644` and above.

## OpenSnitch / Snitchwatch deployment status

The default image selects the legacy TCP user bridge at `127.0.0.1:50051`.
`SNITCHWATCH_BRIDGE=system` is an explicit image build opt-in and requires
`FIREWALL_DAEMON=opensnitch`. Both profiles retain `ProcMonitorMethod: proc`
(the tested baseline; historical eBPF load errors do not establish kernel
incompatibility). Both profiles permanently
ship `DefaultAction: allow`; deny enforcement always requires explicit operator
opt-in. Unanswered unruled connections use allow, while explicit rules still
apply. `DropWhileAsking` remains absent (default false); opting into it can drop
traffic while a prompt is pending even with allow. The candidate supplies native system bridge version 0.1.1,
its two socket units, named system accounts and immutable artifact provenance.
The candidate also supplies the reviewed OpenSnitch 1.8.0 shutdown/NFT repair
with its own `/usr/share/snitchwatch/system-daemon-manifest.json`;
`/usr/libexec/snitchwatch/verify-system-daemon.py --root /` checks the actual
installed `/usr/bin/opensnitchd` against the source, patch and license inventory;
OpenSnitch uses a relative Unix socket from `/run/snitchwatch`, via upstream's
drop-in `/usr/lib/systemd/system/opensnitch.service.d/snitchwatch-system-bridge.conf`
(`Wants=`/`After=` the gRPC socket, `WorkingDirectory=-/run/snitchwatch`).

Build the local candidate with `just build-snitchwatch-system`. Use
`just build-snitchwatch-system-vm` only for a disposable VM: it additionally
enables SSH socket activation. Boot that image with
`just run-vm-ssh localhost/bazzite-tower snitchwatch-system-vm <unused-port>`.
This does not install a GUI or grant any user the GUI group. Install the reviewed
system-profile Flatpak per user and inspect existing same-ID installations and
overrides before selecting it.

The daemon build toolchain (Go, protoc, libnetfilter_queue) is installed from
pinned, Fedora-signed Koji RPMs (`toolchainRpms` in
`build_files/firewall/snitchwatch-system-daemon-pins.json`), not live repos, so
Fedora repo updates cannot change the Go, protoc and libnetfilter_queue RPMs (7 pinned, signed). Phase 2 also pins the bridge's rust, cargo and rust-std-static 1.99.0-1.fc44 as three signed Koji RPMs in `build_files/firewall/snitchwatch-system-pins.json`. The shared `snitchwatch-system-toolchain.sh` helper runs after the byte-identical upstream `install-deps.sh` and before the Rust version gate. Remaining live toolchain and transitive dependencies, including LLVM, gcc, binutils, libnfnetlink-devel, pkgconf, git and python3, are not fully hermetic. `just test-snitchwatch-daemon-patch` uses the same daemon RPMs (helper `fetch` mode). Koji is therefore a build-time
dependency: if `kojipkgs.fedoraproject.org` is unreachable the build fails
closed. To re-pin: download each RPM from
`https://kojipkgs.fedoraproject.org/packages/<name>/<ver>/<rel>/data/signed/6d9f90a6/<arch>/`
(never the unsigned `/packages/.../<arch>/` copy), confirm `rpm -K` reports
`digests signatures OK` against `/etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-44-primary`,
record sha256/size/NEVRA in the respective bridge or daemon pins file, update
`rustVersion` or `goVersion`/`protocVersion`/`libnetfilterQueueVersion` to match,
then update `EXPECTED_PINS_SHA256` in the corresponding
`snitchwatch-system-verify.py` or `snitchwatch-system-daemon-verify.py` and rerun
the relevant toolchain and verifier tests.

Run `ujust opensnitch-readiness` for the image's selected profile. The legacy
profile retains the [release-install contract](../README.md#opensnitch-application-firewall).
System readiness requires an explicit privileged read for cross-account
process inspection: run `sudo ujust snitchwatch-system-readiness`.
It verifies immutable provenance, service/socket ownership,
permissions, token access, resolved service hardening and the shipped
`allow`/`proc` policy. An explicit local deny override fails this readiness
contract as policy drift; readiness does not enable enforcement.
It is read-only and cannot prove a GUI answers prompts. Migration and rollback
must preserve the recorded legacy baseline and reject unexpected live `/etc`
policy/address drift or user-unit/Flatpak overrides. Do not activate the system
profile while a legacy bridge process or listener remains. Use
`ujust snitchwatch-system-migrate check` to inspect the current deployment,
`ujust snitchwatch-system-migrate apply` for the explicit transaction (it
publishes the system reference config, retaining
`DefaultAction: allow` while switching the transport), and
`ujust snitchwatch-system-migrate rollback` to restore its recorded baseline.
The migration journal is protected at
`/var/lib/bazzite-tower/snitchwatch-migration/current.json`; preserve it until
rollback is no longer needed. These migration recipes request sudo explicitly.

### Opt-in deny on the system profile (`ujust snitchwatch-deny`)

The image ships `DefaultAction: allow`; deny is never the default. On the
**system profile only** (`Server.Address` = `unix:opensnitchd.sock`; the legacy
TCP profile is refused, issue #35) an operator can try deny with automatic
recovery. Helper: `/usr/libexec/bazzite-tower-snitchwatch-deny` (root).

1. `ujust snitchwatch-deny status` - confirms preconditions (`opensnitch.service`
   active, root-only bridge socket present, root-owned config with
   `allow`/`proc`). Machine-readable:
   `sudo /usr/libexec/bazzite-tower-snitchwatch-deny status --json` returns
   `{"schema":1,"profile","defaultAction","optIn","pending","expiresAt","canEnable","blockers"}`.
2. `ujust snitchwatch-deny on 10` - keeps an exact backup of the config under
   `/var/lib/bazzite-tower/snitchwatch-deny/`, changes only the `DefaultAction`
   value, restarts `opensnitch.service` and arms a transient timer
   (`bazzite-tower-snitchwatch-deny-revert`). Any failure after the write
   restores the config (fail-open).
3. Inside the window, verify a Snitchwatch prompt can be answered and the network
   works, then `ujust snitchwatch-deny confirm`. This writes
   `/etc/bazzite-tower/snitchwatch-deny-optin` and deny persists across reboots.
   `sudo /usr/libexec/bazzite-tower-snitchwatch-readiness` then reports
   `deny (operator opt-in)`.
4. `ujust snitchwatch-deny off` - restores `allow` (exact backup if untouched,
   otherwise only the one key) and removes the marker.

Recovery: an unconfirmed trial reverts at its deadline, and
`bazzite-tower-snitchwatch-deny-guard.service` reverts it at boot before
`opensnitch.service` starts. If the network is already unusable: wait out the
timer, or from a TTY run `sudo /usr/libexec/bazzite-tower-snitchwatch-deny off`
(or `revert-if-pending`), or `rpm-ostree rollback`. A lock held by another policy
writer (`/run/lock/bazzite-tower-snitchwatch-migration.lock`) makes every
mutating subcommand refuse. Readiness treats pending deny, or deny without a
valid marker, as drift. Not yet exercised on real hardware/systemd.

### OpenSnitch resource baseline and owner eBPF experiment

This is an optional **owner-operated** test on the candidate, after its initial
`allow`/`proc` boot passes `sudo ujust snitchwatch-system-readiness` and the full
`tests/boot-check.sh` gate. Agents do not edit host configuration, invoke sudo,
restart services or reboot. No kernel, security or probe workarounds belong in
this experiment. A past `unable to load eBPF module` message does not identify
its cause. The RPM supplies `/usr/lib/opensnitchd/ebpf/opensnitch.o`,
`opensnitch-procs.o` and `opensnitch-dns.o`; build/smoke verifies their ELF BPF
headers, while the native daemon executable itself does not embed them.

Use the same workload, GUI, power state and observation lengths in each mode.
Allow two minutes of warmup, sample ten minutes idle, then ten minutes of a
repeatable normal workload. The reporting-only sampler needs only Python's
standard library and reads MainPID and `/proc/PID/stat`; it collects no network
payloads, connection details, command lines, environment or tokens. Optional
`--pid gui=PID` includes a GUI process selected by the owner (reacquire after a
GUI restart). Run each window with the GUI/bridge mode held constant:

```bash
# Run as the owner. sudo permits reading cross-account proc entries only.
sudo /usr/libexec/bazzite-tower-snitchwatch-resources --duration 600 --interval 5 > proc-idle.json
sudo /usr/libexec/bazzite-tower-snitchwatch-resources --duration 600 --interval 5 > proc-workload.json
```

Repeat as `ebpf-idle.json` and `ebpf-workload.json` only after the eBPF startup
checks below succeed. JSON includes each PID/start tick, monotonic sample time,
interval CPU percentage (100% = one fully busy core; can exceed 100%), RSS and
threads, plus CPU median/p95, RSS initial/final/peak and observed restart count.
CPU is the change in utime+stime divided by actual elapsed monotonic time and
clock ticks, never lifetime CPU. Compare matching windows and retain raw series;
unavailable/permission-denied samples and PID reuse/restarts invalidate an
unbroken comparison. Samples spanning a missing process or identity change
have no CPU percentage. Short-lived restarts between observations may be missed;
extra child processes are outside the sampled PID's accounting. No automatic
resource thresholds or performance claims follow from the report.

Before editing, stop other policy writers. Run the complete block below from
an owner terminal. A root shell holds the migration-lock descriptor while
waiting for the owner subprocess to finish recovery. Its signal handlers preserve that lock
through terminal interrupts. The editor and experiment shell run as
the owner; `sudoedit` retains the selected owner editor. Every failure before
editing aborts. After the verified private backup exists, EXIT/HUP/INT/TERM
restore proc automatically, including editor failure and Ctrl-C. The trap is
registered **before** the first edit. Do not run individual lines separately.

```bash
sudo bash -c '
set -euo pipefail
exec 9>/run/lock/bazzite-tower-snitchwatch-migration.lock
flock -n 9
trap ":" HUP INT TERM
sudo -H -u "$1" -- env SUDO_EDITOR="$2" bash -s
' -- "$USER" "${SUDO_EDITOR:-${VISUAL:-${EDITOR:-vi}}}" <<'EBPF_EXPERIMENT'
set -Eeuo pipefail
umask 077
config=/etc/opensnitchd/default-config.json
log=/var/log/opensnitchd.log
sudo jq -e '.ProcMonitorMethod == "proc" and .DefaultAction == "allow" and .Server.Address == "unix:opensnitchd.sock"' "$config" >/dev/null
backup_dir="$(sudo mktemp -d /var/lib/bazzite-tower/snitchwatch-ebpf.XXXXXX)"
[ "$(sudo stat -c '%u:%g:%a' "$backup_dir")" = 0:0:700 ]
sudo cp --preserve=all -- "$config" "$backup_dir/proc.json"
sudo cmp -- "$config" "$backup_dir/proc.json"
original_metadata="$(sudo stat -c '%u:%g:%a:%C' "$config")"
[ "$(sudo stat -c '%u:%g:%a:%C' "$backup_dir/proc.json")" = "$original_metadata" ]
printf 'Protected restore directory: %s\n' "$backup_dir"

restore_proc() {
    # Explicit guards: never restart after a failed copy or failed validation.
    sudo cp --preserve=all -- "$backup_dir/proc.json" "$config" || return 1
    sudo cmp -- "$backup_dir/proc.json" "$config" || return 1
    local restored_metadata
    restored_metadata="$(sudo stat -c '%u:%g:%a:%C' "$config")" || return 1
    [ "$restored_metadata" = "$original_metadata" ] || return 1
    sudo jq -e '.ProcMonitorMethod == "proc" and .DefaultAction == "allow" and .Server.Address == "unix:opensnitchd.sock"' "$config" >/dev/null || return 1
    sudo systemctl restart opensnitch.service || return 1
    sudo ujust snitchwatch-system-readiness || return 1
    printf 'Restored exact proc config, metadata and service readiness.\n'
}
recover() {
    local experiment_status=$?
    trap - EXIT
    trap '' HUP INT TERM
    set +e
    until restore_proc; do
        printf 'RECOVERY FAILED; migration lock retained. Backup: %s/proc.json\n' "$backup_dir" >&2
        printf 'Repair the failure in another terminal; press Enter to retry. Do not kill this session.\n' >&2
        read -r _ </dev/tty || sleep 60
    done
    exit "$experiment_status"
}
trap recover EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Change only ProcMonitorMethod from proc to ebpf; preserve every other key.
sudoedit "$config" </dev/tty
sudo python3 - "$backup_dir/proc.json" "$config" <<'CHECK_CONFIG'
import json, sys
before, after = [json.load(open(path)) for path in sys.argv[1:]]
before['ProcMonitorMethod'] = 'ebpf'
assert after == before, 'Only ProcMonitorMethod may change'
CHECK_CONFIG
log_inode="$(sudo stat -c %i "$log")"
log_bytes="$(sudo stat -c %s "$log")"
ebpf_log="$(mktemp ./ebpf-experiment.XXXXXX.log)"
printf 'New restart log interval: %s\n' "$ebpf_log"
sudo systemctl restart opensnitch.service
sudo systemctl is-active --quiet opensnitch.service

check_interval() {
    [ "$(sudo stat -c %i "$log")" = "$log_inode" ] || return 1
    [ "$(sudo stat -c %s "$log")" -ge "$log_bytes" ] || return 1
    sudo tail -c "+$((log_bytes + 1))" "$log" > "$ebpf_log" || return 1
    grep -Fq '[eBPF] module loaded: /usr/lib/opensnitchd/ebpf/opensnitch.o' "$ebpf_log" || return 1
    grep -Fq '[eBPF] module loaded: /usr/lib/opensnitchd/ebpf/opensnitch-procs.o' "$ebpf_log" || return 1
    grep -Fq 'Process monitor method ebpf' "$ebpf_log" || return 1
    if grep -Ei 'unable to load eBPF module|opensnitch-procs.o not available|error starting ebpf monitor method|module collection.*error|opening kprobe|opening kretprobe|tracepoint.*(fail|error|missing|not found)|(fail|error|missing).*tracepoint|Process monitor method (/?proc|audit|ftrace)' "$ebpf_log"; then
        return 1
    fi
}
sleep 120  # Same two-minute warmup as proc, then validate both loaded modules.
check_interval
cat "$ebpf_log"
printf 'Inspect the new interval for any other fallback/probe errors. Type validated to sample: '
read -r answer </dev/tty
[ "$answer" = validated ]
sudo /usr/libexec/bazzite-tower-snitchwatch-resources --duration 600 --interval 5 > ebpf-idle.json
printf 'Prepare the same workload used for proc; type ready to start: '
read -r answer </dev/tty
[ "$answer" = ready ]
sudo /usr/libexec/bazzite-tower-snitchwatch-resources --duration 600 --interval 5 > ebpf-workload.json
check_interval  # Includes late workload errors, rotation and truncation.
cat "$ebpf_log"
# End of block triggers the EXIT trap and restores proc before the root parent releases its lock.
EBPF_EXPERIMENT
```

Missing logs, log rotation/truncation, absent successes, fallback, module load
errors, relevant kprobe/kretprobe warnings or missing syscall/scheduler
tracepoints abort and trigger restoration. Inspect the complete interval for
other diagnostic wording; the pattern check cannot recognize every error.
An active service or just the `opensnitch.o` success is insufficient. The DNS
object's packaging check does not replace the two required module successes.
Full system readiness **must refuse** the local eBPF override: its contract is
the shipped allow/proc policy. Do not loosen readiness for this experiment.

The recovery trap retries only after owner acknowledgement (or every 60 seconds
if its terminal vanished) and keeps the lock held until copy, byte comparison,
metadata/policy checks, service restart and readiness all succeed. A failed
copy/validation never proceeds to restart. Do not close/kill a recovery session;
repair its reported failure from another owner terminal and retry. SIGKILL,
power loss and a reboot cannot be trapped: use the printed protected backup
path to restore with `sudo cp --preserve=all -- BACKUP/proc.json
/etc/opensnitchd/default-config.json`, compare bytes/metadata, validate proc/allow,
restart OpenSnitch and run readiness **before** resuming normal operations.
After successful proc recovery and block completion, run the same full `tests/boot-check.sh` gate used
before the experiment. Preserve its result alongside the resource/log reports.

Retain the protected backup and reports. `cp --preserve=all` restores original
ownership, permissions and security context; do not relax DAC to make backup
files readable. The full boot gate's existing whole-boot eBPF error assertion
remains strict. If an experiment failed and left a boot-journal error, report
that exact experiment interval separately alongside restored-proc checks;
do not call the full gate clean or erase the evidence. A subsequent clean
proc boot is an owner action, never an agent action.

Policy writers must serialize edits with the migration helper using
`/run/lock/bazzite-tower-snitchwatch-migration.lock`. Linux filesystem exchange
cannot conditionally replace a file against arbitrary uncoordinated root
writers. The helper verifies the displaced config's bytes and security metadata
before activation. If they changed, it retains that inode under the root-owned
mode-0700 migration journal directory, records `recovery-required`, and leaves
OpenSnitch stopped. Automatic rollback refuses that retained unexpected config;
review the journal and preserved file manually before restoring service.

The [validation report](research/snitchwatch-system-bridge.md) preserves the
October 4–5 artifact tests separately from fresh target-image validation.
The clean GUI release build on supported KDE 6.11 / Qt 6.11.2 and native
bridge 0.1.1 reproducibility passed at source `5c2b44a`. The GUI crate remains
0.1.0 and ships a separate full source/license archive. On October 6–7 a fresh
enforcing-SELinux VM of the candidate built from `d96a7a5` passed 15
consecutive cold boots with no stalled queue, controlled daemon stops, the
migration/refusal checks and default KDE GUI decisions (render, inline Allow,
bridge-restart reconnect, last-GUI fallback, unenrolled-user refusal). The same
gates caught and fixed a daemon queue-reader stall in the earlier `78be87b`
candidate. Keep production deployment and deny-policy changes behind the
remaining rollout gates in that report.

Operating the system candidate:

- OpenSnitch writes its main log to `/var/log/opensnitchd.log`; the journal
  carries only its raw stderr lines.
- If a netfilter queue reader stops while its queue is still bound, the daemon
  logs `netfilter queue reader N stopped: …`, leaves through its normal
  cleanup and exits 1. systemd restarts it after `RestartSec=30`; until then
  `QueueBypass` lets traffic through unfiltered whatever the
  `DefaultAction`. Verified on the r4 VM (2026-10-08 UTC) by closing
  the queue socket: the daemon exited 1 within 0.4 s and restarted 30 s
  later; 44 of 45 test requests during the gap succeeded.
- Before the 22-file and later daemon patches, a daemon stop during reboot could log
  `Queue.destroy() idx=1, nfq_destroy_queue() not closed: -1`. The queue was
  unbound anyway; a stale verdict error confused libnfnetlink. The patch now
  drains pending replies first and logs the `errno` if destroy still fails
  ([investigation](research/opensnitch-nfq-destroy/README.md)).
- While one prompt is pending, opensnitchd applies the default action to other
  new connections without asking; the GUI banner says so. Answer pending
  prompts promptly.
- The GUI's inline Deny answers "this time" only: the daemon keeps no rule,
  so a retrying program is asked again, and while that prompt is pending
  other new connections get the default action (`allow` in both current
  profiles; deny requires explicit operator opt-in). To block, open the row and
  Deny with "Until quit" or "Forever" (verified on the r5 VM).

### r15.1 transition from an existing deny deployment

r15.1 restores the permanent allow default for both profiles. An existing locally
edited `/etc/opensnitchd/default-config.json` can retain `DefaultAction: deny`
through ostree's three-way `/etc` merge. An image upgrade alone therefore does
not guarantee this host uses allow. Inspect the live config after upgrading:

```bash
jq .Server.Address,.DefaultAction /etc/opensnitchd/default-config.json
```

To adopt the shipped policy, explicitly set `DefaultAction` to `allow` in that
file and restart `opensnitch.service`. Preserve any other intended local edits.
Then run `sudo ujust snitchwatch-system-readiness`; the system transport must
remain `unix:opensnitchd.sock`. Keeping deny is an explicit local enforcement
opt-in and fails the shipped allow readiness/migration contract as policy drift.
If a migration journal exists, changing the live policy also makes rollback
refuse to overwrite it; inspect the recorded baseline before recovery.

### Historical r15 system image: fail-closed upgrade and recovery

This section records the r15 deny default. Current images permanently ship
allow in both profiles; deny is an explicit local opt-in. On current images,
use the current migration/readiness contract above. The r15 instructions below
apply only to an existing r15 deployment.

**Before rebooting into an r15 `:snitchwatch-system` deployment** (ostree's `/etc`
3-way merge keeps a locally edited config, which would leave the old policy or
address in place):

```
sudo ostree admin config-diff | grep opensnitchd
jq .Server.Address /etc/opensnitchd/default-config.json
```

If `config-diff` lists `M etc/opensnitchd/default-config.json`, run
`sudo cp /usr/etc/opensnitchd/default-config.json /etc/opensnitchd/` so the merge
takes the new image's file. After the reboot `jq .Server.Address,.DefaultAction
/etc/opensnitchd/default-config.json` must print `"unix:opensnitchd.sock"` and
`"deny"` (otherwise repeat the `cp` and `sudo systemctl restart
opensnitch.service`).

After boot: `sudo ss -xlp | grep /run/snitchwatch/opensnitchd.sock` shows the
root-only (0600) gRPC listener held by systemd, `ss -tln | grep 127.0.0.1:50051`
prints nothing, and `sudo grep 'Error compiling list rule' /var/log/opensnitchd.log`
is empty.

**Recovery with fail-closed.** Keep a console (TTY) login handy. If the bridge
socket fails, `opensnitch.service` still starts (the drop-in only `Wants=` the
socket and its working directory may be missing) and, with no UI to ask, denies
every unruled outbound connection. Check `systemctl status
snitchwatch-system-bridge-grpc.socket snitchwatch-system-bridge.service`, then
`sudo systemctl restart snitchwatch-system-bridge-grpc.socket opensnitch.service`.
The bridge service has no start limit (`StartLimitIntervalSec=0`), so a crash
loop keeps retrying every 2 s instead of staying failed. If networking is still
blocked, back out in this order:

1. `ujust snitchwatch-system-migrate rollback` — if this host was moved to the
   system bridge with the migrate helper, it restores the journaled pre-migration
   config and unit state.
2. `sudo bootc rollback && sudo reboot` — boots the previous deployment.
3. Only then, as a last-resort local override, set `DefaultAction` to `allow` in
   `/etc/opensnitchd/default-config.json` from the TTY and restart
   `opensnitch.service`. This is local `/etc` drift that the readiness helper
   reports, and it makes `snitchwatch-system-migrate rollback` refuse ("live
   config changed after migration; rollback refuses to overwrite operator
   policy"), so try steps 1–2 first and restore the image copy
   (`/usr/share/bazzite-tower/opensnitchd-system-bridge-config.json`) afterwards.

**OS updates under fail-closed.** Owner decision 2026-10-17: OS image updates
must always work, GUI or not. The system profile therefore ships a second
reserved rule, `/etc/opensnitchd/rules/000-snitchwatch-tower-os-update.json` (allow,
`always`, `precedence: false`, so any matching deny or blocklist rule still
wins). It matches only when all four hold: process path `/usr/bin/skopeo` or
`/usr/bin/rpm-ostree`; uid 0 or 65534; destination port 443; TCP. Why those:
`bootc upgrade`/`switch` (also `uupd`, `bootc-fetch-apply-updates`) and
`rpm-ostree upgrade` pull through containers-image-proxy, which spawns
`skopeo experimental-image-proxy` — the registry connection is skopeo's, not
bootc's. bootc 1.16.9 runs it as root; rpm-ostree 2026.2's vendored ostree-ext
drops it to `nobody` (65534) via `setpriv` when `rpm-ostreed` runs under
systemd. `rpm-ostreed` itself (`ExecStart=+rpm-ostree start-daemon`, root)
fetches layered-package repo metadata in-process. No host match: ghcr.io
redirects blobs to `pkg-containers.githubusercontent.com`. Like the fetch rule,
its digest is pinned in `verify-system-manifest.py`, so editing or deleting it
fails verification.

Pulls also need name resolution, so a third reserved rule,
`/etc/opensnitchd/rules/000-snitchwatch-tower-resolved-dns.json`, lets the system resolver
always reach DNS: process path `/usr/lib/systemd/systemd-resolved`, a system
uid (0–999, regexp — `systemd-resolve` is allocated at build time: 990 on this
image, not the 193 `sysusers.d` asks for), destination port 53 or 853 (DNS,
DNS-over-TLS), UDP or TCP. A normal user (uid ≥ 1000) running the binary
themselves does not match, so it is not a DNS exfiltration channel for them;
any app can still tunnel data inside ordinary lookups through the resolver,
as with any DNS allow. Same pinning and `precedence: false`. It coexists with
a local `010-allow-systemd-resolved.json` and with Snitchwatch's opt-in
"dns-resolved" recommended rule (`snitchwatch-default-dns-resolved`): all are
allows, and the bridge only manages names under its own prefixes
(`snitchwatch-default-`, blocklist `z00-`/`900-`), so it neither flags nor
deletes the tower files.

Both tower rules are named under the bridge's reserved packaged prefix
`000-snitchwatch-`, so the bridge shows them read-only and refuses any GUI
change, toggle or delete of them (Snitchwatch 7bf3f19: `read_only_reason`,
`refuse_reserved_name`, `DaemonCommands::send`); it never rewrites or deletes
them itself. If one is edited or removed anyway (as root), the verifier and
smoke checks refuse; restore the image copy and restart the daemon:

    sudo install -m0644 -o root -g root /usr/etc/opensnitchd/rules/<name>.json /etc/opensnitchd/rules/
    sudo systemctl restart opensnitch.service

with `<name>` `000-snitchwatch-tower-os-update` or
`000-snitchwatch-tower-resolved-dns`. Accepted risk: these are
`precedence: false` allows, so a deny rule or blocklist that a GUI member adds
and that matches the same connection (for example a blocklist containing
`ghcr.io` or `pkg-containers.githubusercontent.com`, or a deny for
systemd-resolved) still wins and can block updates. That is by design: deny
always beats these allows.

## Common issues

| Symptom | Cause | Fix |
|---|---|---|
| Wi-Fi gone after a rebase | stale `wifi.backend=iwd` with iwd not enabled | the `wifi-backend-guard` service auto-recovers on boot; inspect with `ujust wifi-debug` |
| `virtqemud` won't start | upstream change dropped the `qemu` system user | rebuilt/guarded in `build.sh`; the smoke + boot tests catch regressions |
| Can't manage VMs as your user | user not yet in `kvm`/`libvirt` | `ujust fix-vm-groups`, then re-login (the first-boot oneshot adds the first user automatically) |
| Docker command cannot connect | Docker is intentionally disabled by default | Run `ujust enable-docker`, acknowledge that the Docker group is root-equivalent, then re-login |
| Libvirt NAT guests cannot reach the default uplink after Docker starts | Docker's `DOCKER-USER` chain was not created or the active NAT bridge lacks an IPv4 address | `sudo systemctl restart docker.service`; inspect `journalctl -u docker.service -b`; the post-start helper refuses to create a missing Docker chain but does not fail Docker itself. Changes in libvirt's network state directory (path unit) and the 10-minute self-heal timer and NetworkManager route/VPN/reapply events retry it. From a repository checkout, run `just test-docker-libvirt-forwarding` on a suitable host. |
| `docker.socket` fails at boot (`Failed to resolve group 'docker'`) | the `docker` group wasn't baked into the image (stale gshadow orphan made `systemd-sysusers` abort, so the group got created late) | `build.sh` now strips all shadow/gshadow orphans and bakes `groupadd -r docker`; the smoke test asserts the group exists |
| Display flicker / ~30s sluggish wake | i915 PSR/DC or `deep` suspend on Meteor Lake | baked kargs disable PSR/DC and pin `s2idle`; verify `cat /sys/power/mem_sleep` |
| `cat /proc/cmdline` shows `mem_sleep_default=deep` despite `20-suspend.toml` declaring `s2idle` | [`bootc` docs](https://bootc.dev/bootc/building/kernel-arguments.html) mark it **undefined behavior** when a kargs.d value changes across versions of the same fragment (this one shipped `deep` before PR #16 corrected it to `s2idle`) — once a value like this is baked into a deployment's persisted kernelopts, later `bootc upgrade`s can keep carrying the old value forward instead of reconciling to the fragment's current content. Confirmed on this machine 2026-09-19: `rpm-ostree kargs` showed `deep` on a deployment built long after the `s2idle` fix landed. **Harmless in practice on this hardware**: `cat /sys/power/mem_sleep` reports only `s2idle` — the firmware exposes no `deep` state at all, so the stale karg has nothing to select and resume is not currently degraded by it. Worth fixing for hygiene (declared intent should match reality) and because a future firmware/BIOS update could theoretically add a `deep` state this karg would then actually select, not because anything is broken today | one-time local correction, which then persists correctly across future upgrades the same way the wrong value did. Run `rpm-ostree kargs` bare first to confirm the deployment actually carries `deep` — `--delete` fails if it doesn't: `sudo rpm-ostree kargs --delete=mem_sleep_default=deep --append=mem_sleep_default=s2idle`, then reboot. Re-check any OTHER kargs.d fragment you edit after its first deploy — this isn't specific to suspend, it's a general bootc kargs.d gotcha |
| No audio; journal floods with `FW reported error: 9` / `failed to create module pipeline` | SOF topology ABI (3.29) newer than the kernel's SOF driver ABI (3.23); no ABI-≤3.23 firmware in repos to downgrade to | `25-audio-sof-bypass.toml` forces the legacy HDA driver (`snd_intel_dspcfg.dsp_driver=1`), sidestepping SOF; verify `journalctl -k \| grep -i dsp_driver` |
| Frequent corrected MCEs in the journal | machine-check events need per-event bank/status decode; zero EDAC `igen6` counters alone do not exclude DRAM faults | `rasdaemon` records/decodes them; `mcelog` is masked (its trigger tried to offline a CPU). Decode with `sudo ras-mc-ctl --errors` |
| `smartd` warns of media errors / available-spare drop | NVMe wear or developing fault | `journalctl -u smartd`; confirm with `sudo smartctl -a /dev/nvmeN`; a falling available-spare or rising media-error count is an escalation/back-up signal |
| Secure Boot refuses the image | — | the image kernel is signed with the shared ublue MOK (already enrolled on ublue/Bazzite hosts); no MOK work needed when switching ublue↔bazzite-tower |
| Black screen at login, no prompt, after a deployment update | KDE Plasma package-family skew — `kwin` landed a point release ahead of `kscreenlocker` in that day's dnf transaction (`kwin_wayland: undefined symbol: ...inhibitSuspend()`, exit 127, no compositor); recurred twice, see `docs/research/kwin-screenlocker-abi-2026-07-26/` and `-2026-08-08/` | roll back to the previous deployment via the GRUB menu or `rpm-ostree rollback`; `build.d/05-pin-kde-packages.sh` now excludes the KDE Plasma/KWin family from this build's own dnf transactions, and `tests/smoke.sh` fails the build if `kwin`/`kscreenlocker` don't share a major.minor version |

## Audio: SOF bypass (legacy HDA)

**Hardware** (verified on the live box): Realtek **ALC287** HDA codec (headphones +
analog/headset mic), **TI TAS2781** smart-amp speakers bound to the ALC287 as an HDA
*side-codec* (`tas2781_hda_comp_ops`), Intel HDMI codec, and a 2-mic **DMIC** array
that is SOF/DSP-only.

The kernel's SOF IPC4 driver is at topology ABI **3.23**, but stock
`alsa-sof-firmware` (2025.12.2) ships topologies at ABI **3.29**. When playback
starts on `pcm0p` ("HDA Analog" = speakers/headphones) the kernel sends a
module-create IPC the firmware (ADSPFW 2.14.1.1) can't parse — `failed to create
module pipeline.1` / `ipc error 0x11000007` / `ASoC error (-22) at
snd_soc_pcm_component_prepare` — and PipeWire retries at ~10 Hz (**94k+** errors per
boot, dead audio). Fedora's repos no longer carry an ABI-≤3.23 `alsa-sof-firmware`,
so the firmware **cannot be downgraded** to match the kernel.

- **Fix in the image:** `kargs.d/25-audio-sof-bypass.toml` sets
  `snd_intel_dspcfg.dsp_driver=1`, forcing the **legacy `snd_hda_intel` driver**.
  Because the speakers (TAS2781 via ALC287), headphones, HDMI, and analog/headset
  mic all live on the HDA codec path, they all work on legacy HDA — this is the
  documented SOF workaround (upstream even hardcodes legacy HDA for some ThinkPads
  in `intel-dsp-config`). **Lost:** the 2 internal DMICs and SOF DSP effects. Revert
  by deleting the fragment (re-enables SOF).
- **Verify after reboot:** `journalctl -k | grep -i dsp_driver`,
  `journalctl -k | grep -c "FW reported error: 9"` (expect `0`), and
  `cat /proc/asound/cards` (one HDA card, no SOF storm).
- **Seatbelt (dormant while bypassed):** `…/wireplumber.conf.d/90-tower-sof-backoff.conf`
  shortens the audio node's idle/error suspend window. It only matters if SOF is
  re-enabled (fragment removed); harmless otherwise.
- **Alternative considered, not taken:** overlay a matched ABI-3.23 firmware +
  topology set from [thesofproject/sof-bin](https://github.com/thesofproject/sof-bin)
  or Koji to keep full SOF + the mic array — more complex, depends on an external
  source, and needs per-update version matching. Revisit if the DMIC array is needed.

## CPU MCEs and memory evidence

This Meteor Lake CPU has logged corrected machine-check events (~115/boot in an
earlier observation). Attribution to CPU cache requires the decoded bank/status
and event type. Zero EDAC `igen6` ECC counters do **not** exclude DRAM faults or
establish that the events are harmless. `rasdaemon` collects and decodes supported
events; `mcelog` is masked.

```bash
sudo ras-mc-ctl --summary    # recorded database counts; may span boots
sudo ras-mc-ctl --errors     # decoded per-event detail (bank, address, type)
```

Reading the result:

- **Corrected** means hardware reported recovery for that event. It does not
  establish the cause or that continuing events are harmless. Compare rates,
  decoded banks/status, firmware and microcode revisions across boots; correlate
  with hangs, application crashes and memory failures.
- Corrected errors **localized to a single core / cache line** that recur are a
  possible RMA signal; capture `ras-mc-ctl --errors` over several boots.
- **Any _uncorrected_ MCE is an escalation**: preserve the decode and relevant
  journal, stop workload experiments and seek hardware diagnosis. Follow owner
  recovery procedures; an OS rollback alone does not establish hardware safety.
- `/proc/meminfo` `HardwareCorrupted` is memory currently accounted as poisoned
  by the kernel, in kB. A nonzero value warrants investigation; zero does not
  exclude errors that were corrected, unreported or outside this accounting.
- Missing RAS tools, inaccessible journals or unavailable counters mean
  **unknown**, not zero errors. RAS database counts and journal excerpts have
  different lifetimes; the health helper labels its bounded current-boot window.

Microcode: `microcode_ctl` is layered at the latest Fedora revision in `build.sh`.
Early-load takes effect once the initramfs is regenerated (on a base bump); confirm
the running revision with `grep -m1 microcode /proc/cpuinfo`.

## SELinux labels: boot.log and kvmfr

`ujust tower-health` shows the current and installed-policy expected labels for
`/var/log/boot.log` and `/dev/kvmfr0`, failed services, and whether an exact
`/dev/kvmfr0` mapping exists in the targeted policy's `file_contexts.local`. It
prints no file contents or other local mappings. Broader matching local patterns
are not enumerated; `matchpathcon` is the effective policy expectation. Missing
tools/permissions are **unknown**. The report does not change either label.

On this machine the owner observed `boot.log` as `var_t` while the installed
policy expected `plymouthd_var_log_t`, causing normal logrotate to fail. The
image-owned `logrotate.service` drop-in runs a fixed-path helper before its
existing rotation command. It calls `restorecon` only for an existing regular,
root-owned, single-link `/var/log/boot.log`; it refuses symlinks and never creates
or recursively relabels files. The current policy determines the expected type,
so another base is not forced to use `plymouthd_var_log_t`. A refused/failed repair
leaves the service failed for investigation. Vendor timer, AC-power condition,
logrotate arguments and rotation policy remain in effect; no forced rotation.

For an existing image without the helper, the owner can inspect and correct the
single file, then retry the normal service on AC power:

```bash
ls -lZ /var/log/boot.log
sudo restorecon -nv -- /var/log/boot.log  # inspect proposed label, no change
# Proceed only if it is the expected regular file, not a symlink/hard-link alias.
sudo restorecon -v -- /var/log/boot.log
sudo systemctl start logrotate.service
systemctl status logrotate.service --no-pager
```

`/dev/kvmfr0` is a separate owner-maintained SELinux override issue. A local exact
mapping to `svirt_tmpfs_t` was observed in `file_contexts.local`; do not infer that
the image udev ownership rule caused the denial. Inspect with
`sudo semanage fcontext -l -C` and `matchpathcon /dev/kvmfr0`, then review any
mapping change with the owner. The image does not remove local mappings, relabel
kvmfr, relax enforcement or install an allow-all policy.

## Wi-Fi: BE200 firmware asserts (desktop freezes)

Symptom: the whole desktop stops for 5–30s, then resumes. Wi-Fi is this machine's
**only** uplink (no ethernet port), so when the BE200 firmware asserts and the
driver resets the chip, every network-dependent process blocks at once and the
machine looks frozen.

```bash
journalctl -k -b 0 | grep -c "Device error - SW reset"   # the metric that matters
```

The signature:

```
iwlwifi: Error sending SYSTEM_STATISTICS_CMD: time out after 2000ms.
iwlwifi: 0x00000084 | NMI_INTERRUPT_UNKNOWN
iwlwifi: 0x20000066 | NMI_INTERRUPT_HOST
iwlwifi: Device error - SW reset
ieee80211 phy0: Hardware restart was requested
```

The host sends a routine statistics command, the firmware never answers, both its
processors take an NMI, and the driver hard-resets the device. Recovery is 5–15s.

**No kernel watchdog catches this.** The soft-lockup watchdog only fires on a CPU
spinning in kernel mode; `hung_task` only after 120s. The CPU is never stalled
here — only network I/O blocks. The iwlwifi error log is the sole reliable signal,
which is why the journal cap must retain weeks of history.

Mitigation: `…/modprobe.d/iwlwifi-be200-stability.conf` sets `iwlmld
power_scheme=1` (CAM — the radio never sleeps). `iwlmld` carries its **own** power
scheme, independent of `iwlwifi.power_save`, and it defaults to 2 (firmware
sleeps); power-save sleep/wake transitions are the suspected trigger.

```bash
cat /sys/module/iwlmld/parameters/power_scheme    # want 1

# apply without a reboot
sudo nmcli radio wifi off
sudo modprobe -r iwlmld && sudo modprobe iwlmld
sudo nmcli radio wifi on
```

Revert by deleting the file. If asserts continue with `power_scheme=1`, power save
is not the trigger — the next discriminator is a **real AP instead of a phone
hotspot** (every observation to date was on a tethered hotspot), and after that a
wired uplink, which is also the only way to stop Wi-Fi being a single point of
failure for the entire desktop.

## Bluetooth: paired device won't reconnect after suspend

Symptom: a previously-connected Bluetooth device (observed: a mouse) does not
reconnect after the system wakes from suspend. `bluetoothd` stays "active" the
whole time (no crash, nothing logged), and `journalctl -k` shows no BE200-style
firmware NMI/reset signature for `hci0` the way it does for the Wi-Fi radio
above — so the adapter's HCI/link state, not firmware, is what doesn't come
back cleanly across a suspend/resume cycle. Same underlying class of issue as
the i915 display resume regression and the BE200 Wi-Fi asserts: this platform
doesn't reinitialize every subsystem cleanly across s2idle.

Confirmed 2026-09-19: a ~2.5-day suspend left the adapter with zero reconnect
activity for 40+ minutes until a full reboot; the same day, a ~3h suspend
self-recovered on its own in 23s. So the stall is occasional, not guaranteed,
and gets more likely the longer the sleep.

Mitigation: `…/system-sleep/bazzite-tower-bluetooth-resume-guard` restarts
`bluetooth.service` on every resume (`systemd-sleep` invokes every executable
in that directory automatically; no unit/enable step). This re-opens the HCI
socket and re-initializes the adapter — the userspace-layer equivalent of what
a full reboot also does. A plain daemon restart does **not** reload the
btusb/btintel kernel driver or reset the USB device itself, and no incident
has yet confirmed whether that's actually necessary (only a full reboot was
ever tried on the aggravated case above) — so the guard escalates: if the
adapter doesn't report itself powered within 30s of the restart (the
documented 23s self-recovery above sets the floor — escalating sooner would
unbind a perfectly healthy adapter on an ordinary slow wake), it unbind/rebinds
the BE200 Bluetooth USB device (`8087:0036`, distinct from the BE200 Wi-Fi PCI
device — resetting it doesn't touch Wi-Fi) to force a closer equivalent of
what reboot provides, then restarts the daemon again. The health check itself
uses `busctl get-property`, not `bluetoothctl show` — the latter can SIGABRT
(observed, systemd-coredump) if queried before D-Bus is fully back up.
`etc/systemd/system/bluetooth.service.d/10-fast-timeout.conf` caps each
restart at 10s start / 10s stop, since this can restart the service twice in
a row and systemd-sleep blocks the whole system's resume until the script
exits — without it, a wedged restart could hold resume open for minutes.

```bash
# Confirm it ran on the last resume, and whether it had to escalate
journalctl -t bazzite-tower-bluetooth-resume-guard --no-pager
journalctl -u bluetooth --no-pager | grep -i "restart\|deactivat"

# Manual equivalents if you need them before the next suspend/resume cycle
sudo systemctl restart bluetooth.service
# `btusb` binds the device's interfaces, not the device itself -- the device
# binds to the generic `usb` driver. Use that, not `drivers/btusb`:
echo 3-10 | sudo tee /sys/bus/usb/drivers/usb/unbind   # bus path varies; see lsusb
echo 3-10 | sudo tee /sys/bus/usb/drivers/usb/bind
```

This is a separate mechanism from `…/modprobe.d/btusb-no-autosuspend.conf`
(`btusb enable_autosuspend=0`), which prevents the USB layer from
runtime-suspending the controller during active use — that guards against a
different trigger (USB autosuspend) than a full system suspend/resume cycle.

## Freezes with no kernel trace (i915 GuC / stall detector)

Some stalls on this machine leave **no kernel message at all**. The soft-lockup
watchdog only fires on a CPU spinning in kernel mode; `hung_task` only after
`hung_task_timeout_secs` (120s by default). A 5-15s freeze caused by a *blocked*
kernel worker trips neither, at any threshold.

`bazzite-tower-stall-detect.service` fills that gap: it samples `CLOCK_MONOTONIC`
and, on a gap, records which workers were stuck in D state. That is what
identifies the subsystem — without it a freeze is just "the machine paused".

```bash
ujust freeze-report                  # last week
ujust freeze-report "3 days ago"
```

The known local cause is the **i915 GuC TLB invalidation timeout**:

```
i915 0000:00:02.0: [drm] *ERROR* GT0: GUC: TLB invalidation response timed out
```

with `kworker/*+i915_flip` blocked — the display flip worker waiting on the GPU
microcontroller. This is [drm/i915 issue 14469](https://gitlab.freedesktop.org/drm/i915/kernel/-/issues/14469),
**unfixed upstream**. Intel attribute it to the GuC dying while waking the
hardware from RC6. It affects both the i915 and xe drivers, so it is firmware or
silicon, not driver code.

**The fix is a CSME firmware update — and this machine does not have it.**
Intel attribute the bug to the GuC dying while waking from RC6; the fix is in
PCODE, which ships inside Intel CSME firmware, which ships inside OEM BIOS.

```bash
cat /sys/class/mei/mei0/fw_ver     # want >= 18.1.18.2644
```

As of 2026-08-29 this box reports **18.0.5.2141** — the factory original. BIOS
N48ET34W (1.21, 2026-05-11) did not bundle the update, and `fwupd` offers no ME
update even with `lvfs-testing` enabled. Lenovo publishes it under the
**Chipset** category, not BIOS. Re-check periodically; a reporter on this same
laptop model received 18.1.18.2724 through fwupd/LVFS in April 2026, so the path
exists, it is just not offered for machine type 21KV yet.

Meanwhile, verified-ineffective (do not re-try these):

- `i915.enable_psr=0`, `i915.enable_dc=0` — **already set here**, and documented
  upstream as tested-and-ineffective against *this* bug. They are present for the
  cx0 resume regression, a different problem.
- `i915.enable_guc=0` / `=2` — the machine **will not boot**; GuC is mandatory on MTL.
- the `xe` driver — **worse** on this exact laptop model: silent hard lockups and
  failed resumes instead of a recoverable stall.
- BIOS updates alone, and CSME 18.0.15.x — both insufficient.
- `i915.enable_rc6=0` works, but the parameter was removed from the kernel in
  2018 and Lenovo BIOS has no Render Standby toggle, so it needs a patched kernel.

Available mitigations that cost nothing: **disable hardware video acceleration in
browsers** (the most consistently reported trigger is browser video playback), and
stay on the `balanced` platform profile rather than `performance`.

Full investigation, including the elimination table from a reporter with this same
laptop: [`docs/research/i915-guc-tlb-2026-08-29.md`](research/i915-guc-tlb-2026-08-29.md).

Until the firmware lands: **measure, do not patch.**

## Module blacklists (lean boot)

`…/modprobe.d/blacklist-unused-gpu.conf` blacklists **amdgpu** / **amdxcp** — there
is no AMD GPU on this machine, and no display path (including DisplayPort-alt over
USB-C/Thunderbolt, which the Intel iGPU drives) depends on them. `xe` is left
loaded on purpose. Revert by deleting the file. If `lsmod | grep amdgpu` still
shows it loaded after a rebase, it's initramfs-embedded — add the kernel arg
`rd.driver.blacklist=amdgpu` (new kargs.d fragment) as the stronger lever.

## Tuning defaults (swappiness, indexer)

- `…/sysctl.d/99-tower-swappiness.conf` sets `vm.swappiness=10` (zram was filling
  while RAM was free). Override at `/etc/sysctl.d/`; verify `sysctl vm.swappiness`.
- `/etc/xdg/baloofilerc` seeds baloo's `exclude filters` with build/cache trees
  (`.gradle`, `target`, `build`, language caches; `node_modules` is already a baloo
  default). It only seeds new users — a user's `~/.config/baloofilerc` overrides it.
  Re-index after editing with `balooctl6 disable && balooctl6 enable`.
- `…/journald.conf.d/90-tower-journal-cap.conf` sets `SystemMaxUse=4G` plus
  `MaxRetentionSec=1month` / `MaxFileSec=1day` (default cap is 10% of the fs
  ≈ 730 GiB here). journald enforces it continuously — no `journalctl --vacuum`
  timer needed. Check with `journalctl --disk-usage`. Raised from 500M on
  2026-08-28: that cap vacuumed away all prior-boot kernel messages within hours
  and hid the BE200 firmware asserts below. Growth is now bounded mainly by time,
  with size as the backstop.

## /etc drift vs the image

`/etc` is writable and 3-way merged across `rpm-ostree upgrade`; local state can
also persist across bootc rebases. It silently shadows whatever the image ships,
so a previously enabled optional service may remain enabled after switching to a
newer image. Inspect with `ujust tower-health` and explicitly disable unwanted
units. Anything intended as image policy belongs in `system_files/`.

```bash
sudo ostree admin config-diff | grep -E '^[AMD] '   # A = added locally, M = modified
```

Most of the ~300 entries are runtime state that *belongs* in `/etc` and must be
left alone: `systemd/system.control` (transient properties), `libvirt/nwfilter`
and `libvirt/storage` (libvirt regenerates these), `selinux/targeted` (policy
store), `bazzite/fixups` (ublue's own "already ran" markers), the user database,
akmods signing keys. **`NetworkManager/system-connections` holds Wi-Fi and VPN
secrets and must never be baked into the image.**

### Service enablement — triaged 2026-08-28

| Unit | Disposition |
|------|-------------|
| `tailscaled.service` | **Baked** only as an unauthenticated client daemon. No node identity, auth key, Serve/Funnel setup, or listener is included; the operator runs `tailscale up` separately. |
| `docker.service` / `docker.socket` | **Disabled by default.** `ujust enable-docker` is explicit because its group is root-equivalent. |
| `cockpit.socket` | **Disabled by default** and loopback-only when enabled with `ujust enable-cockpit`. The recipe prints, but does not run, the Tailscale Serve command. |
| `waydroid-container.service` | **Disabled by default.** `ujust enable-waydroid` starts only the service; Android image initialisation stays separate. |
| `sshd.service` | **Local only, on purpose.** The image ships sshd off so it stays safe to hand to anyone. Re-apply after a rebase with `ujust enable-ssh` |
| `plugin_loader.service` | **Never bake.** Decky Loader; runs as root with an ExecStart inside one user's home. Owned by Decky's installer |
| `libvirtd.service` | `/etc` carries an inert enable symlink. `60-libvirt-services.sh` **masks** this unit in favour of the modular `virt*` daemons and the mask wins. Delete the symlink, do not promote: `sudo rm /etc/systemd/system/multi-user.target.wants/libvirtd.service` |

### Device rules — triaged 2026-08-29

Promoted to `system_files/usr/lib/udev/rules.d/`: `70-kvmfr.rules` (Looking
Glass needs `/dev/kvmfr0` group-owned by qemu), `99-smartcard.rules` (rewritten
from `MODE="0666"` to `TAG+="uaccess"` — the old rule was world read/write),
`99-i2c-designware.rules`, `70-xreal-xr.rules`, `70-viture-xr.rules`,
`70-plustek-scanner.rules`. Plus `btusb-no-autosuspend.conf` to `modprobe.d/`.

**Not promoted, delete from `/etc`:**

| File | Why |
|------|-----|
| `72-usbhp.rules` | **Fails `udevadm verify`** (missing comma after `ACTION=="add"`), so it has never worked. Even corrected it would tag *every* USB device with uaccess. Whatever printer problem it was for is still unsolved |
| `70-rokid-xr.rules`, `70-rayneo-xr.rules`, `70-uinput-xr.rules` | Empty — comments only, zero directives |
| `modprobe.d/kvmfr.conf` | Empty — 290 bytes of comments, zero directives |
| `modprobe.d/i915-sleep.conf` | Redundant: all three options are already kernel args from `kargs.d/10-i915-display.toml` |
| `modprobe.d/kvm.conf` | Likely redundant with `kvm.ignore_msrs=1` in `kargs.d/30-vfio-kvm.toml` — verify before deleting |
| `firewalld/zones/FedoraWorkstation.xml` | Diff vs the base file is *attribute reordering only* (`port protocol= port=` vs `port port= protocol=`). Semantically identical; firewalld rewrote it on load. `.xml.old` is cruft |
| `libvirt/hooks/qemu` + `qemu.d/` | SharkWipf per-guest hook dispatcher, left from a GPU-passthrough experiment. `qemu.d/` is empty so it dispatches nothing. Delete; it is a well-known public script if ever wanted again |

This box has a habit of fixing things twice — `i915-sleep.conf` and `kvm.conf`
both duplicate settings already applied as kernel args. Check `kargs.d/` before
adding a `modprobe.d` drop-in.

### Superseded /etc copies

Three files now duplicate what the image ships and should be removed once the
Wi-Fi mitigation is confirmed holding — keep them until then as a no-rebuild
fallback, and note they only become redundant **after** an
`rpm-ostree upgrade` + reboot onto an image that contains them:

```bash
sudo rm /etc/modprobe.d/iwlwifi-fix.conf                # -> iwlwifi-be200-stability.conf
sudo rm /etc/systemd/journald.conf.d/99-retention.conf  # -> 90-tower-journal-cap.conf
# keep /etc/sysctl.d/99-watchdog-thresh.conf — diagnostic instrumentation,
# deliberately NOT in the image
```

## CPU power & thermal

The box boots into the firmware's most conservative state — cpufreq
`EPP=power`, ACPI `platform_profile=low-power` — with no power daemon, throttling a
plugged-in homelab. The image sets a **balanced** baseline:

- `bazzite-tower-power-tuning.service` (oneshot, runs `…/libexec/bazzite-tower-power-tuning`
  at boot) sets `platform_profile=balanced` and cpufreq `EPP=balance_performance`
  on every core. Idempotent; skips any knob that's absent/read-only.
- `thermald.service` manages Meteor Lake thermal limits.
- **Want more/less?** For max throughput edit the helper to write `performance`
  (EPP) / `performance` (profile); to revert, mask the service
  (`sudo systemctl mask bazzite-tower-power-tuning.service`). EPP can reset across
  suspend — re-run `sudo /usr/libexec/bazzite-tower-power-tuning` after resume, or
  add a `systemd-sleep` hook if that matters.

Runtime surface (units, helpers, kargs): [docs/CODEMAPS/system-files.md](./CODEMAPS/system-files.md).

## Publish pipeline (operator view)

`build.yml`: build → **smoke gate** → push GHCR → cosign sign **by digest**. The gate
runs before login/push, so a broken image never reaches `:latest` (it stays last-good).
Each guard workflow opens — and later auto-closes — a labelled tracking issue:

<!-- AUTO-GENERATED:ci-labels (from .github/workflows/) -->
| Label | Workflow | Meaning |
|---|---|---|
| `ci-failure-<variant>` | `build.yml` | build or smoke gate failed for that matrix leg (`safe-pin`, `latest-kernel` or `snitchwatch-system`); nothing published for that leg. Per-leg so one leg's success never auto-closes the other's issue |
| `boot-test-failure` | `boot-test.yml` | image built but misbehaved at runtime |
| `base-bump` | `base-watch.yml` | upstream base changed a blast-radius package |
| `iso-failure` | `build-iso.yml` | titanoboa live/installer ISO build failed |
<!-- END AUTO-GENERATED:ci-labels -->

## CI secrets

<!-- AUTO-GENERATED:secrets (from .github/workflows/) -->
| Secret | Required | Used for |
|---|---|---|
| `GITHUB_TOKEN` | auto | GHCR push; open/close tracking issues (provided by Actions) |
| `SIGNING_SECRET` | required for default-branch image release | cosign private key — signs image digests and SBOM attestations in `build.yml`; image publication fails if absent. ISO sign-blob remains optional. |
| `S3_PROVIDER`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION`, `S3_ENDPOINT`, `S3_BUCKET_NAME` | optional | rclone upload of disk/ISO artifacts (`build-disk.yml`, `build-iso.yml`). Unset → artifact-only |
<!-- END AUTO-GENERATED:secrets -->

### Signing-key rotation

Do **not** replace `SIGNING_SECRET` and `cosign.pub` in place: hosts that still
trust only the old key would reject the next image before it can deliver a new
policy. Use a staged transition instead:

1. Generate and protect the new key, while retaining the current key and its
   `SIGNING_SECRET`.
2. Publish an image signed by the **currently trusted** key that adds the new
   public key to the image/host policy alongside the old key (separate
   repository-scoped `sigstoreSigned` requirements, each with its own
   `keyPath`). Deploy that policy update to installed hosts with the bootstrap
   helper or equivalent managed configuration.
3. Configure CI to dual-sign every release during the transition, including its
   image and signed SBOM attestations, and verify both signatures. Confirm every
   installed host accepts the new key before retiring the old one.
4. Only then remove the old signing key, its public key, and its policy rule.

The current workflow intentionally supports one `SIGNING_SECRET` only. Dual
signing and dual-key policy support are prerequisites for a rotation; do not
start one by changing the existing secret or `cosign.pub` alone.

## Verify a published image

```bash
cosign verify --key cosign.pub ghcr.io/bearyjd/bazzite-tower:latest
```

Verify the signed SBOM attached to that image:

```bash
cosign verify-attestation --key cosign.pub --type spdxjson ghcr.io/bearyjd/bazzite-tower:latest
```

**Newer cosign may fail this with `no matching attestations: expected key
signature, not certificate`** (seen 2026-10-10). The image also carries a keyless
GitHub build-provenance bundle (a `sigstore.bundle.v0.3` DSSE envelope with a
Fulcio certificate, stored under the `sha256-<digest>` fallback tag because ghcr
lacks the OCI referrers API). Our `.sig`/`.att` tags are key-signed and valid;
the likely cause is that a newer cosign also evaluates the certificate-signed
bundle and `--key` cannot match it. This is inferred from the registry contents,
not reproduced against a specific cosign version. CI verifies with the pinned
`cosign v2.6.3` (`build.yml`), which passes. If your local cosign fails, either
use that version or check what the host actually enforces: switching with
`--enforce-container-sigpolicy` (below) verifies the key signature through
`/etc/containers/policy.json` and does not use the cosign CLI at all.

The repository policy uses `matchRepository`: it proves a digest was signed by
this repository key, but it does not guarantee freshness. A registry (or a
compromised signing process) could point `:latest` at an older, still-signed
digest. For rollback-sensitive hosts, record a reviewed image digest and switch
to that immutable reference instead of a tag:

```bash
sudo bootc switch --enforce-container-sigpolicy ghcr.io/bearyjd/bazzite-tower@sha256:<reviewed-digest>
```

## Disk / ISO artifacts

- `build-disk.yml` (dispatch) → qcow2 via bootc-image-builder → artifact or S3.
- `build-iso.yml` (dispatch, Sun 08:00 UTC) → titanoboa live/installer ISO,
  Secure-Boot-bootable → checksum + cosign sign-blob → artifact or S3. Build internals:
  [docs/CODEMAPS/iso-build.md](./CODEMAPS/iso-build.md).
