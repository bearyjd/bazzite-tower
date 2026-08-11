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
# 2026-08-05 run failed by reading `systemctl is-active` once, before the daemon
# had exited, and recording that as a pass.
#
# Exits 0 only if every HARD check passed. Anything else is not a verdict.
#
# ABORT PATH — if V2 (name resolution) fails, the guest may have no usable
# network. Stop the unit and reboot the guest:
#     sudo systemctl stop portmaster.service && sudo reboot
# Do not try to repair chains by hand while the failure mode is still unknown.
set -uo pipefail

UNIT=portmaster.service
UNIT_FILE=/usr/lib/systemd/system/portmaster.service
CORE=/usr/libexec/portmaster/portmaster-core
CONFIG_SRC=/usr/share/bazzite-tower/portmaster-config.default.json
CONFIG_DST=/var/lib/portmaster/config.json
DWELL=30
WORK=/tmp/pmgate-work
JOURNAL="${WORK}/journal.txt"
NS_CONFIG="${WORK}/config-in-ns.json"

fail=0
say() { echo "$*"; }
# hard <desc> <cmd...> — a failure fails the gate.
hard() { local d="$1"; shift; if "$@" > /dev/null 2>&1; then say "  ok   ${d}"; else say "  FAIL ${d}"; fail=1; fi; }
# note <desc> <cmd...> — evidence only, never fails the gate.
note() { local d="$1"; shift; if "$@" > /dev/null 2>&1; then say "  ok   ${d}"; else say "  note ${d} (informational)"; fi; }
# skip <desc> <why> — could not be evaluated. Does NOT set fail: whatever made
# it unevaluatable has already failed and owns the verdict. Reporting these as
# FAIL misattributes a missing precondition to the thing being measured.
skip() { say "  skip ${1} (${2})"; }

# A fixed IP, not a hostname, for the interception probe (V8): a hostname
# would make a failed connect ambiguous between "DNS didn't resolve" and
# "TCP was blocked", and V8 exists specifically to tell those apart. Cloudflare
# 1.1.1.1:443 is stable enough for a disposable-VM check that runs once.
PROBE_IP=1.1.1.1
PROBE_PORT=443

# Predicates, so checks stay readable instead of nesting quotes inside
# `bash -c`. Same idiom as not_failed() in tests/boot-check.sh -- and like it,
# each is invoked only indirectly through hard()/note(), which shellcheck
# cannot see.
#
# NOTE ON PIPES: this script runs under `set -o pipefail`, and `cmd | grep -q`
# is a trap under it -- grep exits on first match, cmd takes SIGPIPE (141), and
# pipefail reports the whole pipeline as failed even though the match SUCCEEDED.
# That produced a false FAIL on V5 and could have produced a false PASS on V3.
# So: dump once to a file, then grep the file. Never pipe into `grep -q` here.
show() { systemctl show -p "$1" --value "${UNIT}" 2> /dev/null; }
# shellcheck disable=SC2329 # Invoked indirectly through `hard`/`note`.
unit_has_our_changes() {
    local n
    n=$(grep -cE 'log-stdout|BindReadOnlyPaths|StartLimitBurst' "${UNIT_FILE}" 2> /dev/null) || n=0
    [[ "${n}" -ge 3 ]]
}
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
journal_has() { grep -qi "$1" "${JOURNAL}"; }
# shellcheck disable=SC2329 # Invoked indirectly through `hard`.
journal_lacks() { ! grep -qiE "$1" "${JOURNAL}"; }
journal_dump() { journalctl -u "${UNIT}" -b --no-pager > "${JOURNAL}" 2> /dev/null || true; }
# shellcheck disable=SC2329 # Invoked indirectly through `hard`.
tcp_reachable() { timeout 5 bash -c "exec 3<>/dev/tcp/${PROBE_IP}/${PROBE_PORT}" 2> /dev/null; }
# shellcheck disable=SC2329 # Invoked indirectly through `hard`.
tcp_blocked() { ! tcp_reachable; }
# Swap the file BindReadOnlyPaths pulls from, restart, and wait for the
# terminal state -- same bind-over-CONFIG_SRC technique V7 uses to prove
# fail-closed, reused here to prove the daemon actually enforces whatever
# config it reads rather than just holding chains open.
# shellcheck disable=SC2329 # Invoked indirectly through `hard`.
restart_with_config() {
    umount "${CONFIG_SRC}" 2> /dev/null || true
    systemctl stop "${UNIT}" 2> /dev/null || true
    if [[ -n "$1" ]]; then
        mount -o bind,ro "$1" "${CONFIG_SRC}" || return 1
    fi
    systemctl reset-failed "${UNIT}" 2> /dev/null || true
    systemctl start "${UNIT}" || true
    local activated=0
    for _ in $(seq 1 10); do
        systemctl is-active --quiet "${UNIT}" && { activated=1; break; }
        sleep 1
    done
    [[ "${activated}" -eq 1 ]] || return 1
    # A single is-active read right after start is exactly the trap the
    # 2026-08-05 postmortem (see file header) already burned this repo on:
    # Type=simple marks a unit active the instant ExecStart forks, before a
    # near-immediate crash (observed here taking well under a second) is
    # detected. Settle and re-verify before trusting it.
    sleep 3
    systemctl is-active --quiet "${UNIT}"
}
# Normalise the [packets:bytes] counters that iptables-save always emits on
# `:CHAIN POLICY` lines. They increment with every packet the guest handles, so
# comparing raw output measures traffic rather than rules and can never match on
# a live system.
snapshot_rules() {
    iptables-save 2> /dev/null | grep -v '^#' | sed -E 's/\[[0-9]+:[0-9]+\]/[0:0]/g' > "$1" || true
}

