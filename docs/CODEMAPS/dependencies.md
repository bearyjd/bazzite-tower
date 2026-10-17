<!-- Generated: 2026-08-08 | Files scanned: 6 | Token estimate: ~700 -->
<!-- Targeted update: 2026-10-05 | OpenSnitch/Snitchwatch release boundaries; not a full regeneration -->
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

## OpenSnitch and Snitchwatch

`build_files/build.d/95-firewall.sh` pins and SHA-256 verifies the upstream
OpenSnitch 1.8.0 RPM before extraction. It installs `libnetfilter_queue` and
`nftables` explicitly; extraction neither resolves dependencies nor registers
the daemon in the RPM database. The default user bridge and both GUI profiles remain per-user installs. The opt-in system candidate adds a pinned native bridge and immutable source/toolchain/license provenance. It separately compiles the reviewed upstream OpenSnitch 1.8.0 plus a downstream queue-lifetime/NFT-ownership repair, retaining module locks, generated protocol hashes, Go/protoc/RPM inventory and license payloads. Default legacy builds retain the RPM binary and do not compile either native component.

October 4 VM tests used Snitchwatch `1def745`. October 5 follow-up used base
`2690109` plus reviewed uncommitted changes, source snapshot `851f3a`. Its clean
x86_64 GUI build used pinned KDE/Qt 6.9.3 and Rust 1.89.0, declared protoc 29.3
and mold 2.40.4, and 657 lockfile-verified crate inputs. These test artifacts are
not image-consumed release pins; guest installation reported KDE 6.9 as
end-of-life, so a supported SDK/runtime needs release validation. OpenSnitch's
grpc-go 1.32.0 path still requires
the relative Unix address workaround; default KDE startup, daemon shutdown
and release/image integration remain gated. A fresh candidate GUI release
build at `5c2b44a` now passed on supported KDE 6.11 / Qt 6.11.2 with Rust
1.98.1 and mold 2.42.0; actual default KDE behavior requires fresh VM proof.
The GUI crate remains 0.1.0, separate from native bridge 0.1.1. See [system bridge research](../research/snitchwatch-system-bridge.md).

Snitchwatch daemon build toolchain: 7 Fedora 44 RPMs pinned by sha256 in
`build_files/firewall/snitchwatch-system-daemon-pins.json` and fetched from Koji
(signed copies, key sha256 pinned); see [image-build](image-build.md).
Phase 2 separately pins three signed Koji bridge RPMs (rust, cargo,
rust-std-static 1.98.1-1.fc44) in `build_files/firewall/snitchwatch-system-pins.json`.
The shared toolchain helper runs after the byte-identical upstream installer
and before the Rust gate; the daemon retains its seven pins. Remaining live
toolchain and transitive dependencies, including LLVM, are not fully hermetic.
Re-pin `EXPECTED_PINS_SHA256` in the respective bridge
`snitchwatch-system-verify.py` or daemon `snitchwatch-system-daemon-verify.py`
whenever its pins manifest changes, and rerun relevant tests.

The r15 bridge candidate source is pinned at
`7bf3f1974395a7a55ec3a099e2e16bb5ad255ec4` (tree
`c60578ce519174fdabd2a1b73ce01ce85f5b48b1`, unchanged OpenSnitch gitlink
`b404c4c6316760fa7bc415509d3f8d747f7dc9cc`; r13/r14 used `bf15bb5`). Bridge
packaging input hashes (15, adding upstream's `opensnitch.service` drop-in and
daemon config) and the pins verifier are refreshed from that committed snapshot. Source and
workflow review/runtime acceptance remain publication gates; the candidate pin
confers no stable release authority. See [r13 publication](../r13-publication.md).

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

cosign — `cosign.pub` tracked; private key via mandatory `SIGNING_SECRET` for
default-branch image releases. Images are signed by digest, carry a signed SPDX
installed-RPM inventory attestation (not a whole-filesystem or language-package
SBOM) and GitHub provenance. The image merges a repository-scoped
`sigstoreSigned` policy plus a sigstore-attachment registry config. The global
policy default is `reject`; only unrelated Docker/Podman pulls retain an
empty-scope Docker compatibility fallback. Bootstrap a host's first verified
switch with `scripts/install-signature-policy.sh` and
`bootc switch --enforce-container-sigpolicy`.
`matchRepository` accepts any digest signed by this repository key, so it does
not prevent a signed rollback: rollback-sensitive installs should switch to a
reviewed immutable `@sha256:…` reference rather than a mutable tag.

## GitHub Actions (SHA-pinned, renovate-managed)

`actions/checkout`, `ublue-os/remove-unwanted-software`,
`redhat-actions/buildah-build` + `push-to-registry`, `docker/metadata-action` +
`login-action`, `sigstore/cosign-installer`,
`actions/attest-build-provenance`, `actions/github-script`,
`osbuild/bootc-image-builder-action`, `actions/upload-artifact`,
`ublue-os/titanoboa` (live-ISO build; pinned to `main`).

## Local-dev tooling

`just`, `podman`, bootc-image-builder (digest-pinned
`quay.io/centos-bootc/bootc-image-builder`),
qemu (run-vm), `python3` (base-diff.py), `shellcheck`/`shfmt` (`just lint`/`format`).

## ISO build (live/installer)

Extra **build-time** packages the `installer/` payload pulls (not in the OS image):
`dracut-live`, `livesys-scripts`, `anaconda-live`, `grub2-efi-x64-cdboot`,
`xorriso`, `isomd5sum`, and a **stock Fedora-signed `kernel`** (Secure Boot). See
[iso-build.md](iso-build.md).

## Automation

`renovate.json` — pins/updates Actions + base. No application dependency manifest
(this is not an app).
