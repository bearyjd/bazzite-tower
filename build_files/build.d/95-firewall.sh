#!/usr/bin/env bash
set -euo pipefail

# ── Application firewall ─────────────────────────────────────────────────────
# The selector defaults to the proven OpenSnitch setup.  Portmaster is an
# explicitly-built, disabled-by-default VM spike; its script must never make it
# into a normal :latest build unless FIREWALL_DAEMON=portmaster was supplied.
# `portmaster` is intentionally a VM-only spike.  Its build is opt-in and the
# production OpenSnitch block below remains the default path.
case "${FIREWALL_DAEMON:-opensnitch}" in
    opensnitch) ;;
    portmaster)
        # shellcheck disable=SC1091 # Build-time path is relative to this file.
        source "$(dirname "${BASH_SOURCE[0]}")/../firewall/portmaster.sh"
        ;;
    *)
        echo "Unknown FIREWALL_DAEMON='${FIREWALL_DAEMON:-}'." >&2
        exit 1
        ;;
esac

# ── OpenSnitch: interactive application firewall ──────────────────────────────
if [[ "${FIREWALL_DAEMON:-opensnitch}" == "opensnitch" ]]; then
    # Not in Fedora/RPM Fusion, and the one Fedora-44 COPR (androfuchs/opensnitch) has
    # zero builds — install the upstream-signed release RPM directly, pinned by
    # sha256 (no published GPG key to check against, so hash-pin is the trust anchor,
    # same reproducibility bar as the Containerfile's digest-pinned base image).
    # Daemon only (no GUI package) — the GUI is Snitchwatch (github.com/bearyjd/
    # snitchwatch), installed per-user, and it conflicts with upstream opensnitch-ui.
    #
    # Runtime deps must be installed explicitly: the rpm2cpio extraction below
    # bypasses RPM dependency resolution entirely, so nothing pulls in what
    # opensnitchd's ELF headers ask for. `libnetfilter_queue.so.1` is a hard
    # DT_NEEDED of /usr/bin/opensnitchd and is NOT in the bazzite-nvidia base
    # (verified against the pinned base's /usr/lib64) — without it the daemon
    # cannot exec at all and opensnitch.service fails every boot. nftables (the
    # configured Firewall backend) and libnfnetlink.so.0 are already in the base;
    # naming nftables here is idempotent and pins the dependency against base drift.
    # NOT installed: `info`, which the RPM requires only for its %post install-info
    # scriptlet — the extraction path never runs scriptlets.
    dnf install -y libnetfilter_queue nftables

    opensnitch_version="1.8.0"
    opensnitch_rpm="opensnitch-${opensnitch_version}-1.x86_64.rpm"
    opensnitch_sha256="e06e9119daf764e56455b61c319e496274c0274bb53bb94a0ff1ab72967fea7d"
    curl -fsSL -o "/tmp/${opensnitch_rpm}" \
        "https://github.com/evilsocket/opensnitch/releases/download/v${opensnitch_version}/${opensnitch_rpm}"
    echo "${opensnitch_sha256}  /tmp/${opensnitch_rpm}" | sha256sum -c -
    # Extract with rpm2cpio|cpio instead of `dnf install`/`rpm -i`, for two
    # reasons found the hard way:
    #   1. The RPM's %post calls systemctl in a way that expects a live systemd
    #      bus (not just the enable-by-symlink our other dnf installs above rely
    #      on), which aborts the whole transaction under a plain `podman build`
    #      with no running PID 1.
    #   2. On a base shipped with RPM 6's SQLite rpmdb backend (e.g. upstream
    #      bazzite-nvidia:stable as of 2026-07), a `dnf`/`rpm` transaction's
    #      database writes were silently lost across the buildah layer commit —
    #      the installed *files* persisted but the rpmdb entries reverted, and
    #      `rpm --rebuilddb` as a fixup failed outright ("failed to replace old
    #      database with new database"). Extracting the files directly sidesteps
    #      rpmdb entirely, so it's immune to this regardless of which rpmdb
    #      backend a future base ships. Trade-off: `rpm -q opensnitch` won't
    #      find it — smoke.sh checks for the binary instead.
    ( cd / && rpm2cpio "/tmp/${opensnitch_rpm}" | cpio -idm )
    rm -f "/tmp/${opensnitch_rpm}"

    # Overwrite the RPM's default-config.json with the Snitchwatch-tuned one. This
    # runs *after* the extraction on purpose: `COPY system_files/` happens earlier in
    # the Containerfile, and cpio's overwrite behaviour for an already-present file
    # is mtime-dependent, so staging the file under /usr/share and installing it here
    # is the only deterministic order. The /usr/share copy is also the pristine
    # image-intent reference — /etc is 3-way merged on bootc upgrades, so once this
    # file is edited locally it stops tracking the image, and `diff`ing the two shows
    # exactly what drifted.
    #
    # Rejected alternative: opensnitchd takes `-config-file`, so a systemd drop-in
    # could point it straight at the read-only /usr copy and sidestep the /etc
    # 3-way merge entirely. Don't — Snitchwatch writes the daemon config back when
    # settings change (that is how DefaultAction gets toggled from the GUI), and a
    # config under /usr is immutable on an ostree system. /etc is the correct live
    # path precisely because it is writable.
    #
    # Three deliberate deltas from the RPM's shipped default:
    #   Server.Address 127.0.0.1:50051 — the Snitchwatch bridge's gRPC listener
    #     (a per-user service that is NOT part of this image) rather than upstream
    #     opensnitch-ui's unix:///tmp/osui.sock.
    #   ProcMonitorMethod "proc"       — NOT "ebpf". The v1.8.0 RPM's bundled eBPF
    #     module fails to load on this image's 6.19/7.x kernels ("unable to load
    #     eBPF module (opensnitch.o)", snitchwatch#6) and the daemon degrades badly.
    #     Revisit only when an opensnitch release ships an eBPF module built for
    #     this kernel.
    #   DefaultAction "allow"          — fail open. "deny" is the designed end state
    #     but denies every new outbound connection on any boot where no UI/bridge is
    #     answering prompts, and the bridge is still installed by hand. To flip it
    #     to "deny" once the bridge user-service is in place, edit the value in
    #     system_files/usr/share/bazzite-tower/opensnitchd-default-config.json and
    #     the matching assertion in tests/smoke.sh. See README "OpenSnitch".
    install -D -m 0644 /usr/share/bazzite-tower/opensnitchd-default-config.json \
        /etc/opensnitchd/default-config.json

    systemctl enable opensnitch.service
    printf '%s\n' opensnitch > /usr/share/bazzite-tower/firewall-daemon
    # Keep the unavailable alternative impossible to start without leaving a
    # rollback-persistent /etc mask behind.
    ln -sf /dev/null /usr/lib/systemd/system/portmaster.service
fi

