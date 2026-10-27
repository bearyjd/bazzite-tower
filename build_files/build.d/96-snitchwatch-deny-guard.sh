#!/usr/bin/env bash
set -euo pipefail

# ── Snitchwatch deny opt-in boot guard ────────────────────────────────────────
# The image always ships DefaultAction=allow. `ujust snitchwatch-deny on` starts
# an operator-initiated, time-boxed deny trial; if the machine reboots before it
# is confirmed, this guard reverts it before opensnitch.service starts. The unit
# is conditional on the helper's state file, so it is inert unless a trial exists.
# Ships in system_files/; only enablement belongs here.
systemctl enable bazzite-tower-snitchwatch-deny-guard.service

# Display-only snapshot for the GUI (/run/snitchwatch/deny-status.json); /run is
# tmpfs so this regenerates it each boot. Not a dependency of the firewall.
systemctl enable bazzite-tower-snitchwatch-deny-status.service