if [[ ${EUID} -ne 0 ]]; then
    say "must run as root (starts a firewall daemon and reads netfilter state)"
    exit 2
fi

rm -rf "${WORK}"
mkdir -p "${WORK}"
RULES_BEFORE="${WORK}/rules-before"
RULES_RUNNING="${WORK}/rules-running"
RULES_AFTER="${WORK}/rules-after"
RULES_IDEMPOTENT="${WORK}/rules-idempotent"
# Deliberately NOT cleaned up on exit: a failed run needs its evidence.
say "evidence kept in ${WORK}/"

# ── V0 — the image actually contains the code under test ────────────────────
# The 2026-08-05 run graded a three-day-old image for a full cycle, because
# _rootful_load_image reported success while silently declining to move the
# tag, and every guard checked artifact *identity* rather than content. Assert
# the code is present before grading anything. Cheap, and decisive.
say "== V0 image under test contains the code under test =="
hard "unit carries the pinned flags and the config bind mount" unit_has_our_changes
if [[ "${fail}" -ne 0 ]]; then
    say ""
    say "ABORT — this image predates the changes under test. Grading it would"
    say "produce a verdict about the wrong code. Rebuild and re-run."
    exit 1
fi

# ── Baseline, captured once the ruleset has settled ─────────────────────────
# Docker installs its chains asynchronously during boot. A baseline taken
# before that finishes makes the later "unrelated ruleset unchanged" check
# fail against a moving target, which is what happened on 2026-08-05.
say "== baseline =="
stable=0
for _ in $(seq 1 12); do
    snapshot_rules "${RULES_BEFORE}"
    sleep 5
    snapshot_rules "${WORK}/rules-probe"
    if diff -q "${RULES_BEFORE}" "${WORK}/rules-probe" > /dev/null 2>&1; then
        stable=1
        break
    fi
done
hard "netfilter ruleset settled before baseline" test "${stable}" -eq 1
say "  baseline: $(wc -l < "${RULES_BEFORE}") rules"
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

DAEMON_UP=0
systemctl is-active --quiet "${UNIT}" && DAEMON_UP=1

# ── V4a — the config bind mount actually applied ────────────────────────────
# The one check that proves the update pin is real. Reading the pinned VALUE
# from outside the unit's namespace proves nothing: BindReadOnlyPaths is
# unit-private (systemd.exec: "not visible in the host's mount table"), so
# outside it CONFIG_DST is just the empty file systemd created as the mount
# destination. Read it through nsenter, from the daemon's own namespace.
# Match the mountpoint field exactly: StateDirectory= implies its own bind of
# /var/lib/portmaster, which a loose path search would match instead.
say "== V4a config is bind-mounted read-only from /usr =="
if [[ "${DAEMON_UP}" -eq 1 ]]; then
    PM_PID=$(show MainPID)
    MOUNT_LINE=$(awk -v d="${CONFIG_DST}" '$5 == d' "/proc/${PM_PID}/mountinfo" 2> /dev/null || true)
    hard "config.json is a mountpoint in the daemon's namespace" mount_found
    hard "the mount source is the image copy" mount_from_usr
    hard "the mount is read-only" mount_readonly
    nsenter -t "${PM_PID}" -m cat "${CONFIG_DST}" > "${NS_CONFIG}" 2> /dev/null || true
    hard "pinned values are what the daemon actually reads" \
        jq -e '.core.automaticUpdates == false and .core.automaticIntelUpdates == false' "${NS_CONFIG}"
else
    skip "V4a mount checks" "daemon not running, see V1"
fi

# ── V3 — the daemon does not fight the read-only config ─────────────────────
# Tested against the FINAL unit, not a writable stand-in, so the result
# describes production rather than a configuration we would never ship.
say "== V3 no config-write errors under the read-only mount =="
journal_dump
hard "no config write failure in the journal" \
    journal_lacks 'config.*(read-only|permission denied|erofs)'

# ── V5 — logging reaches the journal ────────────────────────────────────────
# --log-stdout can parse fine and still produce nothing useful.
say "== V5 daemon logging reaches the journal =="
hard "journal carries daemon output" journal_has "running Portmaster"

