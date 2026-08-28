<!-- Generated: 2026-08-08 | Files scanned: 6 | Token estimate: ~700 -->
# Dependencies & External Surfaces

## Base image

`ghcr.io/ublue-os/bazzite-nvidia-open`, **default pinned to the dated tag
`44.20260825`** (kernel `7.2.0-ogc6.1`) — Bazzite KDE + NVIDIA open kernel
modules, F44+ — not `:stable`, for reproducibility. Carries the i915 Meteor
Lake s2idle-resume fix; proprietary `bazzite-nvidia` was used until
2026-08-28 but forked onto a kernel track that won't get it (see
`docs/research/i915-bug-report/UPSTREAM-FIX-STATUS-2026-08-28.md`).
`:stable` is still built as the opt-in `:latest-kernel` tag. Swap the
Containerfile `FROM` → `bazzite-nvidia` for the proprietary driver. Base
provides (relied on, not installed here): the
`kvmfr` Looking Glass module (`kvmfr` + `kmod-kvmfr`, hikariknight COPR),
tailscale, distrobox, most of Cockpit, the KDE Plasma desktop (`kwin`,
`kscreenlocker`, `libplasma`, ...).

## Packages layered by build.sh

virt stack (qemu-kvm, libvirt*, virt-*, edk2-ovmf, guestfs-tools, spice-gtk3),
Docker CE, dev tooling (android-tools, ccache, flatpak-builder, podman-machine/tui,
rclone, restic, zsh), the hardware/health additions (**smartmontools**,
**cockpit + cockpit-machines**, **rasdaemon**, **microcode_ctl**, **thermald**),
and the firewall selector (default **OpenSnitch** v1.8.0, pinned RPM extraction +
Snitchwatch config; disabled **Portmaster** VM spike behind
`FIREWALL_DAEMON=portmaster`, see `docs/research/portmaster-bootc-spike.md`).

## KDE Plasma package-family pin

`05-pin-kde-packages.sh` (runs first, before any other `dnf install` in
`build.d/`) appends an `exclude=` line to `/etc/dnf/dnf.conf`'s `[main]` section
(guarded — refuses to run if `[main]` isn't found, rather than appending blind)
for `kwin`/`kwin-libs`/`kwin-common`/`libplasma`/`kscreenlocker`/
`plasma-workspace*`/`plasma-desktop`/`kdecoration`/`kf6-kwindowsystem`, so none
of this build's own dnf transactions can touch them. Not a `dnf.conf.d/`
drop-in — this base runs **dnf5**, whose real drop-in directory is
`/etc/dnf/libdnf5.conf.d/`, not `/etc/dnf/dnf.conf.d/`; the first version of
this fix used the latter, silently did nothing, and was caught by CI's own
smoke gate (see the "Correction" note in
`docs/research/kwin-screenlocker-abi-2026-08-08/REPORT.md`). Root cause it
guards against: `dnf install` for an unrelated package can still resolve a
newer `kwin`/`libplasma` against live Fedora/COPR repo metadata at build time
without a matching `kscreenlocker` build existing yet, producing an
undefined-symbol crash in `kwin_wayland` at login (black screen, no login
prompt) — see `docs/research/kwin-screenlocker-abi-2026-07-26/` and
`-2026-08-08/`. Verified by two `tests/smoke.sh` checks: `kwin`/`kscreenlocker`
share the same major.minor, and the `exclude=` line is actually present in
`/etc/dnf/dnf.conf`.

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
