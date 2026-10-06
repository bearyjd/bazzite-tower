#!/usr/bin/env bash
# tests/boot-check.sh — runs INSIDE the booted image (systemd as PID 1 inside a
# `podman run --systemd=always` container) via `podman exec`, after the workflow
# has waited for the system to settle. Where tests/smoke.sh only asserts our
# changes are *present*, this proves they *work at runtime*: it socket-activates
# virtqemud and actually connects to qemu:///system (the end-to-end proof that
# the qemu user resolves and virtqemud initializes — the exact regression #8
# fixed), and confirms the Wi-Fi backend guard ran clean.
#
# Exits 0 if every HARD check passed, non-zero otherwise. The runner reads that
# exit code; all output here lands in the workflow log for diagnosis.
#
# A container shares the host kernel and has no /dev/kvm, no bootloader and
# limited networking, so anything that genuinely needs those (kargs application,
# full NetworkManager device management, the Docker daemon's netfilter setup) is
# a SOFT check — reported but non-fatal. The QEMU connect and the guard execution
# do not need any of that, so they are HARD checks.
set -uo pipefail

fail=0
say()  { echo "$*"; }
# hard <desc> <cmd...> — a failure fails the boot test.
hard() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then say "  ok   ${d}"; else say "  FAIL ${d}"; fail=1; fi; }
# soft <desc> <cmd...> — reported, but never fails the boot test (container limits).
soft() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then say "  ok   ${d}"; else say "  warn ${d} (non-fatal in a container)"; fi; }

# shellcheck disable=SC2329 # Invoked indirectly through `soft` below.
not_failed() { [[ "$(systemctl is-failed "$1" 2>/dev/null)" != "failed" ]]; }

# Give the Before=NetworkManager guard oneshot a moment in case exec raced it.
sleep 3

say "== system state =="
say "  is-system-running: $(systemctl is-system-running 2>/dev/null || true)"

say "== QEMU / libvirt runtime =="
hard "qemu user resolves at runtime"   id qemu
hard "virtqemud.socket active"         systemctl is-active --quiet virtqemud.socket
hard "virtnetworkd.socket active"      systemctl is-active --quiet virtnetworkd.socket
# The real end-to-end proof: connecting socket-activates virtqemud, which aborts
# if the qemu user is unresolvable (regression #8). Bounded so it can't wedge.
hard "virsh -c qemu:///system connects" timeout 60 virsh -c qemu:///system list --all
hard "virtqemud.service not failed"     not_failed virtqemud.service
soft "docker-libvirt-forwarding.path active" systemctl is-active --quiet docker-libvirt-forwarding.path
soft "docker-libvirt-forwarding.timer active" systemctl is-active --quiet docker-libvirt-forwarding.timer

say "== Wi-Fi backend guard runtime =="
# The guard is a oneshot (RemainAfterExit) ordered Before=NetworkManager. Active
# means it executed cleanly against the real image's NM config.
hard "wifi guard ran (active)"  systemctl is-active --quiet bazzite-tower-wifi-backend-guard.service
hard "wifi guard not failed"    not_failed bazzite-tower-wifi-backend-guard.service
# NM device management is unreliable in a container — informational only.
soft "NetworkManager active"    systemctl is-active --quiet NetworkManager.service

say "== SOF audio (ABI-mismatch regression guard) =="
# A SOF topology/kernel ABI mismatch surfaces as these kernel + ASoC lines and,
# left unchecked, storms the journal until PipeWire turns the card off. The CI
# container has no SOF hardware, so this passes vacuously there; on a real boot
# journal it catches the regression. HARD: any occurrence fails the boot test.
hard "no SOF 'FW reported error: 9'" \
    bash -c '! journalctl -k -b 0 --no-pager 2>/dev/null | grep -q "FW reported error: 9"'
hard "no SOF 'failed widget list set up'" \
    bash -c '! journalctl -b 0 --no-pager 2>/dev/null | grep -q "failed widget list set up"'

say "== OpenSnitch (application firewall) =="
firewall_daemon="$(cat /usr/share/bazzite-tower/firewall-daemon 2>/dev/null || true)"
case "${firewall_daemon}" in
opensnitch)
# opensnitchd is extracted from an upstream RPM with rpm2cpio, which resolves no
# dependencies — so a missing shared library ships green and only surfaces as an
# exec failure at boot. Actually executing the binary is the strongest available
# proof (it exercises the real loader, not ldd's report of it), and `-version`
# exits 0 without touching the network or netfilter — so unlike the activeness
# check below this needs nothing a container lacks, and is HARD.
hard "opensnitchd execs at runtime" \
    bash -c '/usr/bin/opensnitchd -version >/dev/null 2>&1'
