#!/usr/bin/env bash
set -euo pipefail

# ── CPU power/thermal: balanced baseline ──────────────────────────────────────
# The box boots into the firmware's low-power state (cpufreq EPP=power, ACPI
# platform_profile=low-power) with no power daemon, throttling a plugged-in
# homelab (96 throttle events/boot observed). Install thermald for proper Meteor
# Lake thermal management, and enable a oneshot that sets a balanced EPP + platform
# profile at boot (helper + unit ship in system_files/).
dnf install -y thermald
systemctl enable thermald.service
systemctl enable bazzite-tower-power-tuning.service

# NOTE on SOF audio: the analog/HDMI codec is driven the legacy-HDA way on this
# image via the snd_intel_dspcfg.dsp_driver=1 kernel arg (kargs.d/25-audio-sof-bypass.toml),
# NOT by downgrading firmware. The kernel's SOF driver is at topology ABI 3.23 while
# stock alsa-sof-firmware ships ABI 3.29, and Fedora's repos no longer carry an
# ABI-≤3.23 build to downgrade to — so the firmware can't be pinned. Forcing the
# legacy HDA path sidesteps SOF entirely. See docs/RUNBOOK.md "Audio".

