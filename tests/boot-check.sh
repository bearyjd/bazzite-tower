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

say "== Wi-Fi backend guard runtime =="
# The guard is a oneshot (RemainAfterExit) ordered Before=NetworkManager. Active
# means it executed cleanly against the real image's NM config.
hard "wifi guard ran (active)"  systemctl is-active --quiet bazzite-tower-wifi-backend-guard.service
hard "wifi guard not failed"    not_failed bazzite-tower-wifi-backend-guard.service
# NM device management is unreliable in a container — informational only.
soft "NetworkManager active"    systemctl is-active --quiet NetworkManager.service

say "== first-boot oneshot =="
soft "firstboot service not failed" not_failed bazzite-tower-firstboot.service

say "== Docker (soft: daemon netns/iptables limited in a container) =="
soft "docker.service active" systemctl is-active --quiet docker.service

say "== done (fail=${fail}) =="
exit "${fail}"
