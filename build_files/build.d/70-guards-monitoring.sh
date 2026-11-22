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

# ── Cockpit: web UI for system, services, storage, logs, podman (opt-in) ───────
# Homelab management surface on :9090. The base already ships most of Cockpit
# (bridge/system/networkmanager/storaged/podman/files/selinux) and does NOT ship
# cockpit-machines or libvirt-dbus (verified with rpm -q on the base image).
# There is deliberately NO Virtual Machines page: cockpit-machines pulls in
# libvirt-dbus, which let any group-libvirt process manage qemu:///system over
# the system bus without a polkit prompt, bypassing
# 49-bazzite-tower-libvirt-manage.rules. Removed in PR #225; see docs/RUNBOOK.md
# "Docker without silent root". For VMs use virt-manager / virsh.
# The socket is not enabled (disabled below). The operator opts in with
# `ujust enable-cockpit`; its image-supplied drop-in binds Cockpit to loopback so
# remote access needs deliberate Tailscale Serve or firewall policy.
dnf install -y cockpit
systemctl disable cockpit.socket || true