# ProcMonitorMethod is pinned to "proc" because the v1.8.0 RPM's bundled eBPF
# module does not load on this image's kernel (snitchwatch#6). If a future config
# or release flips back to ebpf on an unsupported kernel, the daemon degrades and
# logs this. HARD: passes vacuously if the daemon never started in the container,
# but catches the regression on a real boot journal (same bargain as SOF above).
hard "no eBPF module load failure" \
    bash -c '! journalctl -b 0 --no-pager 2>/dev/null | grep -q "unable to load eBPF module"'
# Interception needs NFQUEUE + NET_ADMIN, which a container does not have — the
# daemon legitimately fails to come up here, so activeness is informational only.
soft "opensnitch.service active" systemctl is-active --quiet opensnitch.service
# System sockets, credentials and hardening need a running systemd but not
# NFQUEUE. Actual interception and SELinux authorization remain VM gates.
bridge_profile="$(< /usr/share/bazzite-tower/snitchwatch-bridge-profile)"
case "${bridge_profile}" in
legacy) ;;
system)
    say "== Snitchwatch system bridge runtime =="
    hard "installed system provenance verifies" /usr/libexec/snitchwatch/verify-system-manifest.py --root /
    hard "installed downstream daemon provenance verifies" /usr/libexec/snitchwatch/verify-system-daemon.py --root /
    # A rootless boot container cannot start NFQUEUE. If the service is active,
    # its executable must match the immutable manifest. VM acceptance separately
    # requires daemon activeness and tests actual interception/shutdown.
    hard "active daemon uses the verified installed executable" python3 -c '
import hashlib, json, os, subprocess
m=json.load(open("/usr/share/snitchwatch/system-daemon-manifest.json"))
pid=int(subprocess.check_output(["systemctl","show","-p","MainPID","--value","opensnitch.service"],text=True,timeout=5).strip())
if pid == 0:
 print("No active daemon PID; NFQUEUE execution remains a VM gate")
else:
 executable="/proc/"+str(pid)+"/exe"
 assert os.readlink(executable)==m["binary"]["path"]=="/usr/bin/opensnitchd"
 assert hashlib.sha256(open(executable,"rb").read()).hexdigest()==m["binary"]["sha256"]
 assert open("/proc/"+str(pid)+"/cmdline","rb").read()==b"/usr/bin/opensnitchd\0"
 status=dict(line.split(":",1) for line in open("/proc/"+str(pid)+"/status") if ":" in line)
 assert status.get("Uid","").split()==["0"]*4'
    hard "system bridge user resolves" id snitchwatch
    hard "system GUI group resolves" getent group snitchwatch-ui
    hard "sysusers and tmpfiles setup completed" \
        bash -c 'systemctl show -p Result --value systemd-sysusers.service | grep -qx success && systemctl show -p Result --value systemd-tmpfiles-setup.service | grep -qx success'
    hard "no user implicitly enrolled in the GUI group" python3 -c '
import grp, pwd
g = grp.getgrnam("snitchwatch-ui")
assert not g.gr_mem
assert not any(p.pw_gid == g.gr_gid for p in pwd.getpwall())'
    hard "gRPC socket active" systemctl is-active --quiet snitchwatch-system-bridge-grpc.socket
    hard "GUI socket active" systemctl is-active --quiet snitchwatch-system-bridge-gui.socket
    hard "both named sockets have expected DAC" python3 -c '
import grp, os, stat
gid = grp.getgrnam("snitchwatch-ui").gr_gid
for path, mode, owner, group in [("/run/snitchwatch",0o711,0,0),("/run/snitchwatch/opensnitchd.sock",0o600,0,0),("/run/snitchwatch/bridge.sock",0o660,0,gid)]:
 s=os.stat(path); assert stat.S_IMODE(s.st_mode)==mode and (s.st_uid,s.st_gid)==(owner,group), path
 assert stat.S_ISDIR(s.st_mode) if path=="/run/snitchwatch" else stat.S_ISSOCK(s.st_mode)'
    hard "bridge starts without NFQUEUE or a GUI" timeout 30 systemctl start snitchwatch-system-bridge.service
    hard "bridge is active" systemctl is-active --quiet snitchwatch-system-bridge.service
    hard "bridge publishes its token promptly" timeout 10 bash -c 'until [[ -f /run/snitchwatch-auth/token ]]; do sleep 0.1; done'
    hard "bridge runtime token has named credentials and modes" python3 -c '
import grp, os, pwd, stat
uid=pwd.getpwnam("snitchwatch").pw_uid; gid=grp.getgrnam("snitchwatch-ui").gr_gid
for path,mode in [("/run/snitchwatch-auth",0o2750),("/run/snitchwatch-auth/token",0o640)]:
 s=os.stat(path); assert stat.S_IMODE(s.st_mode)==mode and (s.st_uid,s.st_gid)==(uid,gid), path'
    hard "resolved bridge hardening and activation are intact" python3 -c '
