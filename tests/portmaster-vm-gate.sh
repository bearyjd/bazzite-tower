#!/usr/bin/env bash
# tests/portmaster-vm-gate.sh — the Portmaster spike's runtime verdict.
#
# RUN THIS ONLY INSIDE A DISPOSABLE VM. Never on the ThinkPad. Portmaster's
# filter chain ends in `filter PORTMASTER-FILTER -m mark --mark 0 -j DROP`:
# anything it has not marked is dropped. A misconfigured daemon on a laptop
# whose recovery path is a TTY is exactly what docs/prp/portmaster-viability-
# spike.md's hard rule exists to prevent.
#
# Where tests/smoke.sh asserts the image is built right and tests/boot-check.sh
# asserts the unit resolved right, this proves the daemon actually *works*, and
# is the only thing that can retire docs/research/portmaster-bootc-spike.md's
# open verdicts. Every check is a predicate with an expected value, because the
# 2026-08-05 run failed by reading `systemctl is-active` before the daemon had
# exited and recording that as a pass.
#
# Exits 0 only if every HARD check passed. Anything else is not a verdict.
#
# ABORT PATH — if V2 (name resolution) fails, the guest may have no usable
# network. Stop the unit and reboot the guest:
#     sudo systemctl stop portmaster.service && sudo reboot
# Do not try to repair chains by hand while the failure mode is still unknown.
set -uo pipefail

UNIT=portmaster.service
CORE=/usr/libexec/portmaster/portmaster-core
CONFIG_SRC=/usr/share/bazzite-tower/portmaster-config.default.json
CONFIG_DST=/var/lib/portmaster/config.json
DWELL=30

fail=0
say() { echo "$*"; }
# hard <desc> <cmd...> — a failure fails the gate.
hard() { local d="$1"; shift; if "$@" > /dev/null 2>&1; then say "  ok   ${d}"; else say "  FAIL ${d}"; fail=1; fi; }
# note <desc> <cmd...> — evidence only, never fails the gate.
note() { local d="$1"; shift; if "$@" > /dev/null 2>&1; then say "  ok   ${d}"; else say "  note ${d} (informational)"; fi; }

# Predicates, so the checks stay readable instead of nesting quotes inside
# `bash -c`. Same idiom as not_failed() in tests/boot-check.sh — and like it,
# each is invoked only indirectly through hard()/note(), which shellcheck
# cannot see.
show() { systemctl show -p "$1" --value "${UNIT}" 2> /dev/null; }
# shellcheck disable=SC2329 # Invoked indirectly through `hard`/`note`.
no_portmaster() { ! grep -q PORTMASTER "$1"; }
# shellcheck disable=SC2329 # Invoked indirectly through `hard`.
has_portmaster() { grep -q PORTMASTER "$1"; }
# shellcheck disable=SC2329 # Invoked indirectly through `hard`.
mount_found() { [[ -n "${MOUNT_LINE}" ]]; }
# shellcheck disable=SC2329 # Invoked indirectly through `hard`.
mount_from_usr() { [[ "${MOUNT_LINE}" == *"${CONFIG_SRC}"* ]]; }
# shellcheck disable=SC2329 # Invoked indirectly through `hard`.
mount_readonly() { [[ " ${MOUNT_LINE} " == *" ro,"* || " ${MOUNT_LINE} " == *" ro "* ]]; }
# shellcheck disable=SC2329 # Invoked indirectly through `hard`.
journal_has() { journalctl -u "${UNIT}" -b --no-pager 2> /dev/null | grep -qi "$1"; }
# shellcheck disable=SC2329 # Invoked indirectly through `hard`.
journal_lacks() { ! journalctl -u "${UNIT}" -b --no-pager 2> /dev/null | grep -qiE "$1"; }

if [[ ${EUID} -ne 0 ]]; then
    say "must run as root (starts a firewall daemon and reads netfilter state)"
    exit 2
fi

# ── Baseline, captured before the daemon has ever run ────────────────────────
say "== baseline =="
RULES_BEFORE=$(mktemp)
RULES_RUNNING=$(mktemp)
RULES_AFTER=$(mktemp)
RULES_IDEMPOTENT=$(mktemp)
# shellcheck disable=SC2064 # Expand the paths now, not at trap time.
trap "rm -f '${RULES_BEFORE}' '${RULES_RUNNING}' '${RULES_AFTER}' '${RULES_IDEMPOTENT}'" EXIT
iptables-save 2> /dev/null | grep -v '^#' > "${RULES_BEFORE}" || true
say "  captured $(wc -l < "${RULES_BEFORE}") iptables rules"
note "no PORTMASTER chains before start" no_portmaster "${RULES_BEFORE}"
# A prior failed run would otherwise poison NRestarts before the dwell begins.
systemctl reset-failed "${UNIT}" 2> /dev/null || true

# ── V1 — the daemon starts AND HOLDS ────────────────────────────────────────
# The 2026-08-05 false pass came from sampling is-active at t=0. Sample
# throughout the dwell instead, and require systemd's own terminal verdict.
say "== V1 daemon reaches and holds active (${DWELL}s dwell) =="
systemctl start "${UNIT}" || true
held=1
for _ in $(seq "${DWELL}"); do
    if ! systemctl is-active --quiet "${UNIT}"; then
        held=0
        break
    fi
    sleep 1
