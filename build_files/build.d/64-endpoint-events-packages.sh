#!/usr/bin/env bash
set -euo pipefail

# ── Opt-in Vigie endpoint-event feed: packages only, everything disabled ──────
# opensnitchd remote logger (JSON, TCP 127.0.0.1:514) -> a SEPARATE rsyslog
# instance -> TLS (6514) to Vigie. The image only carries the packages; the
# pinned config/units/nft table are inert files under
# /usr/share/snitchwatch/endpoint-events (staged by the Snitchwatch system
# overlay), and enabling the feed is a manual operator step (docs/RUNBOOK.md).
# rsyslog: the daemon; mmjsonparse: JSON field parsing; gnutls: the TLS driver.
dnf install -y rsyslog rsyslog-mmjsonparse rsyslog-gnutls

# The stock rsyslog.service is NOT the opt-in instance (that is its own unit)
# and must never run: journald stays the only logger and nothing listens on
# 514/6514. RPM scriptlets may apply an enable preset at install time, so disable explicitly, for every
# unit the three packages ship (no `|| true`: a renamed unit fails the build).
# Disable, never mask: the opt-in instance reuses the rsyslogd binary only.
shipped="$(rpm -ql rsyslog rsyslog-mmjsonparse rsyslog-gnutls)"
mapfile -t units < <(grep -E '^/(usr/)?lib/systemd/system/[^/]+\.(service|socket|timer|path)$' <<<"${shipped}" | sed 's|.*/||' | sort -u)
if [[ ! " ${units[*]} " == *" rsyslog.service "* ]]; then
    echo "rsyslog.service not shipped by the rsyslog packages; unit names drifted" >&2
    exit 1
fi
systemctl disable "${units[@]}"
for unit in "${units[@]}"; do
    state="$(systemctl is-enabled "${unit}" 2>/dev/null || true)"
    case "${state}" in
        disabled | static) ;;
        *)
            echo "${unit} is not disabled after install (state '${state:-unknown}')" >&2
            exit 1
            ;;
    esac
done
