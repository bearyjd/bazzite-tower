#!/usr/bin/env bash
set -euo pipefail

# Reject invalid combinations before any downloads or package transactions.
bash "$(dirname "${BASH_SOURCE[0]}")/../firewall/snitchwatch-system-mode.sh"
install -d -m 0755 /usr/share/bazzite-tower
printf '%s\n' "${SNITCHWATCH_BRIDGE:-legacy}" > /usr/share/bazzite-tower/snitchwatch-bridge-profile

# ── Application firewall ─────────────────────────────────────────────────────
# The selector defaults to (and only supports) OpenSnitch; any other value is
# rejected below.
case "${FIREWALL_DAEMON:-opensnitch}" in
    opensnitch) ;;
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
    # DT_NEEDED of /usr/bin/opensnitchd and is NOT in the pinned base
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
    # Legacy: the eBPF modules staged by the Containerfile were built from the
    # OpenSnitch commit of the v1.8.0 release. Refuse unless this file is exactly
    # the RPM they are pinned to (name, version, release and sha256), so an RPM
    # bump without re-pinning the modules fails here instead of pairing wrong
    # modules with the daemon. The upstream RPM is unsigned: `rpm -qp` may warn.
    if [[ "${SNITCHWATCH_BRIDGE:-legacy}" == legacy ]]; then
        python3 -B "$(dirname "${BASH_SOURCE[0]}")/../firewall/snitchwatch-system-ebpf.py" check-rpm \
            --pins "$(dirname "${BASH_SOURCE[0]}")/../firewall/snitchwatch-system-ebpf-pins.json" "/tmp/${opensnitch_rpm}"
    fi
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
    # eBPF objects. Both profiles overwrite the RPM payload (built against fixed
    # headers, not this kernel) with objects compiled at image build (Track A,
    # Containerfile stages ebpf-kernel and snitchwatch-ebpf[-rebuild] / ebpf-legacy
    # [-rebuild]) against this BASE_IMAGE's own kernel-devel headers, from the
    # same OpenSnitch commit as the daemon that loads them: system a1353848 (its
    # source-built daemon), legacy b404c4c6 (v1.8.0, the RPM daemon, checked above).
    # They were staged under /usr/share because cpio's overwrite of an existing
    # file depends on mtime; install here, after the extraction, is the
    # deterministic order. The manifest records kernel, commit, toolchain and
    # per-object sha256; the check below binds it to the one /usr/lib/modules/*
    # release and the bytes on disk.
    # ProcMonitorMethod is "ebpf" with Ebpf.ModulesPath pinned to this directory
    # (config below); these per-kernel matched objects are what it loads. Whether
    # the daemon really loaded them is a runtime fact: the daemon falls back to
    # /proc silently, so bazzite-tower-opensnitch-ebpf-check --runtime decides.
    for object in opensnitch.o opensnitch-procs.o opensnitch-dns.o; do
        install -m 0644 "/usr/share/bazzite-tower/opensnitch-ebpf/${object}" "/usr/lib/opensnitchd/ebpf/${object}"
    done
    rm -r /usr/share/bazzite-tower/opensnitch-ebpf
    python3 /usr/libexec/bazzite-tower-opensnitch-ebpf-check

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
    #   ProcMonitorMethod "ebpf"       — default. The image supplies all three BPF
    #     objects built per kernel from the daemon's own commit, and
    #     Ebpf.ModulesPath pins them (otherwise the daemon searches
    #     /usr/local/lib/opensnitchd/ebpf first, where a stale object could
    #     shadow them). "proc" is the documented one-key fallback
    #     (docs/RUNBOOK.md); readiness reports it as a WARN, not drift.
    #   DefaultAction "allow"          — permanent default for both profiles;
    #     deny enforcement always requires explicit operator opt-in. Without a
    #     decision client, deny can block every new unruled outbound connection.
    #     The system profile below replaces only the address with the root-only
    #     Unix socket, retaining allow/ebpf. Both profiles are asserted in
    #     tests/smoke.sh.
    install -D -m 0644 /usr/share/bazzite-tower/opensnitchd-default-config.json \
        /etc/opensnitchd/default-config.json

    if [[ "${SNITCHWATCH_BRIDGE:-legacy}" == system ]]; then
        python3 /usr/libexec/snitchwatch/verify-system-manifest.py --image-tree
        # RPM payload extraction above must finish before replacing its daemon.
        # The separate immutable Go receipt binds the candidate and installed
        # executable to the reviewed downstream patch and reproducible build.
        install -m 0755 /usr/share/snitchwatch/daemon/opensnitchd /usr/bin/opensnitchd
        python3 /usr/libexec/snitchwatch/verify-system-daemon.py
        install -m 0644 /usr/share/bazzite-tower/opensnitchd-system-bridge-config.json \
            /etc/opensnitchd/default-config.json
        # The overlay is copied before build.sh, but explicitly create these
        # identities here too: 40-sysusers predates the firewall step and a later
        # staging refactor must not make tmpfiles/socket startup race sysusers.
        systemd-sysusers /usr/lib/sysusers.d/snitchwatch.conf
        systemd-analyze verify snitchwatch-system-bridge.service \
            snitchwatch-system-bridge-grpc.socket snitchwatch-system-bridge-gui.socket opensnitch.service
        systemctl enable snitchwatch-system-bridge-grpc.socket snitchwatch-system-bridge-gui.socket
    fi
    systemctl enable opensnitch.service
    printf '%s\n' opensnitch > /usr/share/bazzite-tower/firewall-daemon
fi
