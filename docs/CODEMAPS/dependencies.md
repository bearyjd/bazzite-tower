<!-- Generated: 2026-06-10 | Files scanned: 6 | Token estimate: ~580 -->
# Dependencies & External Surfaces

## Base image

`ghcr.io/ublue-os/bazzite-nvidia:stable` — Bazzite KDE + proprietary NVIDIA, F44+.
Swap the Containerfile `FROM` → `bazzite-nvidia-open:stable` for the open modules.

## Package repos

| Repo | State | Use |
|---|---|---|
| Fedora (base) | enabled | virt stack, dev tooling |
| `docker-ce` (download.docker.com) | `enabled=0` on disk | install-time only via `--enablerepo=docker-ce-stable`; inert at runtime |

## Registries / external services

- **GHCR** — image publish + pull (the rebase target)
- **download.docker.com** — build-time only (Docker CE packages + gpg key)
- **S3** (rclone, optional) — disk-image upload in `build-disk.yml` (`S3_*` secrets)
- **ArtifactHub** — image metadata/labels (`artifacthub-repo.yml`, labels in build.yml)

## Signing

cosign — `cosign.pub` is tracked; private key via `SIGNING_SECRET` (CI) or
`cosign.key` (local, gitignored). Image is signed by digest.

## GitHub Actions (SHA-pinned, renovate-managed)

`actions/checkout`, `ublue-os/remove-unwanted-software`,
`redhat-actions/buildah-build` + `push-to-registry`, `docker/metadata-action` +
`login-action`, `sigstore/cosign-installer`, `actions/github-script`,
`osbuild/bootc-image-builder-action`, `actions/upload-artifact`,
`ublue-os/titanoboa` (live-ISO build; pinned to `main` — mid-revamp, no release tag).

## Local-dev tooling

`just`, `podman`, bootc-image-builder (`quay.io/centos-bootc/bootc-image-builder`),
qemu (run-vm), `python3` (base-diff.py), `shellcheck`/`shfmt` (`just lint`/`format`).

## ISO build (live/installer)

Extra **build-time** packages the `installer/` payload pulls (not in the OS image):
`dracut-live`, `livesys-scripts`, `anaconda-live`, `grub2-efi-x64-cdboot`,
`xorriso`, `isomd5sum`, and a **stock Fedora-signed `kernel`** (swapped in for the
ublue kernel so the ISO boots under Secure Boot). See [iso-build.md](iso-build.md).

## Automation

`renovate.json` — pins/updates Actions + base. No application dependency manifest
(this is not an app).
