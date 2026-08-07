#!/usr/bin/env bash
set -euo pipefail

# ── i915 resume-regression watcher ────────────────────────────────────────────
# Containerfile pins the base image to a 6.19.x-ogc kernel specifically to dodge
# the Meteor Lake cx0 DPLL s2idle-resume regression (see
# docs/research/i915-mtl-resume-2026-06-20.md). This periodic check is the
# machine-checkable signal that comment points at: pre-7.0 it's a no-op; once
# the kernel moves to 7.0+ (e.g. after re-evaluating the pin) it greps the
# current boot's kernel log for the known-bad DPLL/flip_done signature and logs
# a warning so a human notices before assuming 7.0+ is safe. Helper + units
# ship in system_files/.
systemctl enable i915-resume-fix-check.timer

