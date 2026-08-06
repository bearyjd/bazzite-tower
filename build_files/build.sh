#!/usr/bin/env bash
# build.sh — runs inside the container image build
set -euo pipefail

# ── QEMU / libvirt / KVM stack ────────────────────────────────────────────────
# config-network/config-nwfilter ship the default NAT network and nwfilter rules.
dnf install -y \
    edk2-ovmf \
    guestfs-tools \
    libvirt \
    libvirt-client \
    libvirt-daemon-config-network \
    libvirt-daemon-config-nwfilter \
    libvirt-daemon-kvm \
    qemu-kvm \
    spice-gtk3 \
    virt-install \
    virt-manager \
    virt-viewer

# ── DX-equivalent dev tooling (Fedora repos) ──────────────────────────────────
dnf install -y \
    android-tools \
    ccache \
    flatpak-builder \
    podman-machine \
    podman-tui \
    rclone \
    restic \
    zsh

# ── Docker CE ─────────────────────────────────────────────────────────────────
# Mirror of https://download.docker.com/linux/fedora/docker-ce.repo with every
# section disabled. We flip docker-ce-stable on for the install transaction only
# via --enablerepo, so the repo file is inert at runtime (no background updates,
# no surprise upgrades). Bazzite ships podman-docker which owns /usr/bin/docker;
# remove it first so docker-ce can land cleanly.
install -Dm644 /dev/stdin /etc/yum.repos.d/docker-ce.repo <<'EOF'
[docker-ce-stable]
name=Docker CE Stable - $basearch
baseurl=https://download.docker.com/linux/fedora/$releasever/$basearch/stable
enabled=0
gpgcheck=1
gpgkey=https://download.docker.com/linux/fedora/gpg

[docker-ce-stable-debuginfo]
name=Docker CE Stable - Debuginfo $basearch
baseurl=https://download.docker.com/linux/fedora/$releasever/debug-$basearch/stable
enabled=0
gpgcheck=1
gpgkey=https://download.docker.com/linux/fedora/gpg

[docker-ce-stable-source]
name=Docker CE Stable - Sources
baseurl=https://download.docker.com/linux/fedora/$releasever/source/stable
enabled=0
gpgcheck=1
gpgkey=https://download.docker.com/linux/fedora/gpg

[docker-ce-test]
name=Docker CE Test - $basearch
baseurl=https://download.docker.com/linux/fedora/$releasever/$basearch/test
enabled=0
gpgcheck=1
gpgkey=https://download.docker.com/linux/fedora/gpg

[docker-ce-test-debuginfo]
name=Docker CE Test - Debuginfo $basearch
baseurl=https://download.docker.com/linux/fedora/$releasever/debug-$basearch/test
enabled=0
gpgcheck=1
gpgkey=https://download.docker.com/linux/fedora/gpg

[docker-ce-test-source]
name=Docker CE Test - Sources
baseurl=https://download.docker.com/linux/fedora/$releasever/source/test
enabled=0
gpgcheck=1
gpgkey=https://download.docker.com/linux/fedora/gpg

[docker-ce-nightly]
name=Docker CE Nightly - $basearch
baseurl=https://download.docker.com/linux/fedora/$releasever/$basearch/nightly
enabled=0
gpgcheck=1
gpgkey=https://download.docker.com/linux/fedora/gpg

[docker-ce-nightly-debuginfo]
name=Docker CE Nightly - Debuginfo $basearch
baseurl=https://download.docker.com/linux/fedora/$releasever/debug-$basearch/nightly
enabled=0
gpgcheck=1
gpgkey=https://download.docker.com/linux/fedora/gpg

[docker-ce-nightly-source]
name=Docker CE Nightly - Sources
baseurl=https://download.docker.com/linux/fedora/$releasever/source/nightly
enabled=0
gpgcheck=1
gpgkey=https://download.docker.com/linux/fedora/gpg
EOF

dnf remove -y podman-docker || true

dnf install -y --enablerepo=docker-ce-stable \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin

