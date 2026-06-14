#!/usr/bin/env bash
# tower-diagnostic.sh — full Bazzite Tower health sweep
# Run with sudo for complete output (SMART, MCE decode, full dmesg):
#   chmod +x tower-diagnostic.sh && sudo ./tower-diagnostic.sh
# Without sudo it still works but skips root-only checks.
set -uo pipefail
hr(){ printf '\n\033[1m===== %s =====\033[0m\n' "$1"; }
have(){ command -v "$1" >/dev/null 2>&1; }

hr "SYSTEM"
hostnamectl 2>/dev/null | grep -iE "Operating System|Kernel|Hardware"
echo "cmdline:"; cat /proc/cmdline

hr "SOF AUDIO — storm + root cause"
echo "FW error 9 count (this boot):"; journalctl -k -b 0 | grep -c "FW reported error: 9"
echo "FW error 9 in last 30s (storm active if >0):"; journalctl -k --since "30 sec ago" | grep -c "FW reported error: 9"
echo "Topology vs kernel ABI (mismatch = root cause):"; journalctl -k -b 0 | grep -E "Topology: ABI|loading topology|Booted firmware" | head -3
echo "Installed SOF firmware:"; rpm -q alsa-sof-firmware sof-firmware 2>/dev/null
echo "PipeWire prepare errors (this boot):"; journalctl --user -u pipewire -b 0 | grep -c "snd_pcm_prepare error"
echo "Active streams (empty = storm is WirePlumber-only):"; pactl list short sink-inputs 2>/dev/null
echo "Default sink state:"; pactl info 2>/dev/null | grep -i "Default Sink"; pactl list short sinks 2>/dev/null

hr "MCE / MEMORY"
echo "MCE log lines (this boot):"; journalctl -k -b 0 | grep -c "Machine check events logged"
echo "EDAC ECC counters (igen6):"; for f in /sys/devices/system/edac/mc/mc*/ce_count /sys/devices/system/edac/mc/mc*/ue_count; do [ -e "$f" ] && echo "  $f = $(cat "$f")"; done
echo "mcelog decode (needs root + mcelog.service):"; journalctl -t mcelog -b 0 2>/dev/null | tail -10 || echo "  (none)"
have ras-mc-ctl && { echo "rasdaemon summary:"; sudo ras-mc-ctl --summary 2>/dev/null; } || echo "  rasdaemon not installed"
free -h; echo; have zramctl && zramctl

hr "i915 PHY A / DISPLAY RESUME"
echo "i915 error count (this boot):"; journalctl -k -b 0 | grep -c "i915.*ERROR"
journalctl -k -b 0 | grep -iE "PHY A|pll\[|flip_done timed out|commit wait timed out|min_voltage" | tail -10
echo "suspend mode:"; cat /sys/power/mem_sleep

hr "THERMALS"
have sensors && sensors | grep -iE "Core|Package|Composite|fan|temp" | head -25 || echo "  lm_sensors not available"

hr "STORAGE (SMART — needs root)"
for d in /dev/nvme0 /dev/nvme1; do
  echo "--- $d ---"
  if have smartctl; then sudo smartctl -H "$d" 2>&1 | grep -iE "health|result|permission";
    sudo smartctl -a "$d" 2>/dev/null | grep -iE "Percentage Used|Critical|Media.*Error|Unsafe Shutdown|Available Spare"; fi
done

hr "RPM-OSTREE"
rpm-ostree status 2>/dev/null
echo "layered/local packages (empty = clean):"; rpm-ostree status -v 2>/dev/null | grep -iE "LayeredPackages|LocalPackages" || echo "  none"

hr "TOP CPU"
ps aux --sort=-%cpu | head -10 | awk '{printf "  %-9s %5s%% %5s%% %s\n",$1,$3,$4,$11}'

hr "DONE"
