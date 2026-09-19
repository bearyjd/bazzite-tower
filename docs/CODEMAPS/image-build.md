<!-- Generated: 2026-08-08 | Files scanned: 3 | Token estimate: ~850 -->
# Image Build Pipeline

`Containerfile` → `build_files/build.sh` (runs inside the build, `set -euo pipefail`).

## Containerfile stages

1. `ARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open@sha256:…` (the single pinned default source for `:latest`; CI leaves it unset for safe-pin and overrides it only for `:latest-kernel`)
2. `FROM scratch AS ctx` + `COPY build_files /` — scripts reachable via bind mount, not baked in
3. `FROM ${BASE_IMAGE}` + OCI labels
4. `COPY system_files/ /` — static content, copied **before** build.sh so it can enable those units
5. `RUN --mount=bind,from=ctx … /ctx/build.sh` — modifications (caches: /var/cache, /var/log; tmpfs /tmp)
6. `RUN bootc container lint`

## build.d scripts (executed in filename order)

`build.sh` is a thin runner: it `bash`-executes each `build_files/build.d/*.sh` in
glob order. Numbers encode execution order, which is load-bearing — `40-` must run
after `30-` and before `60-`. Filenames replace the line ranges this table used to
carry, because line ranges drift and filenames do not.

| Script | Effect |
|---|---|
| `05-pin-kde-packages.sh` | `/etc/dnf/dnf.conf` `exclude=` for the KDE Plasma/KWin package family, so this build's own dnf transactions can't skew `kwin` ahead of `kscreenlocker` (see `docs/research/kwin-screenlocker-abi-2026-08-08/`). Must run before any other script's `dnf install` |
| `10-virt-packages.sh` | dnf: qemu-kvm, libvirt*, virt-install/manager/viewer, edk2-ovmf, guestfs-tools, spice-gtk3 |
| `15-signature-policy.sh` | migrates the global policy default to `reject`, adds a Docker-only empty-scope compatibility fallback if absent, merges the repository-scoped cosign `sigstoreSigned` rule, and installs sigstore attachments without erasing unrelated explicit policy rules |
| `20-dev-tooling.sh` | dnf: android-tools, ccache, flatpak-builder, podman-machine/tui, rclone, restic, zsh |
| `30-docker-ce.sh` | write inert `docker-ce.repo` (every section enabled=0); remove `podman-docker`; install via `--enablerepo=docker-ce-stable`, including explicit `iptables` for the Docker/libvirt forwarding helper |
| `40-sysusers-fixup.sh` | **generic** orphan strip (keep only shadow/gshadow lines with a matching passwd/group) -> `systemd-sysusers` -> guarded `groupadd -r qemu` + `useradd qemu` + **`groupadd -r docker`**. Fixes virtqemud + docker.socket "Unknown group" boot failures. The most fragile piece; kept isolated on purpose |
| `50-docker-networking.sh` | intentional no-op: `ujust enable-docker` creates the Docker-only `iptable_nat` modules-load file locally |
| `60-libvirt-services.sh` | mask `libvirtd.service`; enable `virtqemud/virtnetworkd/virtnodedevd/virtnwfilterd/virtstoraged/virtproxyd.socket`; explicitly disable Docker service/socket; default NAT net autostart symlink (virsh can't run at build time); polkit `wheel` -> `qemu:///system`; enable `bazzite-tower-firstboot.service` (kvm/libvirt only) |
| `70-guards-monitoring.sh` | enable `bazzite-tower-wifi-backend-guard.service`; dnf smartmontools + enable `smartd.service`; dnf cockpit/cockpit-machines but explicitly disable Cockpit socket (loopback-only if an operator later enables it) |
| `80-ras-microcode.sh` | dnf rasdaemon (enable); **mask `mcelog.service`**; dnf microcode_ctl (latest) |
| `85-i915-watcher.sh` | enable `i915-resume-fix-check.timer` — kernel-version-gated check for the cx0 DPLL s2idle-resume regression signature; the machine-checkable signal the Containerfile kernel-pin comment points at |
| `90-power-thermal.sh` | dnf thermald (enable); enable `bazzite-tower-power-tuning.service` (balanced EPP + platform-profile). SOF audio: **no install** — bypassed via the `dsp_driver=1` karg |
| `95-firewall.sh` | Firewall selector. Default `opensnitch`: pinned v1.8.0 RPM extraction, Snitchwatch config + enablement. `FIREWALL_DAEMON=portmaster` is a disabled VM spike, sourcing `../firewall/portmaster.sh`: source-build of the exact Portmaster v2.2.1 commit, direct core (no updater/bootstrapper), config pinned from `/usr` via `BindReadOnlyPaths=`, Go toolchain removed after build, OpenSnitch masked. Build with `just build-portmaster-spike`; never a default image |
| `97-vm-gate-ssh.sh` | `VM_GATE_SSH=1` (off by default, `:latest` unaffected): enables `sshd.socket` so VM-gate testing (`just run-vm-ssh`) can SSH in instead of needing a GUI console. `build-portmaster-spike` already passes this build-arg |
| `98-rebrand-motd.sh` | overwrites the identity fields (`image-name`/`image-vendor`/`image-ref`/`image-tag`/`image-branch`) in `/usr/share/ublue-os/image-info.json` from the upstream base's own identity to `bazzite-tower`'s — the login MOTD template reads this file directly, so left unmodified it always claims the machine is running the upstream base image |
| `99-cleanup.sh` | `dnf clean all` |

`FIREWALL_DAEMON` and `VM_GATE_SSH` reach `95-firewall.sh`/`97-vm-gate-ssh.sh`
as inherited environment variables — the Containerfile sets them as a
command-prefix on the `RUN`, so the runner's shell has them and every child
`bash` inherits them.

## Verified by

`tests/smoke.sh` (offline, post-build) asserts each of the above survived the build;
`tests/boot-check.sh` proves the QEMU path works + no SOF storm at runtime. See [ci-cd.md](ci-cd.md).