# ── System users from packaged sysusers.d snippets ────────────────────────────
# A plain `dnf install` in a container build does NOT run systemd-sysusers the
# way an rpm-ostree compose does, so packages that declare their accounts via
# /usr/lib/sysusers.d/*.conf — notably qemu — never get those users created.
# Without the 'qemu' user, virtqemud aborts at startup ("Failed to parse user
# 'qemu'") and the socket-activated service crash-loops into start-limit-hit on
# every boot, so qemu:///system never comes up.
#
# Complication seen on this base: it ships orphan lines in /etc/shadow and
# /etc/gshadow — an entry whose name has no matching /etc/passwd or /etc/group
# (e.g. `qemu` in shadow, `qat` in gshadow, and possibly more). sysusers writes
# passwd+group+shadow+gshadow as one transaction and aborts the WHOLE thing when
# it hits any such pre-existing line ("Group X already exists"), so it silently
# creates nothing — including the `docker` group, whose absence makes docker.socket
# fail at every boot ("Failed to resolve group 'docker': Unknown group") and the
# group then gets created late at a >1000 gid. Strip every orphan first (generic:
# keep only shadow/gshadow lines whose name has a matching passwd/group entry),
# then materialize. `cat >` rewrites in place, preserving the 0000 root:root perms.
awk -F: 'NR==FNR{seen[$1];next} ($1 in seen)' /etc/group  /etc/gshadow > /tmp/gshadow.f && cat /tmp/gshadow.f > /etc/gshadow && rm -f /tmp/gshadow.f
awk -F: 'NR==FNR{seen[$1];next} ($1 in seen)' /etc/passwd /etc/shadow  > /tmp/shadow.f  && cat /tmp/shadow.f  > /etc/shadow  && rm -f /tmp/shadow.f
systemd-sysusers
# Belt-and-suspenders for the accounts our services need, in case a sysusers.d
# snippet is absent or a future base change reintroduces the orphan problem. Both
# are resolved by name, so dynamic system ids (-r) are fine.
#   - qemu user+group: libvirt resolves 'qemu' by name; virtqemud aborts without it.
#   - docker group: docker.socket sets the API socket group to 'docker' at early
#     boot — bake it as a system group so it resolves before docker.socket starts.
getent group  qemu   >/dev/null || groupadd -r qemu
getent passwd qemu   >/dev/null || useradd  -r -g qemu -d / -s /sbin/nologin -c "qemu user" qemu
getent group  docker >/dev/null || groupadd -r docker

# ── docker-in-docker: load iptable_nat at boot ────────────────────────────────
install -Dm644 /dev/stdin /etc/modules-load.d/iptable_nat.conf <<'EOF'
iptable_nat
EOF

# ── libvirt: modular daemons + Docker service ─────────────────────────────────
# Bazzite/F44+ ships modular libvirt: one socket-activated daemon per driver
# (virtqemud, virtnetworkd, ...) instead of the monolithic libvirtd. Mask the
# legacy libvirtd.service so it can't race the modular daemons — that race is the
# root cause of the broken stock `ujust setup-virtualization`.
systemctl mask libvirtd.service

# Enable the per-driver sockets. Each primary .socket carries Also= directives
# that pull in its matching -ro and -admin sockets, so the primaries are enough.
systemctl enable virtqemud.socket
systemctl enable virtnetworkd.socket
systemctl enable virtnodedevd.socket
systemctl enable virtnwfilterd.socket
systemctl enable virtstoraged.socket

# virtproxyd serves the legacy /run/libvirt/libvirt-sock path that older tooling
# expects, forwarding to the modular daemons. It is the modular replacement for
# libvirtd.socket: the two declare Conflicts= on the same socket path, so we
# enable only this one (libvirtd.socket would be inert anyway — its service is
# masked).
systemctl enable virtproxyd.socket

# Docker daemon starts at boot (Docker CE is baked in alongside Podman).
systemctl enable docker.service

# ── libvirt default NAT network: autostart on boot ────────────────────────────
# libvirt-daemon-config-network ships the default NAT network definition. Mark it
# autostart by creating the symlink `virsh net-autostart` would — the daemons
# aren't running at build time, so virsh itself can't be used. Idempotent and
# guarded on the definition existing.
if [[ -f /etc/libvirt/qemu/networks/default.xml ]]; then
    install -d /etc/libvirt/qemu/networks/autostart
    ln -sfn ../default.xml /etc/libvirt/qemu/networks/autostart/default.xml
fi

# ── Polkit: wheel → qemu:///system access (immediate, no logout required) ─────
install -Dm644 /dev/stdin /etc/polkit-1/rules.d/50-libvirt-wheel.rules <<'EOF'
polkit.addRule(function(action, subject) {
    if ((action.id == "org.libvirt.unix.manage" ||
         action.id == "org.libvirt.unix.monitor") &&
        subject.isInGroup("wheel")) {
        return polkit.Result.YES;
    }
});
EOF

