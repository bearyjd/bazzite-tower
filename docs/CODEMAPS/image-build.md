<!-- Generated: 2026-05-31 | Files scanned: 3 | Token estimate: ~650 -->
# Image Build Pipeline

`Containerfile` → `build_files/build.sh` (runs inside the build, `set -euo pipefail`).

## Containerfile stages

1. `FROM scratch AS ctx` + `COPY build_files /` — scripts reachable via bind mount, not baked in
2. `FROM ghcr.io/ublue-os/bazzite-nvidia:stable` + OCI labels
3. `COPY system_files/ /` — static content, copied **before** build.sh so it can enable those units
4. `RUN --mount=bind,from=ctx … /ctx/build.sh` — modifications (caches: /var/cache, /var/log; tmpfs /tmp)
5. `RUN bootc container lint`

## build.sh sections (in order)

| Lines | Section | Effect |
|---|---|---|
| 7–19   | QEMU/libvirt stack | dnf: qemu-kvm, libvirt*, virt-install/manager/viewer, edk2-ovmf, guestfs-tools, spice-gtk3 |
| 22–30  | Dev tooling | dnf: android-tools, ccache, flatpak-builder, podman-machine/tui, rclone, restic, zsh |
| 38–110 | Docker CE | write inert `docker-ce.repo` (every section enabled=0); remove `podman-docker`; install via `--enablerepo=docker-ce-stable` |
| 112–131| qemu sysusers fix | strip orphan `qemu:` shadow/gshadow → `systemd-sysusers` → guarded `groupadd`/`useradd` fallback |
| 134–136| docker-in-docker | `/etc/modules-load.d/iptable_nat.conf` |
| 143    | mask `libvirtd.service` | stop it racing the modular daemons |
| 147–158| enable modular sockets | `virtqemud / virtnetworkd / virtnodedevd / virtnwfilterd / virtstoraged` + `virtproxyd` `.socket` |
| 161    | enable `docker.service` | |
| 168–171| default NAT net autostart | symlink `autostart/default.xml` (virsh can't run at build time) |
| 174–182| polkit rule | `wheel` → `qemu:///system` (manage + monitor) |
| 189    | enable `bazzite-tower-firstboot.service` | |
| 198    | enable `bazzite-tower-wifi-backend-guard.service` | |
| 201    | `dnf clean all` | |

## Verified by

`tests/smoke.sh` (offline, post-build) asserts each of the above survived the build;
`tests/boot-check.sh` proves the QEMU path works at runtime. See [ci-cd.md](ci-cd.md).