# ── V2 — the resolver still works ───────────────────────────────────────────
# Portmaster binds localhost:53 and ships conflict detection for 127.0.0.53.
# If this fails, take the abort path at the top of this file.
say "== V2 name resolution survives =="
hard "example.com resolves" getent hosts example.com
note "port 53 ownership" ss -lunp 'sport = :53'

# ── V8 — the daemon actually enforces what it decides, not just chains ──────
# Every check above proves the daemon boots, keeps its config, and installs
# netfilter rules. None of them prove a real connection is ever matched to a
# verdict: a daemon that boots, resolves DNS, and installs empty chains passes
# all of it while intercepting nothing. filter/defaultAction (upstream default:
# "permit") is the one knob that flips a connection's outcome without a
# profile, a UI, or credentials, so it is usable headlessly in this gate. Prove
# both directions: baseline reachable under the pinned config's implicit
# permit, then unreachable once defaultAction is forced to "block", using a
# fixed IP so the result cannot be confused with a DNS failure.
say "== V8 interception enforces defaultAction, not just chain presence =="
if [[ "${DAEMON_UP}" -eq 1 ]]; then
    hard "probe reachable under default (implicit permit) action" tcp_reachable

    BLOCK_CONFIG="${WORK}/config-block.json"
    printf '%s\n' '{"core":{"automaticUpdates":false,"automaticIntelUpdates":false},"filter":{"defaultAction":"block"}}' \
        > "${BLOCK_CONFIG}"
    if hard "daemon restarts under a forced defaultAction=block config" restart_with_config "${BLOCK_CONFIG}"; then
        hard "probe blocked under defaultAction=block" tcp_blocked
    else
        skip "probe blocked under defaultAction=block" "daemon did not come back up under the block config"
    fi

    if hard "daemon restarts back under the pinned (permit) config" restart_with_config ""; then
        hard "probe reachable again after reverting to the pinned config" tcp_reachable
        DAEMON_UP=1
    else
        skip "probe reachable again after reverting to the pinned config" "daemon did not come back up"
        DAEMON_UP=0
    fi
else
    skip "V8 interception checks" "daemon not running, see V1"
fi

# ── V6 — rules appear, then are cleaned up, and nothing else moves ──────────
# The risk is residual firewall rules stranding the guest, not conntrack
# bookkeeping. Prove Portmaster's chains appear, vanish on stop, and that the
# unrelated Docker/libvirt baseline is byte-identical afterwards.
say "== V6 rule lifecycle =="
if [[ "${DAEMON_UP}" -eq 1 ]]; then
    snapshot_rules "${RULES_RUNNING}"
    hard "PORTMASTER chains present while running" has_portmaster "${RULES_RUNNING}"
else
    skip "PORTMASTER chains present while running" "daemon not running, see V1"
fi

systemctl stop "${UNIT}" || true
sleep 5
snapshot_rules "${RULES_AFTER}"
hard "PORTMASTER chains gone after stop" no_portmaster "${RULES_AFTER}"
hard "unrelated ruleset is unchanged" diff -q "${RULES_BEFORE}" "${RULES_AFTER}"
if ! diff -q "${RULES_BEFORE}" "${RULES_AFTER}" > /dev/null 2>&1; then
    say "  --- ruleset delta (baseline -> after) ---"
    diff "${RULES_BEFORE}" "${RULES_AFTER}" | sed 's/^/    /' | head -20
fi

# ── V6b — the cleanup hook is a no-op when nothing was installed ────────────
# ExecStopPost runs after a FAILED start and on every automatic restart, not
# only after a clean stop, so it must tolerate having no rules to remove.
say "== V6b cleanup is idempotent =="
hard "recover-iptables succeeds with no rules installed" timeout 30 "${CORE}" recover-iptables
snapshot_rules "${RULES_IDEMPOTENT}"
hard "a second cleanup still changes nothing" diff -q "${RULES_AFTER}" "${RULES_IDEMPOTENT}"

# ── V7 — the unit fails closed when the pin source is missing ───────────────
# No daemon is safer than a daemon on upstream's defaults, which enable updates.
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
    skip "V7 fail-closed check" "could not shadow the pin source"
fi

say ""
if [[ "${fail}" -eq 0 ]]; then
    say "GATE PASS — record this in docs/research/portmaster-bootc-spike.md."
    say "NOT covered here: verdict 3 (Tailscale MagicDNS + NextDNS + TorGuard),"
    say "per-process/per-profile rule matching (V8 only proves the global"
    say "defaultAction fallback is enforced), and everything the VM cannot"
    say "model: libvirt, VPN transitions, suspend/resume, NetworkManager"
    say "resolver rewrites. A pass does not mean host-ready."
else
    say "GATE FAIL — do not merge, do not enable on the host."
    say "Evidence in ${WORK}/ (journal.txt, rules-*, config-in-ns.json)."
fi
exit "${fail}"
