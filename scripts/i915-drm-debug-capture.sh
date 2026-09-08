#!/usr/bin/env bash
# i915-drm-debug-capture.sh — turn on DRM/KMS debug logging, then extract the
# journal around the moment a display glitch was actually SEEN.
#
# Why this exists: some display symptoms leave NO trace at default kernel log
# verbosity. The 2026-09-07 flicker investigation
# (docs/research/nvidia-modeset-head-flicker-2026-09-07.md) ruled out three
# theories and ended with exactly this problem — a visible artifact, during both
# active use and idle, with zero i915 or nvidia-modeset errors logged. Raising
# drm.debug is the only way to see what the display pipeline is doing when it
# happens.
#
# Usage (all forms need root for the debug knob — it is 0600 root-only):
#   sudo ./i915-drm-debug-capture.sh on            # enable, default mask
#   sudo ./i915-drm-debug-capture.sh on 0x114      # enable, custom mask
#   sudo ./i915-drm-debug-capture.sh status        # current mask + log volume
#   sudo ./i915-drm-debug-capture.sh grab 23:26    # extract +/-60s around 23:26
#   sudo ./i915-drm-debug-capture.sh grab 23:26 120  # ...+/-120s instead
#   sudo ./i915-drm-debug-capture.sh off           # ALWAYS do this when done
#
# WORKFLOW: run `on`, use the machine normally, and the moment you SEE a
# flicker note the wall-clock time (minute precision is enough). Then run
# `grab <that time>` and `off`. The grab window is what gets analysed.
#
# TIME HANDLING: a bare "HH:MM" means today, or YESTERDAY if that time has not
# happened yet (so `grab 23:26` at 18:47 reads as last night, and says so).
# Pass a full "YYYY-MM-DD HH:MM:SS" to be explicit. A capture whose window
# holds no kernel lines at all is reported as a WARNING, not as "no trace" —
# an empty window nearly always means the wrong time, not a silent glitch.
#
# drm.debug bitmask (see include/drm/drm_print.h):
#   0x001 CORE    0x002 DRIVER  0x004 KMS     0x008 PRIME
#   0x010 ATOMIC  0x020 VBL     0x040 STATE   0x080 LEASE
#   0x100 DP      0x200 DRMRES
#
# Default mask 0x104 = KMS + DP: modeset/pipe activity plus eDP link training
# and AUX traffic, which is where a panel-side glitch would show. ATOMIC (0x10)
# adds per-commit detail and is worth trying if 0x104 shows nothing.
#
# DO NOT enable VBL (0x20) on this machine: the panel runs at 165 Hz, so it
# emits ~165 lines/second and will flood the journal and evict other evidence.
set -euo pipefail

KNOB=/sys/module/drm/parameters/debug
DEFAULT_MASK=0x104
WINDOW_DEFAULT=60

die() { printf 'error: %s\n' "$1" >&2; exit 1; }

require_root() {
    [ "$(id -u)" -eq 0 ] || die "must run as root (the ${KNOB} knob is 0600 root-only)"
}

require_knob() {
    [ -e "$KNOB" ] || die "${KNOB} not present — is the drm module loaded?"
}

cmd_on() {
    local mask="${1:-$DEFAULT_MASK}"
    require_root
    require_knob
    printf '%d\n' "$((mask))" > "$KNOB"
    printf 'drm.debug set to %s (%#x)\n' "$mask" "$((mask))"
    printf '\nNow use the machine normally. When you SEE a flicker, note the time,\n'
    printf 'then run:  sudo %s grab <HH:MM>\n' "$0"
    printf 'When finished, turn it back off — this logs continuously:  %s off\n' "$0"
}

cmd_off() {
    require_root
    require_knob
    echo 0 > "$KNOB"
    echo "drm.debug disabled (0)"
}

cmd_status() {
    require_knob
    local cur
    cur="$(cat "$KNOB")"
    printf 'drm.debug            : %s (%#x)\n' "$cur" "$cur"
    if [ "$cur" -eq 0 ]; then
        printf 'state                : OFF — run "%s on" before trying to capture\n' "$0"
    else
        printf 'state                : ON\n'
    fi
    printf 'kernel lines, last 5m: %s\n' \
        "$(journalctl -k --since '-5 min' --no-pager 2>/dev/null | wc -l)"
    printf 'journal disk usage   : %s\n' \
        "$(journalctl --disk-usage 2>/dev/null | sed 's/^.*take up //')"
}

