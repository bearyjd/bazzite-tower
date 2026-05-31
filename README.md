# bazzite-tower

A custom [bootc](https://github.com/bootc-dev/bootc) image derived from `ghcr.io/ublue-os/bazzite-nvidia-open:stable`, tailored for an NVIDIA RTX-equipped desktop workstation that doubles as a virtualization host and developer machine. Built weekly, signed with cosign, published to `ghcr.io/bearyjd/bazzite-tower`.

## Why this exists

Stock Bazzite KDE is excellent for gaming, but every install needs the same post-boot setup: enable libvirt sockets, run `ujust setup-virtualization` (which is broken on the modular libvirt that ships in F44+), add yourself to libvirt and kvm groups, install Docker on top of Podman, drag in dev tooling. `bazzite-tower` bakes all of that into the image so the first boot is the only boot you need.

This is a **desktop (tower-form-factor) variant** — not for handhelds or Steam Deck. NVIDIA open kernel modules target RTX 30/40 series; older cards (pre-Turing) need the proprietary driver variant of Bazzite instead.

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

The stack is socket-activated and enabled at boot, so `vm-start` is rarely needed — it's there for when you've manually stopped the daemons.

## Design choices

### Modular libvirt (no manual `ujust setup-virtualization`)

Fedora 44+ defaults to modular libvirt: per-driver daemons (`virtqemud`, `virtnetworkd`, `virtnodedevd`, `virtnwfilterd`, `virtstoraged`) replace the monolithic `libvirtd`. `bazzite-tower` enables those modular sockets at build time (enabling each primary `.socket` also pulls in its `-ro`/`-admin` variants via the unit's `Also=` directive). The legacy `libvirtd.service` is masked so it can't race the modular daemons — that race is the root cause of broken `ujust setup-virtualization` on stock images.

For tooling that still expects the monolithic `/run/libvirt/libvirt-sock`, `virtproxyd.socket` is enabled. `virtproxyd` is the modular drop-in for that legacy path: it forwards to the per-driver daemons. It and `libvirtd.socket` both declare `Conflicts=` on the same socket path, so only `virtproxyd.socket` is enabled (`libvirtd.socket` would be inert anyway with its service masked).

The default NAT network (shipped by `libvirt-daemon-config-network`) is marked autostart at build time by creating the `autostart/default.xml` symlink that `virsh net-autostart` would — so guests get networking on first boot without manual setup.

### IOMMU + VFIO enabled for GPU passthrough

`intel_iommu=on iommu=pt` are baked in as kernel arguments via a bootc `kargs.d` fragment (`/usr/lib/bootc/kargs.d/00-iommu.toml`), enabling VFIO/PCI passthrough to guests. This uses bootc's native karg mechanism rather than `rpm-ostree kargs`, which can't run during an image build. Target hardware is Intel (ThinkPad P1); `iommu=pt` keeps DMA-remapping overhead off host-only devices.

A second fragment (`/usr/lib/bootc/kargs.d/10-vfio.toml`) adds `rd.driver.pre=vfio-pci vfio_pci.disable_vga=1` to load `vfio-pci` early. These prepare for passthrough but bind nothing on their own — choose a binding strategy and add `vfio-pci.ids=…` (static) or a libvirt hook (dynamic) when ready. The ThinkPad P1's RTX 4070 Max-Q is a muxless Optimus dGPU (render-only, no display outputs), so guest output is viewed via Looking Glass; the `kvmfr` module and its `kvmfr.static_size_mb` default come from the Bazzite base.

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

- Desktop tower (not handheld / Deck)
- NVIDIA RTX 30 / 40 series with **open** kernel modules
- KVM-capable CPU (Intel VT-x or AMD-V)
- Sufficient RAM for KDE Plasma + concurrent VMs

If you have an older NVIDIA card (10/20 series, Maxwell/Pascal), rebase to a proprietary-driver Bazzite variant instead — open modules don't support pre-Turing.

## Repository layout

| Path | Purpose |
|---|---|
| `Containerfile` | Image build definition (`FROM` + `COPY system_files` + invoke `build.sh`) |
| `build_files/build.sh` | All customizations: packages, repos, units, polkit, first-boot oneshot |
| `system_files/` | Static content copied verbatim into the image (systemd units, ujust recipes, bootc kargs) |
| `disk_config/disk.toml` | qcow2/raw config for bootc-image-builder |
| `disk_config/iso-kde.toml` | KDE Plasma ISO config |
| `disk_config/iso-gnome.toml` | GNOME ISO config |
| `.github/workflows/build.yml` | CI: build, push to GHCR, sign with cosign |
| `.github/workflows/build-disk.yml` | CI: produce qcow2 + anaconda-iso artifacts on demand |
| `cosign.pub` | Public key for verifying signed images |
| `Justfile` | Local build/run recipes (see below) |

## Local build & VM testing

Quick path for testing changes before rebasing your real machine:

```bash
just build               # build the container image locally
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

## Setting Up ISO Builds

The [build-disk.yml](./.github/workflows/build-disk.yml) GitHub Actions workflow creates a disk image from your OCI image using the [bootc-image-builder](https://osbuild.org/docs/bootc/). To use this workflow:

1. Modify `disk_config/iso.toml` to point to your custom container image before generating an ISO image.
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