# ── First-boot oneshot: add the first regular user to the virt + docker groups ─
# Polkit covers libvirt access for wheel users, but raw /dev/kvm, the docker
# socket, and tools that check `groups` membership need real group entries. The
# unit and its helper ship in system_files/; the unit retries every boot until a
# regular user exists, then drops a marker so it stops running.
systemctl enable bazzite-tower-firstboot.service

# ── Wi-Fi backend guard: don't let a stale wifi.backend=iwd strand Wi-Fi ───────
# /etc persists across a bootc rebase, so a `wifi.backend=iwd` file from a
# previous image can outlive its enabled iwd service — NetworkManager then points
# at a supplicant that never runs and every Wi-Fi device reports `unavailable`.
# This unit runs before NetworkManager each boot and forces the default
# wpa_supplicant backend whenever iwd is selected but not enabled (and backs off
# again the moment iwd is properly enabled). Ships in system_files/.
systemctl enable bazzite-tower-wifi-backend-guard.service

# ── Storage health monitoring (SMART) ─────────────────────────────────────────
# smartd runs scheduled self-tests and watches SMART health for both NVMe drives,
# logging warnings to the journal (no MTA on this image). Config ships in
# system_files/etc/smartmontools/smartd.conf.
dnf install -y smartmontools
systemctl enable smartd.service

# ── Cockpit: web-based system + VM management ─────────────────────────────────
# Homelab management surface on :9090 — VMs (cockpit-machines drives the same
# libvirt stack baked above), services, storage, logs, and podman. The base
# already ships most of Cockpit (bridge/system/networkmanager/storaged/podman/
# files/selinux); only cockpit-machines is missing, and the socket isn't enabled.
# Add the VM module and enable socket activation (cockpit.socket listens, starts
# cockpit on first connect). Reach it over Tailscale rather than exposing the LAN.
dnf install -y cockpit cockpit-machines
systemctl enable cockpit.socket

# ── RAS / MCE handling ────────────────────────────────────────────────────────
# This box logs ~115 corrected CPU cache-error MCEs per boot on Meteor Lake. EDAC
# igen6 ECC counters read 0/0, so these are CPU cache, not DRAM. Two changes:
#   1. rasdaemon — the modern RAS collector. It records and decodes MCEs into a
#      local store queryable with `ras-mc-ctl --summary`/`--errors`.
#   2. mask mcelog — redundant with rasdaemon, and on this box its
#      cache-error-trigger tried to *offline a CPU* when it hit the corrected-error
#      threshold (it failed only because the trigger script was buggy). Masking
#      removes that footgun for good.
dnf install -y rasdaemon
systemctl enable rasdaemon.service
systemctl mask mcelog.service

# Latest Intel microcode. Meteor Lake has shipped corrected-cache-error microcode
# updates; this box was seen on revision 0x28. `dnf install` upgrades microcode_ctl
# to the newest in the Fedora repos when the base is older (no-op if already
# current). Note early-load takes effect once the initramfs is regenerated (on a
# base bump); verify the running revision per docs/RUNBOOK.md.
dnf install -y microcode_ctl

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

# ── CPU power/thermal: balanced baseline ──────────────────────────────────────
# The box boots into the firmware's low-power state (cpufreq EPP=power, ACPI
# platform_profile=low-power) with no power daemon, throttling a plugged-in
# homelab (96 throttle events/boot observed). Install thermald for proper Meteor
# Lake thermal management, and enable a oneshot that sets a balanced EPP + platform
# profile at boot (helper + unit ship in system_files/).
dnf install -y thermald
systemctl enable thermald.service
systemctl enable bazzite-tower-power-tuning.service

# NOTE on SOF audio: the analog/HDMI codec is driven the legacy-HDA way on this
# image via the snd_intel_dspcfg.dsp_driver=1 kernel arg (kargs.d/25-audio-sof-bypass.toml),
# NOT by downgrading firmware. The kernel's SOF driver is at topology ABI 3.23 while
# stock alsa-sof-firmware ships ABI 3.29, and Fedora's repos no longer carry an
# ABI-≤3.23 build to downgrade to — so the firmware can't be pinned. Forcing the
# legacy HDA path sidesteps SOF entirely. See docs/RUNBOOK.md "Audio".

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
        source "$(dirname "${BASH_SOURCE[0]}")/firewall/portmaster.sh"
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

# ── Cleanup ───────────────────────────────────────────────────────────────────
dnf clean all
