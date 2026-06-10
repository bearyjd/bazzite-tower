<div align="center">

<h1>
  <img src="docs/assets/banner.svg" alt="bazzite-tower" width="880">
</h1>

[![Build](https://img.shields.io/github/actions/workflow/status/bearyjd/bazzite-tower/build.yml?branch=main&style=for-the-badge&logo=githubactions&logoColor=white&label=build)](https://github.com/bearyjd/bazzite-tower/actions/workflows/build.yml)
[![Boot test](https://img.shields.io/github/actions/workflow/status/bearyjd/bazzite-tower/boot-test.yml?branch=main&style=for-the-badge&logo=linuxcontainers&logoColor=white&label=boot%20test)](https://github.com/bearyjd/bazzite-tower/actions/workflows/boot-test.yml)
[![License](https://img.shields.io/github/license/bearyjd/bazzite-tower?style=for-the-badge&color=4c8bf5)](LICENSE)
[![Image](https://img.shields.io/badge/ghcr.io-bazzite--tower-2496ED?style=for-the-badge&logo=podman&logoColor=white)](https://github.com/users/bearyjd/packages/container/package/bazzite-tower)

![bootc](https://img.shields.io/badge/bootc-immutable-0a0a0a?style=for-the-badge&logo=linux&logoColor=white)
![Fedora](https://img.shields.io/badge/Fedora-51A2DA?style=for-the-badge&logo=fedora&logoColor=white)
![KDE Plasma](https://img.shields.io/badge/KDE_Plasma-1D99F3?style=for-the-badge&logo=kde&logoColor=white)
![NVIDIA](https://img.shields.io/badge/NVIDIA-76B900?style=for-the-badge&logo=nvidia&logoColor=white)
![QEMU/KVM](https://img.shields.io/badge/QEMU%2FKVM-EE0000?style=for-the-badge&logo=qemu&logoColor=white)
![cosign](https://img.shields.io/badge/signed-cosign-FBC02D?style=for-the-badge&logo=sigstore&logoColor=black)

</div>

A custom [bootc](https://github.com/bootc-dev/bootc) image derived from `ghcr.io/ublue-os/bazzite-nvidia:stable`, tailored for an NVIDIA RTX-equipped desktop/laptop workstation that doubles as a virtualization host and developer machine. Built weekly, signed with cosign, published to `ghcr.io/bearyjd/bazzite-tower`.

## Why this exists

Stock Bazzite KDE is excellent for gaming, but every install needs the same post-boot setup: enable libvirt sockets, run `ujust setup-virtualization` (which is broken on the modular libvirt that ships in F44+), add yourself to libvirt and kvm groups, install Docker on top of Podman, drag in dev tooling. `bazzite-tower` bakes all of that into the image so the first boot is the only boot you need.

This is a **desktop/laptop variant** — not for handhelds or Steam Deck. It uses the **proprietary NVIDIA driver** rather than the open kernel modules: on hybrid (Optimus) laptops the proprietary driver is currently the more reliable choice for suspend/resume and power management, which the open modules still struggle with (see [Design choices](#nvidia-proprietary-driver-over-open-modules)). If you prefer the open modules (NVIDIA's default for Turing+), rebase the `FROM` to `bazzite-nvidia-open:stable`.

## What's included beyond stock Bazzite

### Virtualization stack (qemu:///system works on first boot)

- `qemu-kvm`, `libvirt`, `libvirt-daemon-kvm`, `libvirt-client`
- `libvirt-daemon-config-network`, `libvirt-daemon-config-nwfilter` (default NAT network + nwfilter rules)
- `virt-manager`, `virt-install`, `virt-viewer`
- `edk2-ovmf` (UEFI firmware for VMs)
- `guestfs-tools`, `spice-gtk3`

### Developer tooling

- `android-tools` — adb/fastboot for device flashing
- `flatpak-builder` — build local Flatpaks
- `restic`, `rclone` — backup and cloud sync
- `zsh` — alternative shell
- `ccache` — compile caching for native builds
- `podman-machine`, `podman-tui` — Podman VM and TUI

### Docker CE alongside Podman

Docker CE is installed from the upstream `download.docker.com` repo (not Fedora's `moby-engine`):

- `docker-ce`, `docker-ce-cli`, `containerd.io`
- `docker-buildx-plugin`, `docker-compose-plugin`

The Docker repo file ships with **every section disabled**. Packages are pulled in via `--enablerepo=docker-ce-stable` only during the image-build transaction, so the repo never participates in runtime updates.

`iptable_nat` is registered in `/etc/modules-load.d/iptable_nat.conf` for docker-in-docker workloads.

### VM management recipes (`ujust`)

`bazzite-tower` ships extra `ujust` recipes (in `/usr/share/ublue-os/just/60-custom.just`) for driving the modular libvirt stack:

| Recipe | What it does |
|---|---|
| `ujust vm-start` | Start the modular libvirt sockets (`virtqemud`, `virtnetworkd`, `virtstoraged`, `virtnodedevd`) |
| `ujust vm-stop` | Stop those sockets |
| `ujust vm-list` | `virsh -c qemu:///system list --all` |
| `ujust vm-net-status` | `virsh -c qemu:///system net-list --all` |
| `ujust fix-vm-groups` | Add the current user to `kvm`, `libvirt`, `docker` (then log out/in) |
| `ujust wifi-debug` | Dump Wi-Fi diagnostics (rfkill, `lspci`, `iwlwifi`/`DMAR` dmesg, modules, NetworkManager, firmware, kernel cmdline) — read-only, works offline |

The stack is socket-activated and enabled at boot, so `vm-start` is rarely needed — it's there for when you've manually stopped the daemons.

### Wi-Fi not detected?

If Wi-Fi looks dead after a boot — no networks, NetworkManager shows no usable Wi-Fi device — run `ujust wifi-debug` (it works without a network) and read it top-down. **Check the easy, common causes before assuming a driver/firmware problem:**

- **`nmcli` shows the device as `unavailable` (not `disconnected`), but the driver-level scan in `wifi-debug` finds networks** → the radio is fine; NetworkManager's **wifi backend is pointing at a supplicant that isn't running.** The classic case: `/etc/NetworkManager/conf.d/iwd.conf` sets `wifi.backend=iwd` while `iwd` is inactive (`wpa_supplicant` is what's actually running). This frequently happens after a `bootc` **rebase**, because `/etc` persists: a `wifi.backend=iwd` file from a previous image survives, but the enabled `iwd` service does not. `bazzite-tower` ships a guard for exactly this (see [Wi-Fi backend guard](#wi-fi-backend-guard)), so on a fresh boot it self-corrects; to fix a running session immediately, revert to the default backend with `sudo mv /etc/NetworkManager/conf.d/iwd.conf ~/iwd.conf.disabled && sudo systemctl restart NetworkManager` (or `sudo systemctl enable --now iwd` if you actually want `iwd`).
- **`rfkill` shows a hard block** → a physical/Fn switch or a BIOS setting, not the image.
- **`lspci` doesn't list the wireless card at all** → disabled in BIOS or a hardware/seating issue.
- **`lspci` lists the card but `dmesg` shows `iwlwifi ... DMAR` faults or `Failed to start ... ucode`** → the IOMMU is knocking out `iwlwifi`. An Intel CNVi card can fail to initialize under `intel_iommu=on`, and the wireless device then never registers. Confirm by rebooting, pressing `e` in GRUB, removing `intel_iommu=on iommu=pt` from the `linux` line, and booting once. If Wi-Fi returns, the IOMMU karg (`/usr/lib/bootc/kargs.d/00-iommu.toml`, added for PCI passthrough) is the cause — drop that fragment if you don't need VFIO passthrough, and rebuild.
- **Intel BE200 (Wi-Fi 7) specifically:** newer kernels (6.15+) drive it with the new `iwlmld` op-mode and require firmware ≥ v100 — there is no usable `iwlmvm` fallback, so don't bother downgrading firmware. If `wifi-debug`'s driver-level scan works, the BE200 itself is fine and the problem is upstream of the driver (almost always the backend issue above).

## Design choices

### NVIDIA proprietary driver over open modules

NVIDIA's open kernel modules are the default for Turing+ since driver R560 and are at performance parity, so they're the obvious pick on paper. But this image targets a **hybrid (Optimus) laptop** where the priority is reliable *host* dGPU use — PRIME render offload plus dependable suspend/resume — not GPU passthrough. That's exactly where the open modules still lag: NVIDIA's own driver docs list power management as a known-incomplete area, and upstream `open-gpu-kernel-modules` bug reports of suspend/hibernate failures on Intel+NVIDIA hybrid laptops remained open into 2026. Bazzite users on hybrid laptops have reported better stability (and lower idle power) on the proprietary driver.

So `bazzite-tower` builds on `bazzite-nvidia:stable` (proprietary). On an RTX 40-series (Ada) card the proprietary driver is fully supported; the open modules remain one `FROM`-line swap away (`bazzite-nvidia-open:stable`) if you'd rather track NVIDIA's open default — and `bootc rollback` makes trying either low-risk.

### Wi-Fi backend guard

NetworkManager picks a Wi-Fi backend (`wpa_supplicant` by default, or `iwd`). Because `/etc` persists across a `bootc` rebase, a `wifi.backend=iwd` config from a previous image can outlive its enabled `iwd` service — NetworkManager then points at a supplicant that never runs, and **every Wi-Fi device reports `unavailable`** (which looks exactly like a missing card, even though the radio is fine).

`bazzite-tower-wifi-backend-guard.service` runs before `NetworkManager` on each boot. If any config selects `wifi.backend=iwd` while `iwd` is not enabled, it drops a late-sorting override (`/etc/NetworkManager/conf.d/zzz-bazzite-tower-wifi-backend-guard.conf`) restoring the default `wpa_supplicant` backend — and removes that override automatically the moment `iwd` is properly enabled, so a deliberate `sudo systemctl enable --now iwd` is always respected. The backend is corrected before NM starts, so no restart is needed.

### Modular libvirt (no manual `ujust setup-virtualization`)

Fedora 44+ defaults to modular libvirt: per-driver daemons (`virtqemud`, `virtnetworkd`, `virtnodedevd`, `virtnwfilterd`, `virtstoraged`) replace the monolithic `libvirtd`. `bazzite-tower` enables those modular sockets at build time (enabling each primary `.socket` also pulls in its `-ro`/`-admin` variants via the unit's `Also=` directive). The legacy `libvirtd.service` is masked so it can't race the modular daemons — that race is the root cause of broken `ujust setup-virtualization` on stock images.

A container `dnf install` doesn't run `systemd-sysusers` the way an rpm-ostree compose does, so the `qemu` system user that the libvirt packages declare via `sysusers.d` is never created — and `virtqemud` then aborts at startup (`Failed to parse user 'qemu'`) and crash-loops, so `qemu:///system` would silently never come up. `build.sh` materializes that user at build time: it strips the orphan `qemu:` shadow/gshadow lines the base image ships (which otherwise make `systemd-sysusers` roll the whole transaction back and create nothing), runs `systemd-sysusers`, then falls back to a guarded `groupadd`/`useradd`. The [runtime boot test](#continuous-testing--upstream-tracking) connects to `qemu:///system` on every change to keep this honest.

For tooling that still expects the monolithic `/run/libvirt/libvirt-sock`, `virtproxyd.socket` is enabled. `virtproxyd` is the modular drop-in for that legacy path: it forwards to the per-driver daemons. It and `libvirtd.socket` both declare `Conflicts=` on the same socket path, so only `virtproxyd.socket` is enabled (`libvirtd.socket` would be inert anyway with its service masked).

The default NAT network (shipped by `libvirt-daemon-config-network`) is marked autostart at build time by creating the `autostart/default.xml` symlink that `virsh net-autostart` would — so guests get networking on first boot without manual setup.

### IOMMU enabled for PCI passthrough

`intel_iommu=on iommu=pt` are baked in as kernel arguments via a bootc `kargs.d` fragment (`/usr/lib/bootc/kargs.d/00-iommu.toml`), enabling VFIO/PCI passthrough to guests. This uses bootc's native karg mechanism rather than `rpm-ostree kargs`, which can't run during an image build. Target hardware is Intel (ThinkPad P1); `iommu=pt` keeps DMA-remapping overhead off host-only devices.

### Intel display & suspend stability

The target panel (Intel iGPU on Meteor Lake) throws eDP link/PLL errors with flicker and post-resume corruption when the i915 driver's panel power-saving is left on. Separately, a kernel 7.0 regression corrupts the i915 PHY A / C10 (cx0) PLL state on s2idle resume (~30s of flip-done timeouts and a sluggish display after wake). Two more bootc `kargs.d` fragments address these:

- `10-i915-display.toml` — `i915.enable_dc=0 i915.enable_psr=0 i915.enable_psr2_sel_fetch=0` disable Display C-states and Panel Self Refresh (the three are one intervention). Cost is marginally higher panel power; the trade is a stable display. (These mitigate the panel power-saving faults; they do **not** fix the PHY A resume regression on their own.)
- `20-suspend.toml` — `mem_sleep_default=s2idle` pins s2idle suspend. Meteor Lake has no working S3 ("deep") suspend; an earlier attempt to default to deep made resume worse (bounce behaviour), so we pin s2idle explicitly rather than relying on the firmware fallback. Check the live mode with `cat /sys/power/mem_sleep` (the bracketed entry is active). The PHY A resume regression itself needs an upstream kernel fix — `scripts/check-i915-resume-fix.sh` (weekly user timer) watches for it.

Each is its own fragment, so you can drop either independently if your hardware is happy without it. Like the IOMMU karg, these use bootc's native mechanism rather than `rpm-ostree kargs` (which only sets per-machine local state and can't run during an image build).

### Two-layered libvirt/kvm access for the default user

Bootc images don't bake in a default user — the first user is created by KDE Plasma's initial-setup on first boot. `bazzite-tower` uses two complementary mechanisms to give that user immediate virtualization access:

1. **Polkit rule** (`/etc/polkit-1/rules.d/50-libvirt-wheel.rules`) — grants `unix-group:wheel` access to `org.libvirt.unix.manage` and `org.libvirt.unix.monitor`. Anyone in `wheel` can talk to `qemu:///system` from `virt-manager` and `virsh` immediately, no logout required.
2. **First-boot oneshot** (`bazzite-tower-firstboot.service`) — runs after `systemd-user-sessions.service`, finds the first UID≥1000 user, and runs `usermod -aG kvm,libvirt,docker` (adding only groups that exist). This grants real group membership for tools that check `groups`, for raw `/dev/kvm` access, and for the rootless `docker` socket (polkit only covers libvirt). The unit retries every boot until a regular user exists, then writes a marker file (`/var/lib/.bazzite-tower-groups-done`) so it stops running.

Result: `virsh -c qemu:///system list` and `virt-manager` work on first login (via the polkit rule). Raw `/dev/kvm` (`qemu-system-x86_64 -enable-kvm`) and rootless `docker` depend on group membership, so they work once the first-boot service has applied the groups — in practice after the next reboot following initial account creation, plus a fresh login session to pick the new groups up.

### Docker CE instead of podman-docker

`podman-docker` (the package that aliases `docker` to `podman`) is removed at build time. Docker CE is installed alongside Podman. Both daemons can coexist — different binaries, different sockets, different state — pick whichever your workflow expects without alias trickery.

`docker.service` is enabled at boot, and the first regular user is added to the `docker` group (see below), so `docker` works without `sudo` after the first login cycle.

### Disabled-by-default external repos

External repos (currently just Docker CE) are dropped on disk with `enabled=0`. Packages are pulled via `--enablerepo=` flags during the build transaction only. Result: zero background traffic to external repos, no surprise upgrades, no third-party participation in runtime `bootc upgrade`.

### Packages explicitly excluded

To keep the image lean and focused, these are **not** installed even though some sibling images include them: `python3-ramalama`, `bcc`, `bpftrace`, `bpftop`, `tiptop`, `sysprof`, `nicstat`, `numactl`, `usbmuxd`, VS Code. Install any of them via `rpm-ostree install` or `flatpak` as needed.

## Continuous testing & upstream tracking

This image rides `bazzite-nvidia:stable` and the laptop rebases onto `:latest`, so **"the build is green" has to also mean "the image works."** A green build can still publish a silently-broken image — e.g. an upstream change makes the qemu-user logic create nothing, `virtqemud` crash-loops on boot, and `qemu:///system` never comes up, yet nothing ever errors at build time. Three layers guard that gap; the full failure model and the reasoning behind each layer live in [`docs/downstream-change-tracking.md`](./docs/downstream-change-tracking.md).

| Layer | Where | What it does |
|---|---|---|
| **Smoke gate** | `build.yml` → [`tests/smoke.sh`](./tests/smoke.sh) | Offline assertions against the freshly built image, run **before** push: qemu user resolves, the six `virt*.socket`s are enabled, `libvirtd` is masked, the Wi-Fi guard / first-boot / Docker units are enabled, the IOMMU / i915 / suspend kargs are present. A failure blocks the push, so `:latest` stays on the last-good image. |
| **Runtime boot test** | `boot-test.yml` → [`tests/boot-check.sh`](./tests/boot-check.sh) | Boots the image's own systemd under `podman --systemd=always` and proves the stack *works*: socket-activates `virtqemud` and connects to `qemu:///system` (the end-to-end check for the qemu-user regression), and confirms the Wi-Fi backend guard ran clean. |
| **Upstream early warning** | `base-watch.yml` → [`ci/base-diff.py`](./ci/base-diff.py) | Daily, diffs the base image's package manifest (committed to `docs/manifests/` after the first run) against the last-seen one, filtered to the blast-radius packages (qemu/libvirt/NetworkManager/Docker/kernel/systemd/polkit/bootc). A change opens a heads-up issue **before** the next build. |

Each failing layer opens — and later auto-closes — a labelled tracking issue (`ci-failure`, `boot-test-failure`, `base-bump`). Reproduce the smoke gate locally with `just smoke`.

## Installing

From any bootc-based system (Bazzite, Bluefin, Aurora, Silverblue, Fedora Atomic):

```bash
sudo bootc switch ghcr.io/bearyjd/bazzite-tower:latest
sudo systemctl reboot
```

The image is signed with cosign — the public key lives at `cosign.pub` in this repo. Bazzite's bootc policy enforces signature verification by default.

## Tags

- `latest` — current build of `main`
- `latest.YYYYMMDD` — same image, date-stamped
- `YYYYMMDD` — date-only tag
- `<short-sha>` — the 7-character git SHA of the build commit

CI rebuilds weekly (Sunday 06:00 UTC) and on every push to `main`.

## Hardware target

- Desktop or laptop (not handheld / Deck)
- NVIDIA GPU on the **proprietary** driver (developed against an RTX 4070 Max-Q / Ada on a hybrid Optimus laptop)
- KVM-capable CPU (Intel VT-x or AMD-V)
- Sufficient RAM for KDE Plasma + concurrent VMs

The proprietary driver supports Maxwell and newer, so there's no pre-Turing cutoff here. If you'd rather run NVIDIA's open kernel modules (default for Turing+), swap the Containerfile `FROM` to `bazzite-nvidia-open:stable` and rebuild.

## Repository layout

| Path | Purpose |
|---|---|
| `Containerfile` | Image build definition (`FROM` + `COPY system_files` + invoke `build.sh`) |
| `build_files/build.sh` | All customizations: packages, repos, units, polkit, first-boot oneshot |
| `system_files/` | Static content copied verbatim into the image (systemd units, ujust recipes, bootc kargs) |
| `disk_config/disk.toml` | qcow2/raw config for bootc-image-builder |
| `disk_config/iso-kde.toml` | bootc-image-builder anaconda-iso config (unused — see ISO note) |
| `disk_config/iso-gnome.toml` | bootc-image-builder anaconda-iso config (unused — see ISO note) |
| `installer/` | Live-ISO payload (live KDE session + Anaconda) built FROM bazzite-tower, fed to titanoboa by `build-iso.yml` / `just build-iso-live` |
| `.github/workflows/build.yml` | CI: build, **smoke-test gate**, push to GHCR, sign with cosign |
| `.github/workflows/build-disk.yml` | CI: produce a qcow2 disk image on demand (anaconda-iso disabled — upstream blockers) |
| `.github/workflows/build-iso.yml` | CI: build a bootable, Secure-Boot live/installer ISO via titanoboa |
| `.github/workflows/boot-test.yml` | CI: boot the image under systemd and check runtime behaviour |
| `.github/workflows/base-watch.yml` | CI: daily upstream base package-diff early warning |
| `tests/smoke.sh` | Offline assertions run against the built image (the CI gate; also `just smoke`) |
| `tests/boot-check.sh` | Runtime checks run inside the booted image by `boot-test.yml` |
| `ci/base-diff.py` | Filters the upstream package diff to the blast-radius packages |
| `docs/downstream-change-tracking.md` | How the image stays current with upstream Bazzite without silently breaking |
| `cosign.pub` | Public key for verifying signed images |
| `Justfile` | Local build/run recipes (see below) |

## Local build & VM testing

Quick path for testing changes before rebasing your real machine:

```bash
just build               # build the container image locally
just smoke               # offline smoke-test the built image (same assertions as the CI gate)
just build-qcow2         # turn it into a bootable qcow2
just run-vm-qcow2        # boot the qcow2 in qemu, browser console at localhost:8006
```

`just spawn-vm` boots via `systemd-vmspawn` instead, if you'd rather skip the browser console. Run `just` with no arguments for the full recipe list. Detailed Justfile documentation is below.

---

# Repository Contents

## Containerfile

The [Containerfile](./Containerfile) defines the operations used to customize the selected image. This file is the entrypoint for the image build and works exactly like a regular podman Containerfile. For reference, see the [Podman Documentation](https://docs.podman.io/en/latest/Introduction.html).

## build.sh

The [build.sh](./build_files/build.sh) file is called from the Containerfile. It is where every customization in this image lives: package installs, repo files, systemd unit drops, polkit rules, and the first-boot oneshot. Edit this file to change what's in the image.

## build.yml

The [build.yml](./.github/workflows/build.yml) GitHub Actions workflow creates the custom OCI image and publishes it to the GitHub Container Registry (GHCR). The image name matches the GitHub repository name. Several environment variables at the start of the workflow may be of interest to change.

# Building Disk Images

This template provides an out-of-the-box workflow for creating disk images (ISO, qcow, raw) for the custom OCI image, which can be used to directly install onto machines.

This template provides a way to upload the disk images generated from the workflow to an S3 bucket. The disk images will also be available as artifacts from the job if you wish to use an alternate provider. To upload to S3 we use [rclone](https://rclone.org/), which supports [many S3 providers](https://rclone.org/s3/).

## Setting Up Disk Image Builds

The [build-disk.yml](./.github/workflows/build-disk.yml) GitHub Actions workflow creates a disk image from your OCI image using the [bootc-image-builder](https://osbuild.org/docs/bootc/). To use this workflow:

1. **Two artifacts, two tools.** `build-disk.yml` builds a **qcow2** (rootfs=btrfs) for VM testing. **Bootable ISOs** are built separately by [`build-iso.yml`](./.github/workflows/build-iso.yml) using [titanoboa](https://github.com/ublue-os/titanoboa) (ublue's live-ISO toolchain), **not** `bootc-image-builder`'s `anaconda-iso` — that path is upstream-broken ([BIB#1188](https://github.com/osbuild/bootc-image-builder/issues/1188), [bazzite#3418](https://github.com/ublue-os/bazzite/issues/3418)). The ISO is built from the [`installer/`](./installer) payload image (a live KDE session + Anaconda that installs bazzite-tower via `ostreecontainer`); it boots under **Secure Boot** (the payload swaps in a Fedora-signed kernel) and can be built locally with `just build-iso-live`. The `iso-kde.toml`/`iso-gnome.toml` files are leftover BIB configs and are unused. For an existing bootc system, `bootc switch` (see [Installing](#installing)) is still the simplest path.
2. If you changed your image name from the default in `build.yml`, then in `build-disk.yml` edit the `IMAGE_REGISTRY`, `IMAGE_NAME`, and `DEFAULT_TAG` environment variables to match. If you didn't, skip this step.
3. If you want to upload your disk images to S3, add the S3 configuration to the repository's Action secrets (Settings → Secrets and Variables → Actions):
   - `S3_PROVIDER` — must match one of the values from the [supported list](https://rclone.org/s3/)
   - `S3_BUCKET_NAME` — your unique bucket name
   - `S3_ACCESS_KEY_ID` — recommended to make a separate key for this workflow
   - `S3_SECRET_ACCESS_KEY` — see above
   - `S3_REGION` — the region your bucket lives in (set to `auto` if you don't know)
   - `S3_ENDPOINT` — provider-specific endpoint URL

Once the workflow is done, disk images land either in your S3 bucket or as part of the run summary under `Artifacts`.

# Justfile Documentation

The `Justfile` contains commands and configurations for building and managing container images and virtual machine images using Podman and other utilities.
To use it you must have [just](https://just.systems/man/en/introduction.html) installed from your package manager or manually. It's available by default on all Universal Blue images.

## Environment Variables

- `image_name` — the name of the image (default: `bazzite-tower`)
- `default_tag` — the default tag for the image (default: `latest`)
- `bib_image` — the Bootc Image Builder image (default: `quay.io/centos-bootc/bootc-image-builder:latest`)

## Building The Image

### `just build`

Builds a container image using Podman.

```bash
just build $target_image $tag
```

Arguments:
- `$target_image` — the tag to apply to the image (default: `$image_name`)
- `$tag` — the tag for the image (default: `$default_tag`)

### `just smoke`

Runs the offline smoke test (`tests/smoke.sh`) against a built image — the same assertions as the CI promotion gate, with no VM required.

```bash
just smoke $target_image $tag
```

It executes `podman run --rm -i "$target_image:$tag" bash -s < tests/smoke.sh`, so build the image first (`just build`). Exits non-zero if any customization (qemu user, modular `virt*.socket`s, the Wi-Fi guard, the IOMMU / i915 / suspend kargs, Docker CE) is missing.

## Building and Running Virtual Machines and ISOs

The commands below build QCOW2 images by default. To produce or use a different type of image, substitute `qcow2` with that type. Available types: `qcow2`, `iso`, `raw`.

### `just build-qcow2`

Builds a QCOW2 virtual machine image.

```bash
just build-qcow2 $target_image $tag
```

### `just rebuild-qcow2`

Rebuilds a QCOW2 virtual machine image.

```bash
just rebuild-vm $target_image $tag
```

### `just run-vm-qcow2`

Runs a virtual machine from a QCOW2 image.

```bash
just run-vm-qcow2 $target_image $tag
```

### `just spawn-vm`

Runs a virtual machine using `systemd-vmspawn`.

```bash
just spawn-vm rebuild="0" type="qcow2" ram="6G"
```

## File Management

### `just check`

Checks the syntax of all `.just` files and the `Justfile`.

### `just fix`

Fixes the syntax of all `.just` files and the `Justfile`.

### `just clean`

Cleans the repository by removing build artifacts.

### `just lint`

Runs shellcheck on all Bash scripts.

### `just format`

Runs shfmt on all Bash scripts.

## Additional resources

For additional driver support, ublue maintains a set of scripts and container images at [ublue-akmods](https://github.com/ublue-os/akmods). These images include scripts to install multiple kernel drivers within the container (Nvidia, OpenRazer, Framework, etc.) — useful if you need to extend `bazzite-tower` with additional hardware support.

---

Originally derived from the [ublue-os/image-template](https://github.com/ublue-os/image-template). Community resources: [Universal Blue Forums](https://universal-blue.discourse.group/), [Universal Blue Discord](https://discord.gg/WEu6BdFEtp), [bootc discussion forums](https://github.com/bootc-dev/bootc/discussions).
