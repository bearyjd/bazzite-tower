<!-- Generated: 2026-06-14 | Files scanned: 3 | Token estimate: ~780 -->
# Image Build Pipeline

`Containerfile` → `build_files/build.sh` (runs inside the build, `set -euo pipefail`).

## Containerfile stages

1. `FROM scratch AS ctx` + `COPY build_files /` — scripts reachable via bind mount, not baked in
2. `FROM ghcr.io/ublue-os/bazzite-nvidia:stable` + OCI labels
3. `COPY system_files/ /` — static content, copied **before** build.sh so it can enable those units
4. `RUN --mount=bind,from=ctx … /ctx/build.sh` — modifications (caches: /var/cache, /var/log; tmpfs /tmp)
5. `RUN bootc container lint`

## build.sh sections (in order, 265 lines)

| Lines | Section | Effect |
|---|---|---|
| 5–19   | QEMU/libvirt stack | dnf: qemu-kvm, libvirt*, virt-install/manager/viewer, edk2-ovmf, guestfs-tools, spice-gtk3 |
| 21–30  | Dev tooling | dnf: android-tools, ccache, flatpak-builder, podman-machine/tui, rclone, restic, zsh |
| 32–110 | Docker CE | write inert `docker-ce.repo` (every section enabled=0); remove `podman-docker`; install via `--enablerepo=docker-ce-stable` |
| 112–141| sysusers fix | **generic** orphan strip (keep only shadow/gshadow lines with a matching passwd/group) → `systemd-sysusers` → guarded `groupadd -r qemu` + `useradd qemu` + **`groupadd -r docker`**. Fixes virtqemud + docker.socket "Unknown group" boot failures |
| 143–146| docker-in-docker | `/etc/modules-load.d/iptable_nat.conf` |
| 148–171| libvirt modular + docker | mask `libvirtd.service`; enable `virtqemud/virtnetworkd/virtnodedevd/virtnwfilterd/virtstoraged/virtproxyd.socket`; enable `docker.service` |
| 173–181| default NAT net autostart | symlink `autostart/default.xml` (virsh can't run at build time) |
| 183–192| polkit rule | `wheel` → `qemu:///system` (manage + monitor) |
| 194    | enable firstboot | `bazzite-tower-firstboot.service` |
| 201    | enable wifi guard | `bazzite-tower-wifi-backend-guard.service` |
| 210–215| **Storage SMART** | dnf: smartmontools; enable `smartd.service` (config in `system_files/`) |
| 217–225| **Cockpit** | dnf: cockpit, cockpit-machines; enable `cockpit.socket` (web mgmt :9090; rest of Cockpit is base-provided) |
| 227–245| **RAS / MCE** | dnf: rasdaemon (enable) ; **mask `mcelog.service`** ; dnf: microcode_ctl (latest) |
| 247–262| **CPU power/thermal** | dnf: thermald (enable) ; enable `bazzite-tower-power-tuning.service` (balanced EPP + platform-profile). SOF audio: **no install** — bypassed via the `dsp_driver=1` karg (see system-files) |
| 264    | `dnf clean all` | |

## Verified by

`tests/smoke.sh` (offline, post-build) asserts each of the above survived the build;
`tests/boot-check.sh` proves the QEMU path works + no SOF storm at runtime. See [ci-cd.md](ci-cd.md).
