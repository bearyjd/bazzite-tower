#!/usr/bin/env bash
set -euo pipefail

# ── Wi-Fi backend guard: don't let a stale wifi.backend=iwd strand Wi-Fi ───────
# /etc persists across a bootc rebase, so a `wifi.backend=iwd` file from a
# previous image can outlive its enabled iwd service — NetworkManager then points
# at a supplicant that never runs and every Wi-Fi device reports `unavailable`.
# This unit runs before NetworkManager each boot and forces the default
# wpa_supplicant backend whenever iwd is selected but not enabled (and backs off
# again the moment iwd is properly enabled). Ships in system_files/.
systemctl enable bazzite-tower-wifi-backend-guard.service

# ── Storage health monitoring (SMART) ─────────────────────────────────────────
# smartd runs scheduled self-tests and watches SMART health for both NVMe drives,
# logging warnings to the journal (no MTA on this image). Config ships in
# system_files/etc/smartmontools/smartd.conf.
dnf install -y smartmontools
systemctl enable smartd.service

# ── Owner-invoked diagnostics (no automatic collection/tracing) ───────────────
# Fedora packages; no feature probing, GPU tests or trace sessions at build/boot.
# The image preset is already present when RPM install scriptlets apply presets.
dnf install -y bpftool sysstat igt-gpu-tools trace-cmd
# No --now: this is an offline image build. Fail if expected unit names drift.
systemctl disable sysstat.service sysstat-collect.timer sysstat-summary.timer sysstat-rotate.timer trace-cmd.service
# Fedora's sleep hook unconditionally calls sa1 --sleep even with units disabled.
# systemd-sleep executes only executable hooks. Retain vendor bytes but override
# RPM mode deliberately, after this stage's install on every image build.
# Timer opt-in does not re-enable suspend annotations; manual sysstat tools work.
[[ -f /usr/lib/systemd/system-sleep/sysstat.sleep && ! -L /usr/lib/systemd/system-sleep/sysstat.sleep ]]
chmod 0644 /usr/lib/systemd/system-sleep/sysstat.sleep

# ── Cockpit: web-based system + VM management (opt-in) ─────────────────────────
# Homelab management surface on :9090 — VMs (cockpit-machines drives the same
# libvirt stack baked above), services, storage, logs, and podman. The base
# already ships most of Cockpit (bridge/system/networkmanager/storaged/podman/
# files/selinux); only cockpit-machines is missing, and the socket isn't enabled.
# Add the VM module but do not enable socket activation. The operator opts in with
# `ujust enable-cockpit`; its image-supplied drop-in binds Cockpit to loopback so
# remote access needs deliberate Tailscale Serve or firewall policy.
dnf install -y cockpit cockpit-machines
systemctl disable cockpit.socket || true
