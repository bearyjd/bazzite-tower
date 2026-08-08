#!/usr/bin/env bash
set -euo pipefail

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

