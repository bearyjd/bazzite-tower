#!/usr/bin/env bash
set -euo pipefail

# ── RAS / MCE handling ────────────────────────────────────────────────────────
# This box logs ~115 corrected CPU cache-error MCEs per boot on Meteor Lake. EDAC
# igen6 ECC counters read 0/0, so these are CPU cache, not DRAM. Two changes:
#   1. rasdaemon — the modern RAS collector. It records and decodes MCEs into a
#      local store queryable with `ras-mc-ctl --summary`/`--errors`.
#   2. mask mcelog — redundant with rasdaemon, and on this box its
#      cache-error-trigger tried to *offline a CPU* when it hit the corrected-error
#      threshold (it failed only because the trigger script was buggy). Masking
#      removes that footgun for good.
dnf install -y rasdaemon
systemctl enable rasdaemon.service
systemctl mask mcelog.service

# Latest Intel microcode. Meteor Lake has shipped corrected-cache-error microcode
# updates; this box was seen on revision 0x28. `dnf install` upgrades microcode_ctl
# to the newest in the Fedora repos when the base is older (no-op if already
# current). Note early-load takes effect once the initramfs is regenerated (on a
# base bump); verify the running revision per docs/RUNBOOK.md.
dnf install -y microcode_ctl

