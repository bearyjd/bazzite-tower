<!-- Generated: 2026-06-14 | Files scanned: 6 | Token estimate: ~640 -->
# Dependencies & External Surfaces

## Base image

`ghcr.io/ublue-os/bazzite-nvidia:stable` — Bazzite KDE + proprietary NVIDIA, F44+.
Swap the Containerfile `FROM` → `bazzite-nvidia-open:stable` for the open modules.
Base provides (relied on, not installed here): the `kvmfr` Looking Glass module
(`kvmfr` + `kmod-kvmfr`, hikariknight COPR), tailscale, distrobox, most of Cockpit.

## Packages layered by build.sh

virt stack (qemu-kvm, libvirt*, virt-*, edk2-ovmf, guestfs-tools, spice-gtk3),
Docker CE, dev tooling (android-tools, ccache, flatpak-builder, podman-machine/tui,
rclone, restic, zsh), and the hardware/health additions: **smartmontools**,
**cockpit + cockpit-machines**, **rasdaemon**, **microcode_ctl**, **thermald**.

## Package repos

| Repo | State | Use |
|---|---|---|
| Fedora + ublue/bazzite COPRs (base) | enabled | virt stack, dev tooling, health pkgs |
| `docker-ce` (download.docker.com) | `enabled=0` on disk | build-time only via `--enablerepo`; inert at runtime |
| `pgaskin/looking-glass-client` (COPR) | **runtime only** | enabled inside a distrobox by `ujust install-looking-glass-client`; never touches the host image |

## Registries / external services

- **GHCR** — image publish + pull (the rebase target)
- **download.docker.com** — build-time only (Docker CE + gpg key)
- **S3** (rclone, optional) — disk/ISO upload (`S3_*` secrets)
- **ArtifactHub** — image metadata/labels

## Signing

cosign — `cosign.pub` tracked; private key via `SIGNING_SECRET` (CI) or `cosign.key`
(local, gitignored). Image signed by digest.

## GitHub Actions (SHA-pinned, renovate-managed)

`actions/checkout`, `ublue-os/remove-unwanted-software`,
`redhat-actions/buildah-build` + `push-to-registry`, `docker/metadata-action` +
`login-action`, `sigstore/cosign-installer`, `actions/github-script`,
`osbuild/bootc-image-builder-action`, `actions/upload-artifact`,
`ublue-os/titanoboa` (live-ISO build; pinned to `main`).

## Local-dev tooling

`just`, `podman`, bootc-image-builder (`quay.io/centos-bootc/bootc-image-builder`),
qemu (run-vm), `python3` (base-diff.py), `shellcheck`/`shfmt` (`just lint`/`format`).

## ISO build (live/installer)

Extra **build-time** packages the `installer/` payload pulls (not in the OS image):
`dracut-live`, `livesys-scripts`, `anaconda-live`, `grub2-efi-x64-cdboot`,
`xorriso`, `isomd5sum`, and a **stock Fedora-signed `kernel`** (Secure Boot). See
[iso-build.md](iso-build.md).

## Automation

`renovate.json` — pins/updates Actions + base. No application dependency manifest
(this is not an app).