done
hard "active continuously for ${DWELL}s" test "${held}" -eq 1
hard "no restarts" test "$(show NRestarts)" = "0"
hard "systemd result is success" test "$(show Result)" = "success"
hard "restart rate limit is configured" test "$(show StartLimitBurst)" != "0"

# ── V4a — the config bind mount actually applied ────────────────────────────
# This is the one check that proves the update pin is real. Reading the pinned
# VALUE would pass whether or not the mount applied, because the source file
# holds the same values either way — the same false-pass shape as V1's old
# is-active sample. Match the mountpoint field exactly: StateDirectory= implies
# its own bind of /var/lib/portmaster, so a loose path search could match that.
say "== V4a config is bind-mounted read-only from /usr =="
PM_PID=$(show MainPID)
MOUNT_LINE=""
if [[ "${PM_PID:-0}" -gt 0 ]]; then
    MOUNT_LINE=$(awk -v d="${CONFIG_DST}" '$5 == d' "/proc/${PM_PID}/mountinfo" 2> /dev/null || true)
fi
hard "config.json is a mountpoint in the daemon's namespace" mount_found
hard "the mount source is the image copy" mount_from_usr
hard "the mount is read-only" mount_readonly
hard "pinned values are what the daemon sees" \
    jq -e '.core.automaticUpdates == false and .core.automaticIntelUpdates == false' "${CONFIG_DST}"

# ── V3 — the daemon does not fight the read-only config ─────────────────────
# A write attempt against the mount surfaces as an error rather than silently
# degrading. Tested against the FINAL unit, not a writable stand-in, so the
# result describes production rather than a configuration we would never ship.
say "== V3 no config-write errors under the read-only mount =="
hard "no config write failure in the journal" \
    journal_lacks 'config.*(read-only|permission denied|erofs)'

# ── V5 — logging reaches the journal ────────────────────────────────────────
# --log-stdout can parse fine and still produce nothing useful. Require a real
# record, otherwise an empty journal reads as "the daemon said nothing".
say "== V5 daemon logging reaches the journal =="
hard "journal carries daemon output" journal_has portmaster

# ── V2 — the resolver still works ───────────────────────────────────────────
# Portmaster binds localhost:53 and ships conflict detection for 127.0.0.53.
# If this fails, take the abort path at the top of this file.
say "== V2 name resolution survives =="
hard "example.com resolves" getent hosts example.com
note "port 53 ownership" ss -lunp 'sport = :53'

# ── V6 — rules appear, then are cleaned up, and nothing else moves ──────────
# The risk is residual firewall rules stranding the guest's networking, not
# conntrack bookkeeping. Prove Portmaster's chains appear, vanish on stop, and
# that the unrelated Docker/libvirt baseline is byte-identical afterwards.
say "== V6 rule lifecycle =="
iptables-save 2> /dev/null | grep -v '^#' > "${RULES_RUNNING}" || true
hard "PORTMASTER chains present while running" has_portmaster "${RULES_RUNNING}"

systemctl stop "${UNIT}" || true
sleep 5
iptables-save 2> /dev/null | grep -v '^#' > "${RULES_AFTER}" || true
hard "PORTMASTER chains gone after stop" no_portmaster "${RULES_AFTER}"
hard "unrelated ruleset is unchanged" diff -q "${RULES_BEFORE}" "${RULES_AFTER}"

# ── V6b — the cleanup hook is a no-op when nothing was installed ────────────
# ExecStopPost runs after a FAILED start and on every automatic restart, not
# only after a clean stop, so it must tolerate having no rules to remove.
say "== V6b cleanup is idempotent =="
hard "recover-iptables succeeds with no rules installed" timeout 30 "${CORE}" recover-iptables
iptables-save 2> /dev/null | grep -v '^#' > "${RULES_IDEMPOTENT}" || true
hard "a second cleanup still changes nothing" diff -q "${RULES_BEFORE}" "${RULES_IDEMPOTENT}"

# ── V7 — the unit fails closed when the pin source is missing ───────────────
# No daemon is safer than a daemon with an unpinned config. A BindReadOnlyPaths
# setup failure must fail the unit, never start it on upstream's defaults
# (core/automaticUpdates defaults to TRUE).
say "== V7 fails closed without the pin source =="
if mount -o bind,ro /dev/null "${CONFIG_SRC}" 2> /dev/null; then
    systemctl reset-failed "${UNIT}" 2> /dev/null || true
    systemctl start "${UNIT}" 2> /dev/null || true
    sleep 3
    hard "unit does not run without a valid pin source" \
        test "$(show ActiveState)" != "active"
    systemctl stop "${UNIT}" 2> /dev/null || true
    umount "${CONFIG_SRC}" 2> /dev/null || true
    systemctl reset-failed "${UNIT}" 2> /dev/null || true
else
    say "  note could not shadow the pin source; V7 skipped"
fi

say ""
if [[ "${fail}" -eq 0 ]]; then
    say "GATE PASS — record this in docs/research/portmaster-bootc-spike.md."
    say "NOT covered here: verdict 3 (Tailscale MagicDNS + NextDNS + TorGuard),"
    say "and everything the VM cannot model — Docker, libvirt, VPN transitions,"
    say "suspend/resume, NetworkManager resolver rewrites. A pass does not mean"
    say "host-ready."
else
    say "GATE FAIL — do not merge, do not enable on the host."
fi
exit "${fail}"