cmd_grab() {
    local when="${1:-}" window="${2:-$WINDOW_DEFAULT}" epoch from end stamp out
    [ -n "$when" ] || die "grab needs a time, e.g. '23:26' or '2026-09-07 23:26:00'"

    # Convert to epoch and do the window arithmetic numerically. Relative
    # offsets cannot be appended to an absolute datetime: GNU date reads
    # `date -d "2026-09-07 23:26:00 -60 seconds"` as a TIMEZONE offset and
    # fails. Epoch maths sidesteps the ambiguity entirely.
    epoch="$(date -d "$when" '+%s' 2>/dev/null)" \
        || die "could not parse time '${when}' (try '23:26' or '2026-09-07 23:26:00')"

    # A bare "HH:MM" resolves to TODAY, so a time later than the current clock
    # lands in the future and silently yields an empty capture. If the caller
    # gave no explicit date, roll back one day — "23:26" at 18:47 means last
    # night. An explicit date (contains - or /) is always taken literally.
    now="$(date '+%s')"
    if [ "$epoch" -gt "$now" ]; then
        if [[ "$when" == *-* || "$when" == *[/]* ]]; then
            die "'${when}' is in the future — nothing can have been logged yet"
        fi
        epoch=$((epoch - 86400))
        printf 'note: %s had not happened yet today; reading it as %s\n' \
            "$when" "$(date -d "@${epoch}" '+%Y-%m-%d %H:%M')" >&2
    fi

    from="$(date -d "@$((epoch - window))" '+%Y-%m-%d %H:%M:%S')"
    end="$(date -d "@$((epoch + window))" '+%Y-%m-%d %H:%M:%S')"
    stamp="$(date -d "@${epoch}" '+%Y%m%d-%H%M%S')"
    out="i915-drmdebug-${stamp}.log"

    {
        printf '===== drm.debug capture =====\n'
        printf 'sighting reported : %s\n' "$when"
        printf 'window            : %s .. %s (+/-%ss)\n' "$from" "$end" "$window"
        printf 'drm.debug mask    : %#x\n' "$(cat "$KNOB" 2>/dev/null || echo 0)"
        printf 'kernel            : %s\n' "$(uname -r)"
        printf 'cmdline           : %s\n' "$(cat /proc/cmdline)"

        printf '\n===== full kernel log in window =====\n'
        journalctl -k --no-pager -o short-iso --since "$from" --until "$end" 2>/dev/null

        printf '\n===== display-relevant lines only =====\n'
        journalctl -k --no-pager -o short-iso --since "$from" --until "$end" 2>/dev/null |
            grep -iE 'i915|nvidia|drm|crtc|pipe|plane|dp_aux|edp|link|psr|dsb|flip|vblank|underrun' ||
            printf '(none — the pipeline logged nothing display-related in this window)\n'
    } > "$out"

    # Exclude journalctl's own "-- No entries --" / "-- Journal begins... --"
    # placeholder lines, or an empty window counts as 1 and the warning below
    # never fires — which is the exact failure this warning exists to catch.
    local total
    total="$(journalctl -k --no-pager --since "$from" --until "$end" 2>/dev/null |
        grep -cv '^-- ' || true)"

    printf 'wrote %s (%s lines)\n' "$out" "$(wc -l < "$out")"
    printf 'kernel lines in window  : %s\n' "$total"
    printf 'display-relevant matches: %s\n' \
        "$(sed -n '/display-relevant lines only/,$p' "$out" | tail -n +2 | grep -c . || true)"

    if [ "$total" -eq 0 ]; then
        printf '\nWARNING: the window is EMPTY — the kernel logged nothing at all.\n'
        printf 'That almost never means "the glitch left no trace"; it usually means\n'
        printf 'the window is wrong. Check the reported window above against when you\n'
        printf 'actually saw it, and confirm debug was on: %s status\n' "$0"
    elif [ "$(cat "$KNOB" 2>/dev/null || echo 0)" -eq 0 ]; then
        printf '\nNOTE: drm.debug is currently 0. If it was also off during the window,\n'
        printf 'this capture has only default-verbosity lines — enable it and catch the next one.\n'
    fi
}

case "${1:-}" in
    on)     shift; cmd_on "$@" ;;
    off)    cmd_off ;;
    status) cmd_status ;;
    grab)   shift; cmd_grab "$@" ;;
    *)      sed -n '2,30p' "$0" | sed 's/^# \?//'; exit 1 ;;
esac