import subprocess
p=dict(line.split("=",1) for line in subprocess.check_output(["systemctl","show","snitchwatch-system-bridge.service"],text=True).splitlines() if "=" in line)
for key,value in {"User":"snitchwatch","Group":"snitchwatch","NoNewPrivileges":"yes","CapabilityBoundingSet":"","ProtectSystem":"strict","ProtectHome":"yes","PrivateTmp":"yes","PrivateDevices":"yes"}.items():
 assert p.get(key)==value,(key,p.get(key))
assert "SNITCHWATCH_SYSTEM_BRIDGE=1" in p.get("Environment","")
assert "/usr/bin/snitchwatch-bridge-cli" in p.get("ExecStart","")
assert set(p.get("TriggeredBy","").split())=={"snitchwatch-system-bridge-grpc.socket","snitchwatch-system-bridge-gui.socket"}'
    hard "resolved daemon command, working directory and socket dependency" python3 -c '
import re, subprocess
p=dict(line.split("=",1) for line in subprocess.check_output(["systemctl","show","opensnitch.service"],text=True,timeout=5).splitlines() if "=" in line)
assert p.get("WorkingDirectory")=="/run/snitchwatch"
assert "snitchwatch-system-bridge-grpc.socket" in p.get("Requires","").split()
assert "snitchwatch-system-bridge-grpc.socket" in p.get("After","").split()
assert re.findall(r"(?:^|[ {])path=([^ ;}]+)",p.get("ExecStart",""))==["/usr/bin/opensnitchd"]
assert re.findall(r"argv\[\]=([^;]+)",p.get("ExecStart",""))==["/usr/bin/opensnitchd "]'
    ;;
*) hard "known Snitchwatch bridge profile" false ;;
esac
# shellcheck disable=SC2016 # The inner shell, not this script, expands $().
hard "portmaster is masked" \
    bash -c '[[ "$(systemctl is-enabled portmaster.service 2>/dev/null)" == "masked" ]]'
;;
portmaster)
say "== Portmaster (disabled VM spike) =="
hard "portmaster binary execs" \
    bash -c '/usr/libexec/portmaster/portmaster-core version >/dev/null 2>&1'
# Assert the *resolved* ExecStart, not the unit file's text: systemctl sees
# parsed drop-ins and is immune to comments, line continuations, and variable
# expansion. Without --bin-dir, portmaster-core falls back to a hardcoded
# /usr/lib/portmaster whose updater mkdirs it on read-only /usr and exits 2.
# shellcheck disable=SC2016 # The inner shell, not this script, expands $().
hard "portmaster ExecStart pins --bin-dir at the real install dir" \
    bash -c 'systemctl show -p ExecStart --value portmaster.service | grep -q -- "--bin-dir /usr/libexec/portmaster"'
# shellcheck disable=SC2016 # The inner shell, not this script, expands $().
hard "portmaster ExecStart pins --data-dir at writable state" \
    bash -c 'systemctl show -p ExecStart --value portmaster.service | grep -q -- "--data-dir /var/lib/portmaster"'
# Without this the daemon logs to /var/log/portmaster and journalctl shows only
# systemd's own messages, so a VM run reads the wrong place for evidence.
# shellcheck disable=SC2016 # The inner shell, not this script, expands $().
hard "portmaster ExecStart sends logs to the journal" \
    bash -c 'systemctl show -p ExecStart --value portmaster.service | grep -q -- "--log-stdout"'
# shellcheck disable=SC2016 # The inner shell, not this script, expands $().
hard "portmaster remains disabled" \
    bash -c '[[ "$(systemctl is-enabled portmaster.service 2>/dev/null)" == "disabled" ]]'
# shellcheck disable=SC2016 # The inner shell, not this script, expands $().
hard "opensnitch is masked" \
    bash -c '[[ "$(systemctl is-enabled opensnitch.service 2>/dev/null)" == "masked" ]]'
;;
*)
hard "known firewall selector" false
;;
esac

say "== first-boot oneshot =="
soft "firstboot service not failed" not_failed bazzite-tower-firstboot.service

say "== Optional host services (disabled unless the operator opts in) =="
# shellcheck disable=SC2016 # The inner shell, not this script, expands $().
hard "docker.socket disabled" \
    bash -c '[[ "$(systemctl is-enabled docker.socket 2>/dev/null)" == "disabled" ]]'
# shellcheck disable=SC2016 # The inner shell, not this script, expands $().
hard "docker.service disabled" \
    bash -c '[[ "$(systemctl is-enabled docker.service 2>/dev/null)" == "disabled" ]]'
# shellcheck disable=SC2016 # The inner shell, not this script, expands $().
hard "cockpit.socket disabled" \
    bash -c '[[ "$(systemctl is-enabled cockpit.socket 2>/dev/null)" == "disabled" ]]'
# shellcheck disable=SC2016 # The inner shell, not this script, expands $().
hard "waydroid-container.service disabled" \
    bash -c '[[ "$(systemctl is-enabled waydroid-container.service 2>/dev/null)" == "disabled" ]]'

say "== done (fail=${fail}) =="
exit "${fail}"
