#!/usr/bin/env bash
set -euo pipefail

# ── Desktop stall detector ───────────────────────────────────────────────────
# This machine has at least one freeze mode that NO kernel watchdog reports:
# the i915 GuC TLB invalidation timeout (drm/i915 issue 14469, unfixed
# upstream, Intel attribute it to the GuC dying while waking from RC6). The
# soft-lockup watchdog only fires on a CPU spinning in kernel mode and
# hung_task only after 120s, so an 8-second stall caused by a blocked kernel
# worker leaves no trace at all.
#
# The detector samples CLOCK_MONOTONIC and, on a gap, records which workers
# were stuck in D state. That is what identifies the subsystem — without it a
# freeze is just "the machine paused". Output goes to the journal; query it
# with `ujust freeze-report`.
systemctl enable bazzite-tower-stall-detect.service
